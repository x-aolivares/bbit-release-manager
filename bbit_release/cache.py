"""Caché persistente normalizada en SQLite.

Reemplaza el antiguo esquema plano (``repo_cache``, ``scan_cache``, ...) por un
modelo de 4 tablas normalizadas:

- ``init_sesion``      — cada consulta del front registra una sesión con su
                         configuración dinámica (``is_details`` JSON).
- ``service_provider`` — catálogo de proveedores de servicios externos.
- ``request_type``     — catálogo de servicios (endpoint + TTL en segundos).
- ``request``          — histórico de ejecuciones con estado y payload.

TTL: un ``request`` SUCCESS vale mientras ``now - rq_created_at <=
rt_ttl_seconds`` del ``request_type``; pasado ese lapso se trata como miss.

Thread-safety: el backend corre consultas en paralelo (ThreadPoolExecutor),
así que todas las operaciones a la DB se serializan con un ``threading.Lock``
y cada statement usa un cursor fresco.  ``check_same_thread=False`` permite
reutilizar la conexión entre hilos.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from pathlib import Path

log = logging.getLogger("bbit.cache")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_DB = _PROJECT_ROOT / "data" / "cache.db"
_BITBUCKET_BASE = "https://api.bitbucket.org/2.0"

# Catálogo inicial de proveedores.
_SEED_PROVIDERS = {
    "Bitbucket": {"base_url": _BITBUCKET_BASE},
    "CircleCi": {"vcs": "bb"},
}

# name -> (provider, ttl_seconds, service_url, details)
_SEED_REQUEST_TYPES = {
    "get_user_repositories": (
        "Bitbucket", 300,
        f"{_BITBUCKET_BASE}/repositories/{{workspace}}",
        {"method": "GET"},
    ),
    "get_branch_repositories": (
        "Bitbucket", 300,
        f"{_BITBUCKET_BASE}/repositories/{{workspace}}/{{repo}}/refs/branches/{{branch}}",
        {"method": "GET"},
    ),
    "scan_release": (
        "Bitbucket", 1800,
        f"{_BITBUCKET_BASE}/repositories/{{workspace}}/{{repo}}/commits/{{branch}}",
        {"method": "GET"},
    ),
    "diff_ssm": (
        "Bitbucket", 1800,
        f"{_BITBUCKET_BASE}/repositories/{{workspace}}/{{repo}}/diff/{{to}}?from={{from}}",
        {"method": "GET"},
    ),
    "get_master_params": (
        "Bitbucket", 3600,
        f"{_BITBUCKET_BASE}/repositories/{{workspace}}/{{repo}}/src/{{ref}}",
        {"method": "GET"},
    ),
}

_LEGACY_TABLES = ("repo_cache", "scan_cache", "diff_cache", "master_cache", "branch_repos")


def _repo_details(
    prefixes: list[str] | None = None,
    exclude: set[str] | None = None,
    deploy_prefixes: list[str] | None = None,
    ssm_prefixes: list[str] | None = None,
    bypass_cache: bool = False,
) -> dict:
    """Bloque ``is_details`` de una sesión de consulta típica del front."""
    return {
        "bypass_cache": bool(bypass_cache),
        "repositories": {
            "excluded": sorted(exclude or []),
            "prefixes": sorted(prefixes or []),
        },
        "deployment_environments": sorted(deploy_prefixes or []),
        "ssm": {"mode": "DIFF", "prefixes": sorted(ssm_prefixes or [])},
    }


class ReleaseCache:
    """Caché persistente SQLite normalizada y thread-safe."""

    def __init__(self, db_path: Path | None = None):
        self._db_path = db_path or _DEFAULT_DB
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._create_tables()
            self._drop_legacy_tables()
            self._seed_catalog()

    # -- schema ---------------------------------------------------------------

    def _create_tables(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS init_sesion (
                is_id INTEGER PRIMARY KEY AUTOINCREMENT,
                is_source TEXT NOT NULL,
                is_target TEXT NOT NULL,
                is_details TEXT NOT NULL,
                is_created_at REAL NOT NULL,
                is_updated_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS uuidx_is_source_is_target
                ON init_sesion (is_source, is_target);

            CREATE TABLE IF NOT EXISTS service_provider (
                sp_id INTEGER PRIMARY KEY AUTOINCREMENT,
                sp_name TEXT NOT NULL UNIQUE,
                sp_details TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS request_type (
                rt_id INTEGER PRIMARY KEY AUTOINCREMENT,
                rt_service_provider_id INTEGER NOT NULL
                    REFERENCES service_provider(sp_id),
                rt_name TEXT NOT NULL UNIQUE,
                rt_ttl_seconds INTEGER NOT NULL DEFAULT 300,
                rt_service_url TEXT NOT NULL,
                rt_details TEXT NOT NULL,
                rt_created_at REAL NOT NULL,
                rt_updated_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_rt_service_provider
                ON request_type (rt_service_provider_id);

            CREATE TABLE IF NOT EXISTS request (
                rq_id INTEGER PRIMARY KEY AUTOINCREMENT,
                is_id INTEGER NOT NULL REFERENCES init_sesion(is_id),
                rt_id INTEGER NOT NULL REFERENCES request_type(rt_id),
                rq_status TEXT NOT NULL DEFAULT 'PENDING',
                rq_details TEXT,
                rq_created_at REAL NOT NULL,
                rq_updated_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_rq_type_created
                ON request (rt_id, rq_created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_rq_init_sesion
                ON request (is_id);

            CREATE TABLE IF NOT EXISTS service_call (
                sc_id INTEGER PRIMARY KEY AUTOINCREMENT,
                sc_source TEXT NOT NULL,
                sc_method TEXT NOT NULL,
                sc_url TEXT,
                sc_params TEXT,
                sc_status INTEGER NOT NULL,
                sc_duration_ms REAL NOT NULL,
                sc_response TEXT,
                sc_created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_sc_source_created
                ON service_call (sc_source, sc_created_at);
            """
        )
        self._conn.commit()

    def _drop_legacy_tables(self) -> None:
        """Elimina las tablas del esquema plano anterior (si existen)."""
        for table in _LEGACY_TABLES:
            self._conn.execute(f"DROP TABLE IF EXISTS {table}")
        self._conn.commit()

    def _seed_catalog(self) -> None:
        now = time.time()
        for name, details in _SEED_PROVIDERS.items():
            self._conn.execute(
                "INSERT OR IGNORE INTO service_provider (sp_name, sp_details) VALUES (?, ?)",
                (name, json.dumps(details)),
            )
        self._conn.commit()

        for name, (provider, ttl, url, details) in _SEED_REQUEST_TYPES.items():
            row = self._conn.execute(
                "SELECT sp_id FROM service_provider WHERE sp_name = ?", (provider,)
            ).fetchone()
            if row is None:
                continue
            self._conn.execute(
                "INSERT OR IGNORE INTO request_type "
                "(rt_service_provider_id, rt_name, rt_ttl_seconds, rt_service_url, "
                " rt_details, rt_created_at, rt_updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (row[0], name, ttl, url, json.dumps(details), now, now),
            )
        self._conn.commit()

    # -- helpers ---------------------------------------------------------------

    def _fetchone(self, sql: str, params: tuple) -> tuple | None:
        with self._lock:
            cur = self._conn.cursor()
            try:
                cur.execute(sql, params)
                return cur.fetchone()
            finally:
                cur.close()

    def _fetchall(self, sql: str, params: tuple = ()) -> list[tuple]:
        with self._lock:
            cur = self._conn.cursor()
            try:
                cur.execute(sql, params)
                return cur.fetchall()
            finally:
                cur.close()

    def _execute(self, sql: str, params: tuple) -> None:
        with self._lock:
            cur = self._conn.cursor()
            try:
                cur.execute(sql, params)
            finally:
                cur.close()
            self._conn.commit()

    def _insert(self, sql: str, params: tuple) -> int:
        with self._lock:
            cur = self._conn.cursor()
            try:
                cur.execute(sql, params)
                lastrowid = cur.lastrowid
            finally:
                cur.close()
            self._conn.commit()
        return lastrowid

    def _session_fingerprint(self, **parts) -> str:
        """Config completa de la sesión como JSON canónico (clave de dedupe)."""
        return json.dumps(parts, sort_keys=True)

    def get_provider(self, name: str) -> dict | None:
        row = self._fetchone(
            "SELECT sp_id, sp_name, sp_details FROM service_provider WHERE sp_name = ?",
            (name,),
        )
        if row is None:
            return None
        return {"id": row[0], "name": row[1], "details": json.loads(row[2])}

    def get_rt(self, name: str) -> dict | None:
        row = self._fetchone(
            "SELECT rt_id, rt_name, rt_ttl_seconds, rt_service_url, rt_details "
            "FROM request_type WHERE rt_name = ?",
            (name,),
        )
        if row is None:
            return None
        return {
            "id": row[0],
            "name": row[1],
            "ttl_seconds": row[2],
            "service_url": row[3],
            "details": json.loads(row[4]),
        }

    # -- sesiones --------------------------------------------------------------

    def create_session(self, source: str, target: str, details: dict) -> int:
        """Inserta una nueva ``init_sesion`` y devuelve su ``is_id``."""
        now = time.time()
        fp = self._session_fingerprint(
            source_branch=source,
            target_branch=target,
            config=details,
        )
        return self._insert(
            "INSERT INTO init_sesion "
            "(is_source, is_target, is_details, is_created_at, is_updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (source, target, fp, now, now),
        )

    def find_session(self, source: str, target: str, details: dict) -> int:
        """Reusa la sesión existente si la config coincide; si no, crea una nueva."""
        fp = self._session_fingerprint(
            source_branch=source,
            target_branch=target,
            config=details,
        )
        row = self._fetchone(
            "SELECT is_id FROM init_sesion "
            "WHERE is_source = ? AND is_target = ? AND is_details = ? "
            "ORDER BY is_updated_at DESC LIMIT 1",
            (source, target, fp),
        )
        if row is not None:
            now = time.time()
            self._execute(
                "UPDATE init_sesion SET is_updated_at = ? WHERE is_id = ?",
                (now, row[0]),
            )
            return row[0]
        return self.create_session(source, target, details)

    def get_session(self, session_id: int) -> dict | None:
        row = self._fetchone(
            "SELECT is_id, is_source, is_target, is_details, is_created_at, is_updated_at "
            "FROM init_sesion WHERE is_id = ?",
            (session_id,),
        )
        if row is None:
            return None
        return {
            "id": row[0],
            "source": row[1],
            "target": row[2],
            "details": row[3],
            "created_at": row[4],
            "updated_at": row[5],
        }

    # -- requests ---------------------------------------------------------------

    def add_request(
        self,
        session_id: int,
        request_type_id: int,
        payload,
        status: str = "SUCCESS",
    ) -> int:
        """Registra una ejecución en ``request`` y devuelve su ``rq_id``."""
        now = time.time()
        return self._insert(
            "INSERT INTO request "
            "(is_id, rt_id, rq_status, rq_details, rq_created_at, rq_updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, request_type_id, status, json.dumps(payload), now, now),
        )

    def record_service_call(
        self,
        *,
        source: str,
        method: str,
        url: str,
        params: dict | None,
        status: int,
        duration_ms: float,
        response: str,
    ) -> int:
        """Registra una llamada HTTP externa cruda en ``service_call``.

        Es una auditoría append-only para reprocesar/debuguear: guarda el raw
        tal cual lo respondió el servicio, sin transformar.
        """
        now = time.time()
        return self._insert(
            "INSERT INTO service_call "
            "(sc_source, sc_method, sc_url, sc_params, sc_status, sc_duration_ms, "
            " sc_response, sc_created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                source,
                method,
                url,
                json.dumps(params) if params is not None else None,
                status,
                round(duration_ms, 3),
                response,
                now,
            ),
        )

    def is_expired(self, created_at: float, ttl_seconds: int) -> bool:
        if ttl_seconds <= 0:
            return False
        return (time.time() - created_at) > ttl_seconds

    def latest_success(self, rt_id: int, session_id: int) -> dict | None:
        """Último request SUCCESS no expirado para un tipo/sesión.

        Devuelve el payload decodificado con su ``created_at`` si vale el TTL;
        ``None`` si no hay hit (miss).
        """
        rt = self._fetchone(
            "SELECT rt_ttl_seconds FROM request_type WHERE rt_id = ?", (rt_id,)
        )
        if rt is None:
            return None
        row = self._fetchone(
            "SELECT rq_details, rq_created_at, rq_status FROM request "
            "WHERE rt_id = ? AND is_id = ? AND rq_status = 'SUCCESS' "
            "ORDER BY rq_created_at DESC, rq_id DESC LIMIT 1",
            (rt_id, session_id),
        )
        if row is None or self.is_expired(row[1], rt[0]):
            return None
        return {"payload": json.loads(row[0]), "created_at": row[1]}

    # -- invalidación -----------------------------------------------------------

    def invalidate(self, source: str, target: str, details: dict) -> None:
        """Borra sessions y requests cuya config base coincide.

        Se compara ``is_source``/``is_target`` y el bloque ``repositories``
        (excluded/prefixes) del ``is_details`` (se ignoran los extras tipo
        deploy/ssm para que un force de scan limpie tambien el diff de la
        misma pareja de ramas).
        """
        base = details.get("repositories", {})
        wanted_excluded = set(base.get("excluded", []) or [])
        wanted_prefixes = set(base.get("prefixes", []) or [])
        rows = self._fetchall(
            "SELECT is_id, is_details FROM init_sesion "
            "WHERE is_source = ? AND is_target = ?",
            (source, target),
        )
        matched_ids = []
        for is_id, fp in rows:
            try:
                parsed = json.loads(fp)
            except (ValueError, TypeError):
                continue
            repos = parsed.get("config", {}).get("repositories", {})
            if set(repos.get("excluded", []) or []) == wanted_excluded and \
               set(repos.get("prefixes", []) or []) == wanted_prefixes:
                matched_ids.append(is_id)
        for is_id in matched_ids:
            self._execute("DELETE FROM request WHERE is_id = ?", (is_id,))
            self._execute("DELETE FROM init_sesion WHERE is_id = ?", (is_id,))
        log.info(
            "cache invalidate %s->%s: %d sesion(es) y sus requests borrados de SQLite",
            source, target, len(matched_ids),
        )

    def invalidate_all(self) -> None:
        counts: dict[str, int] = {}
        with self._lock:
            for table in ("request", "init_sesion"):
                cur = self._conn.cursor()
                try:
                    cur.execute(f"DELETE FROM {table}")
                    counts[table] = cur.rowcount
                finally:
                    cur.close()
            self._conn.commit()
        log.info(
            "cache invalidate_all: %d request(s), %d sesion(es) borrados de SQLite",
            counts.get("request", 0),
            counts.get("init_sesion", 0),
        )

    def clear_all(self) -> dict:
        """Vacía las tablas de datos del cache, logueando cuántos registros se
        eliminaron de cada una. Devuelve el desglose por tabla."""
        counts: dict[str, int] = {}
        with self._lock:
            for table in ("request", "init_sesion", "service_call"):
                cur = self._conn.cursor()
                try:
                    cur.execute(f"DELETE FROM {table}")
                    counts[table] = cur.rowcount
                finally:
                    cur.close()
            self._conn.commit()
        log.info(
            "cache clear_all: %d request(s), %d sesion(es), %d service_call(s) borrados de SQLite",
            counts.get("request", 0),
            counts.get("init_sesion", 0),
            counts.get("service_call", 0),
        )
        return counts

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- facade de alto nivel (compat con web/api/repos.py) ----------------------

    def _get_cached(self, rt_name: str, source: str, target: str, details: dict):
        """Hit: payload del último SUCCESS de un tipo/sesión; miss: None."""
        rt = self.get_rt(rt_name)
        if rt is None:
            log.warning("cache sin request_type '%s': consulta servicio externo", rt_name)
            return None
        session_id = self.find_session(source, target, details)
        hit = self.latest_success(rt["id"], session_id)
        if hit is None:
            log.info(
                "cache miss %s: %s->%s -> consulta servicio externo",
                rt_name, source, target,
            )
            return None
        ttl = rt["ttl_seconds"]
        log.info(
            "cache hit %s: %s->%s desde SQLite (is_id=%d, creado hace %.0fs)",
            rt_name, source, target, session_id,
            max(0.0, time.time() - hit["created_at"]),
        )
        if ttl:
            remaining = ttl - (time.time() - hit["created_at"])
            log.debug("cache hit: quedan %.0fs de TTL (%ds)", max(0.0, remaining), ttl)
        return hit["payload"]

    def _set_cached(self, rt_name: str, source: str, target: str, details: dict, payload) -> None:
        rt = self.get_rt(rt_name)
        if rt is None:
            log.warning("cache sin request_type '%s': no persisto respuesta", rt_name)
            return
        session_id = self.find_session(source, target, details)
        rq_id = self._insert(
            "INSERT INTO request "
            "(is_id, rt_id, rq_status, rq_details, rq_created_at, rq_updated_at) "
            "VALUES (?, ?, 'SUCCESS', ?, ?, ?)",
            (session_id, rt["id"], json.dumps(payload), time.time(), time.time()),
        )
        log.info(
            "cache store %s: %s->%s guardado en SQLite (is_id=%d, rq_id=%d)",
            rt_name, source, target, session_id, rq_id,
        )

    def get_repos(self, origin: str, destination: str, prefixes: list[str] | None, exclude: set[str] | None):
        return self._get_cached(
            "get_user_repositories", origin, destination,
            _repo_details(prefixes, exclude),
        )

    def set_repos(self, origin: str, destination: str, prefixes: list[str] | None, exclude: set[str] | None, repos: list[dict]) -> None:
        self._set_cached(
            "get_user_repositories", origin, destination,
            _repo_details(prefixes, exclude), repos,
        )

    def get_branch_repos(self, origin: str, destination: str, prefixes: list[str] | None, exclude: set[str] | None):
        return self._get_cached(
            "get_branch_repositories", origin, destination,
            _repo_details(prefixes, exclude),
        )

    def set_branch_repos(self, origin: str, destination: str, prefixes: list[str] | None, exclude: set[str] | None, repos: list[dict]) -> None:
        self._set_cached(
            "get_branch_repositories", origin, destination,
            _repo_details(prefixes, exclude), repos,
        )

    def get_scan(self, origin: str, destination: str, project_prefixes: list[str] | None, exclude: set[str] | None, deploy_prefixes: list[str] | None = None):
        return self._get_cached(
            "scan_release", origin, destination,
            _repo_details(project_prefixes, exclude, deploy_prefixes),
        )

    def set_scan(self, origin: str, destination: str, project_prefixes: list[str] | None, exclude: set[str] | None, data: dict, deploy_prefixes: list[str] | None = None) -> None:
        self._set_cached(
            "scan_release", origin, destination,
            _repo_details(project_prefixes, exclude, deploy_prefixes), data,
        )

    def get_diff(self, origin: str, destination: str, prefixes: list[str] | None, exclude: set[str] | None, ssm_prefixes: list[str] | None = None):
        return self._get_cached(
            "diff_ssm", origin, destination,
            _repo_details(prefixes, exclude, ssm_prefixes=ssm_prefixes),
        )

    def set_diff(self, origin: str, destination: str, prefixes: list[str] | None, exclude: set[str] | None, data: dict, ssm_prefixes: list[str] | None = None) -> None:
        self._set_cached(
            "diff_ssm", origin, destination,
            _repo_details(prefixes, exclude, ssm_prefixes=ssm_prefixes), data,
        )

    def get_master(self, slug: str, destination: str) -> set | None:
        details = _repo_details(bypass_cache=False)
        params = self._get_cached("get_master_params", slug, destination, details)
        if params is None:
            return None
        return set(tuple(p) for p in params)

    def set_master(self, slug: str, destination: str, params: set) -> None:
        details = _repo_details(bypass_cache=False)
        self._set_cached("get_master_params", slug, destination, details, list(params))


_global_cache: ReleaseCache | None = None


def get_cache(db_path: Path | None = None) -> ReleaseCache:
    global _global_cache
    if _global_cache is None:
        _global_cache = ReleaseCache(db_path)
    return _global_cache


def reset_cache() -> None:
    """Cierra y limpia la referencia global (para tests)."""
    global _global_cache
    if _global_cache is not None:
        _global_cache.close()
        _global_cache = None