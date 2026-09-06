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
import uuid
from pathlib import Path

log = logging.getLogger("bbit.cache")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_DB = _PROJECT_ROOT / "data" / "cache.db"
_BITBUCKET_BASE = "https://api.bitbucket.org/2.0"
_CIRCLECI_BASE = "https://circleci.com/api/v2"

# Fila canónica de conexión: credenciales por servicio + settings, sin env.*
CONN_SOURCE = "config"
CONN_TARGET = "connection"

# Alias del cliente local por defecto (multi-usuario en VPS se resuelve por alias).
DEFAULT_CLIENT_ALIAS = "local"

# Catálogo inicial de proveedores.
_SEED_PROVIDERS = {
    "Bitbucket": {"base_url": _BITBUCKET_BASE},
    "CircleCi": {"vcs": "bb"},
    "AWS": {
        "regions": [
            "us-east-1",
            "us-east-2",
            "us-west-1",
            "us-west-2",
            "eu-west-1",
            "eu-central-1",
            "sa-east-1",
        ]
    },
}

# name -> (provider, ttl_seconds, service_url, details)
_SEED_REQUEST_TYPES = {
    "get_user_repositories": (
        "Bitbucket", 1800,
        f"{_BITBUCKET_BASE}/repositories/{{workspace}}",
        {"method": "GET"},
    ),
    "get_branch_repositories": (
        "Bitbucket", 3600,
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
    "circleci_project": (
        "CircleCi", 3600,
        f"{_CIRCLECI_BASE}/project/{{slug}}",
        {"method": "GET"},
    ),
    "circleci_pipelines": (
        "CircleCi", 120,
        f"{_CIRCLECI_BASE}/project/{{slug}}/pipeline",
        {"method": "GET"},
    ),
    "circleci_workflows": (
        "CircleCi", 60,
        f"{_CIRCLECI_BASE}/pipeline/{{pipeline_id}}/workflow",
        {"method": "GET"},
    ),
    "circleci_workflow_jobs": (
        "CircleCi", 60,
        f"{_CIRCLECI_BASE}/workflow/{{workflow_id}}/job",
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
            self._migrate_schema()
            self._drop_legacy_tables()
            self._seed_catalog()

    @property
    def db_path(self) -> Path:
        return self._db_path

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

            CREATE TABLE IF NOT EXISTS client (
                c_id TEXT PRIMARY KEY,              -- uuid, no adivinable
                c_alias TEXT NOT NULL UNIQUE,
                c_details TEXT NOT NULL,            -- JSON flexible
                c_created_at REAL NOT NULL,
                c_updated_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS service_authentication (
                sa_id INTEGER PRIMARY KEY AUTOINCREMENT,
                c_id TEXT NOT NULL,                 -- = client.c_id (uuid)
                sp_id INTEGER NOT NULL,             -- = service_provider.sp_id
                sa_details TEXT NOT NULL,           -- JSON {"env": {...}, "expires_at"?: epoch}
                sa_created_at REAL NOT NULL,
                sa_updated_at REAL NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS uuidx_sa_c_id_sp_id
                ON service_authentication (c_id, sp_id);

            CREATE TABLE IF NOT EXISTS request_type (
                rt_id INTEGER PRIMARY KEY AUTOINCREMENT,
                sp_id INTEGER NOT NULL,
                rt_name TEXT NOT NULL UNIQUE,
                rt_ttl_seconds INTEGER NOT NULL DEFAULT 300,
                rt_service_url TEXT NOT NULL,
                rt_details TEXT NOT NULL,
                rt_created_at REAL NOT NULL,
                rt_updated_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS request (
                rq_id INTEGER PRIMARY KEY AUTOINCREMENT,
                is_id INTEGER NOT NULL,
                rt_id INTEGER NOT NULL,
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

            CREATE TABLE IF NOT EXISTS ssm_value (
                sv_id INTEGER PRIMARY KEY AUTOINCREMENT,
                c_id TEXT NOT NULL,                -- = client.c_id
                sv_path TEXT NOT NULL,
                sv_decrypt INTEGER NOT NULL DEFAULT 0,
                sv_value TEXT NOT NULL,
                sv_encrypted INTEGER NOT NULL DEFAULT 0,
                sv_created_at REAL NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS uuidx_ssm_c_path_decrypt
                ON ssm_value (c_id, sv_path, sv_decrypt);
            """
        )
        self._conn.commit()

    def _migrate_schema(self) -> None:
        """Ajustes de esquema para bases existentes (idempotente).

        - ``request_type.rt_service_provider_id`` -> ``sp_id`` (estándar:
          la columna de referencia usa el nombre de la PK que referencia).
          SQLite renombra también los índices que apuntaban a esa columna.
        - ``service_authentication`` -> UNIQUE index en ``(c_id, sp_id)``
          (dedupe previo de filas repetidas que hayan quedado de antes).
        """
        with self._lock:
            cur = self._conn.cursor()
            try:
                cols = {row[1] for row in cur.execute("PRAGMA table_info(request_type)").fetchall()}
            finally:
                cur.close()
            if "rt_service_provider_id" in cols and "sp_id" not in cols:
                cur = self._conn.cursor()
                try:
                    cur.execute(
                        "ALTER TABLE request_type RENAME COLUMN rt_service_provider_id TO sp_id"
                    )
                finally:
                    cur.close()
            cur = self._conn.cursor()
            try:
                cur.execute("DROP INDEX IF EXISTS idx_rt_service_provider")
            finally:
                cur.close()
            cur = self._conn.cursor()
            try:
                cur.execute("CREATE INDEX IF NOT EXISTS idx_rt_sp_id ON request_type (sp_id)")
            finally:
                cur.close()

            cur = self._conn.cursor()
            try:
                cur.execute(
                    "DELETE FROM service_authentication "
                    "WHERE sa_id NOT IN ("
                    "  SELECT MAX(sa_id) FROM service_authentication GROUP BY c_id, sp_id)"
                )
            finally:
                cur.close()
            cur = self._conn.cursor()
            try:
                cur.execute("DROP INDEX IF EXISTS uuidx_sa_c_id_sp_id")
            finally:
                cur.close()
            cur = self._conn.cursor()
            try:
                cur.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uuidx_sa_c_id_sp_id "
                    "ON service_authentication (c_id, sp_id)"
                )
            finally:
                cur.close()
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
                "INSERT INTO service_provider (sp_name, sp_details) VALUES (?, ?) "
                "ON CONFLICT(sp_name) DO UPDATE SET "
                " sp_details = excluded.sp_details",
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
                "INSERT INTO request_type "
                "(sp_id, rt_name, rt_ttl_seconds, rt_service_url, "
                " rt_details, rt_created_at, rt_updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(rt_name) DO UPDATE SET "
                " rt_ttl_seconds = excluded.rt_ttl_seconds, "
                " rt_service_url = excluded.rt_service_url, "
                " rt_details = excluded.rt_details, "
                " rt_updated_at = excluded.rt_updated_at",
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

    # -- clientes y autenticación ---------------------------------------------

    def get_client(self, c_id: str) -> dict | None:
        row = self._fetchone(
            "SELECT c_id, c_alias, c_details, c_created_at, c_updated_at "
            "FROM client WHERE c_id = ?",
            (c_id,),
        )
        if row is None:
            return None
        return {
            "id": row[0],
            "alias": row[1],
            "details": json.loads(row[2]) if row[2] else {},
            "created_at": row[3],
            "updated_at": row[4],
        }

    def get_client_by_alias(self, alias: str) -> dict | None:
        row = self._fetchone(
            "SELECT c_id, c_alias, c_details FROM client WHERE c_alias = ?",
            (alias,),
        )
        if row is None:
            return None
        return {"id": row[0], "alias": row[1], "details": json.loads(row[2]) if row[2] else {}}

    def get_or_create_client(self, alias: str) -> dict:
        """Devuelve el cliente con ese alias; si no existe, lo crea (uuid)."""
        client = self.get_client_by_alias(alias)
        if client is not None:
            return client
        now = time.time()
        cid = str(uuid.uuid4())
        self._insert(
            "INSERT INTO client (c_id, c_alias, c_details, c_created_at, c_updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (cid, alias, "{}", now, now),
        )
        return {"id": cid, "alias": alias, "details": {}}

    def save_authentication(
        self,
        c_id: str,
        provider_name: str,
        env: dict,
        expires_at: float | None = None,
    ) -> int:
        """Upsert de credenciales por (cliente, proveedor) en ``sa_details``.

        ``env`` es el bloque de variables de entorno del servicio (claves como
        ``BITBUCKET_TOKEN``, ``AWS_PROFILE``, ...). Los valores vacíos se
        descartan. Conserva ``sa_created_at``; actualiza contenido y
        ``sa_updated_at``.

        Atómico gracias al UNIQUE index en ``(c_id, sp_id)`` + ``ON CONFLICT``
        en un solo statement (sin race lookup/insert).
        """
        provider = self.get_provider(provider_name)
        if provider is None:
            raise ValueError(f"Provider desconocido: {provider_name}")
        clean_env = {k: v for k, v in env.items() if v is not None and str(v) != ""}
        details = {"env": clean_env}
        if expires_at is not None:
            details["expires_at"] = float(expires_at)
        now = time.time()
        fp = self._session_fingerprint(sa=details)
        with self._lock:
            cur = self._conn.cursor()
            try:
                cur.execute(
                    "INSERT INTO service_authentication "
                    "(c_id, sp_id, sa_details, sa_created_at, sa_updated_at) "
                    "VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(c_id, sp_id) DO UPDATE SET "
                    "  sa_details = excluded.sa_details, "
                    "  sa_updated_at = excluded.sa_updated_at "
                    "RETURNING sa_id",
                    (c_id, provider["id"], fp, now, now),
                )
                row = cur.fetchone()
            finally:
                cur.close()
            self._conn.commit()
        return row[0] if row is not None else 0

    def get_authentication(self, c_id: str, provider_name: str) -> dict | None:
        """Devuelve el bloque ``env`` (+ ``expires_at``) de un proveedor."""
        provider = self.get_provider(provider_name)
        if provider is None:
            return None
        row = self._fetchone(
            "SELECT sa_id, sa_details FROM service_authentication "
            "WHERE c_id = ? AND sp_id = ?",
            (c_id, provider["id"]),
        )
        if row is None:
            return None
        try:
            parsed = json.loads(row[1]).get("sa") or {}
        except (ValueError, TypeError):
            parsed = {}
        return {
            "sa_id": row[0],
            "env": parsed.get("env", {}),
            "expires_at": parsed.get("expires_at"),
        }

    def list_authentications(self, c_id: str) -> list[dict]:
        rows = self._fetchall(
            "SELECT sp.sp_name, sa.sa_details, sa.sa_updated_at "
            "FROM service_authentication sa "
            "JOIN service_provider sp ON sp.sp_id = sa.sp_id "
            "WHERE sa.c_id = ?",
            (c_id,),
        )
        out = []
        for name, details, updated in rows:
            try:
                parsed = json.loads(details).get("sa") or {}
            except (ValueError, TypeError):
                parsed = {}
            out.append({
                "provider": name,
                "env": parsed.get("env", {}),
                "expires_at": parsed.get("expires_at"),
                "updated_at": updated,
            })
        return out

    def clear_authentications(self, c_id: str) -> None:
        """Elimina todas las credenciales del cliente (no toca su fila)."""
        self._execute("DELETE FROM service_authentication WHERE c_id = ?", (c_id,))

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

    def save_connection(self, details: dict) -> int:
        """Upsert de la fila de conexión (config/connection).

        Guarda el JSON canónico con las credenciales por servicio (bitbucket,
        circle, aws) y los settings. Conserva ``is_created_at`` de la fila
        previa; actualiza solo su contenido e ``is_updated_at``.
        """
        now = time.time()
        fp = self._session_fingerprint(config=details)
        row = self._fetchone(
            "SELECT is_id FROM init_sesion "
            "WHERE is_source = ? AND is_target = ?",
            (CONN_SOURCE, CONN_TARGET),
        )
        if row is not None:
            self._execute(
                "UPDATE init_sesion SET is_details = ?, is_updated_at = ? "
                "WHERE is_id = ?",
                (fp, now, row[0]),
            )
            return row[0]
        return self._insert(
            "INSERT INTO init_sesion "
            "(is_source, is_target, is_details, is_created_at, is_updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (CONN_SOURCE, CONN_TARGET, fp, now, now),
        )

    def get_connection(self) -> dict | None:
        """Devuelve el JSON canónico de la fila de conexión (o ``None``)."""
        row = self._fetchone(
            "SELECT is_details FROM init_sesion "
            "WHERE is_source = ? AND is_target = ?",
            (CONN_SOURCE, CONN_TARGET),
        )
        if row is None:
            return None
        try:
            parsed = json.loads(row[0])
        except (ValueError, TypeError):
            return None
        details = parsed.get("config") if isinstance(parsed, dict) else None
        return details if isinstance(details, dict) else None

    def clear_connection(self) -> None:
        """Elimina la fila de conexión (no toca las sesiones de consulta)."""
        self._execute(
            "DELETE FROM init_sesion WHERE is_source = ? AND is_target = ?",
            (CONN_SOURCE, CONN_TARGET),
        )

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
                    if table == "init_sesion":
                        cur.execute(
                            "DELETE FROM init_sesion "
                            "WHERE NOT (is_source = ? AND is_target = ?)",
                            (CONN_SOURCE, CONN_TARGET),
                        )
                    else:
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
                    if table == "init_sesion":
                        cur.execute(
                            "DELETE FROM init_sesion "
                            "WHERE NOT (is_source = ? AND is_target = ?)",
                            (CONN_SOURCE, CONN_TARGET),
                        )
                    else:
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

    # -- fachadas CircleCI -------------------------------------------------------

    def get_circleci_project(self, slug: str) -> dict | None:
        return self._get_cached("circleci_project", slug, "project", {})

    def set_circleci_project(self, slug: str, payload: dict) -> None:
        self._set_cached("circleci_project", slug, "project", {}, payload)

    def get_circleci_pipelines(self, slug: str, branch_or_tag: str, kind: str) -> list[dict] | None:
        return self._get_cached(
            "circleci_pipelines", slug, branch_or_tag, {"kind": kind},
        )

    def set_circleci_pipelines(self, slug: str, branch_or_tag: str, kind: str, items: list[dict]) -> None:
        self._set_cached(
            "circleci_pipelines", slug, branch_or_tag, {"kind": kind}, items,
        )

    def get_circleci_workflows(self, pipeline_id: str) -> list[dict] | None:
        return self._get_cached("circleci_workflows", pipeline_id, "", {})

    def set_circleci_workflows(self, pipeline_id: str, items: list[dict]) -> None:
        self._set_cached("circleci_workflows", pipeline_id, "", {}, items)

    def get_circleci_workflow_jobs(self, workflow_id: str) -> list[dict] | None:
        return self._get_cached("circleci_workflow_jobs", workflow_id, "", {})

    def set_circleci_workflow_jobs(self, workflow_id: str, items: list[dict]) -> None:
        self._set_cached("circleci_workflow_jobs", workflow_id, "", {}, items)

    # -- valores SSM (per cliente + decrypt) ------------------------------------

    def get_ssm_values(
        self, c_id: str, paths: list[str], *, decrypt: bool = False, ttl: float = 3600
    ) -> dict[str, dict]:
        """Valores SSM cacheados de un cliente (solo no expirados).

        La key es ``(c_id, path, decrypt)``: el valor desencriptado de un
        cliente nunca entra en la vista de otro (evita escalada de privilegios
        via cache compartida entre usuarios).
        """
        out: dict[str, dict] = {}
        if not paths:
            return out
        now = time.time()
        rows = self._fetchall(
            "SELECT sv_path, sv_value, sv_encrypted, sv_created_at "
            "FROM ssm_value WHERE c_id = ? AND sv_decrypt = ?",
            (c_id, 1 if decrypt else 0),
        )
        for path, value, encrypted, created in rows:
            if path not in paths:
                continue
            if self.is_expired(created, ttl):
                continue
            out[path] = {"value": value, "encrypted": bool(encrypted)}
        return out

    def set_ssm_values(
        self,
        c_id: str,
        values: dict[str, dict],
        *,
        decrypt: bool = False,
        ttl: float = 3600,
    ) -> None:
        """Guarda valores SSM de un cliente (upsert por (c_id, path, decrypt)).

        Los viejos quedan y expiran por TTL al leer; insertar marca
        ``sv_encrypted`` si el valor venía encriptado, así la vista de
        ``decrypt=False`` conoce el flag sin exponer nada.
        """
        if not values:
            return
        now = time.time()
        with self._lock:
            cur = self._conn.cursor()
            try:
                for path, info in values.items():
                    cur.execute(
                        "INSERT INTO ssm_value "
                        "(c_id, sv_path, sv_decrypt, sv_value, sv_encrypted, sv_created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?) "
                        "ON CONFLICT(c_id, sv_path, sv_decrypt) DO UPDATE SET "
                        "  sv_value = excluded.sv_value, "
                        "  sv_encrypted = excluded.sv_encrypted, "
                        "  sv_created_at = excluded.sv_created_at",
                        (
                            c_id,
                            path,
                            1 if decrypt else 0,
                            info.get("value", ""),
                            1 if info.get("encrypted") else 0,
                            now,
                        ),
                    )
            finally:
                cur.close()
            self._conn.commit()

    def clear_ssm_values(self, c_id: str | None = None) -> int:
        """Elimina valores SSM (de un cliente o de todos). Devuelve borrados."""
        if c_id:
            sql, params = "DELETE FROM ssm_value WHERE c_id = ?", (c_id,)
        else:
            sql, params = "DELETE FROM ssm_value", ()
        with self._lock:
            cur = self._conn.cursor()
            try:
                cur.execute(sql, params)
                n = cur.rowcount
            finally:
                cur.close()
            self._conn.commit()
        return n


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