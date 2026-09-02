"""Caché persistente en SQLite para resultados de Bitbucket.

Almacena repos, scan, diff y master params en una DB SQLite que sobrevive
reinicios del servidor.  La key principal es ``{origin}|{destination}``
acompañada de los filtros de prefijos y exclusión para que combinaciones
distintas no colisionen.

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

_DEFAULT_DB = Path.home() / ".bbit" / "cache.db"


def _cache_key(
    origin: str,
    destination: str,
    prefixes: list[str] | None = None,
    exclude: set[str] | None = None,
) -> str:
    p = ",".join(sorted(prefixes or []))
    e = ",".join(sorted(exclude or set()))
    return f"{origin}|{destination}|{p}|{e}"


class ReleaseCache:
    """Caché persistente SQLite thread-safe."""

    def __init__(self, db_path: Path | None = None):
        self._db_path = db_path or _DEFAULT_DB
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._create_tables()

    def _create_tables(self) -> None:
        for table in ("repo_cache", "scan_cache", "diff_cache"):
            self._conn.execute(
                f"""CREATE TABLE IF NOT EXISTS {table} (
                    cache_key TEXT PRIMARY KEY,
                    origin TEXT NOT NULL,
                    destination TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                )"""
            )
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS master_cache (
                cache_key TEXT PRIMARY KEY,
                slug TEXT NOT NULL,
                destination TEXT NOT NULL,
                data_json TEXT NOT NULL,
                created_at REAL NOT NULL
            )"""
        )
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS branch_repos (
                cache_key TEXT PRIMARY KEY,
                origin_branch TEXT NOT NULL,
                destination_branch TEXT NOT NULL,
                repos_json TEXT NOT NULL,
                created_at REAL NOT NULL
            )"""
        )
        self._conn.commit()

    def _fetchone(self, sql: str, params: tuple) -> tuple | None:
        with self._lock:
            cur = self._conn.cursor()
            try:
                cur.execute(sql, params)
                return cur.fetchone()
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

    # -- repos ----------------------------------------------------------------

    def get_repos(
        self,
        origin: str,
        destination: str,
        prefixes: list[str] | None = None,
        exclude: set[str] | None = None,
    ) -> list[dict] | None:
        key = _cache_key(origin, destination, prefixes, exclude)
        row = self._fetchone(
            "SELECT data_json FROM repo_cache WHERE cache_key = ?", (key,)
        )
        if row is None:
            return None
        log.debug("cache hit repos: %s", key)
        return json.loads(row[0])

    def set_repos(
        self,
        origin: str,
        destination: str,
        prefixes: list[str] | None,
        exclude: set[str] | None,
        repos: list[dict],
    ) -> None:
        key = _cache_key(origin, destination, prefixes, exclude)
        self._execute(
            "INSERT OR REPLACE INTO repo_cache (cache_key, origin, destination, data_json, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (key, origin, destination, json.dumps(repos), time.time()),
        )

    # -- branch repos ---------------------------------------------------------

    def get_branch_repos(
        self,
        origin: str,
        destination: str,
        prefixes: list[str] | None = None,
        exclude: set[str] | None = None,
    ) -> list[dict] | None:
        key = _cache_key(origin, destination, prefixes, exclude)
        row = self._fetchone(
            "SELECT repos_json FROM branch_repos WHERE cache_key = ?", (key,)
        )
        if row is None:
            return None
        log.debug("cache hit branch_repos: %s", key)
        return json.loads(row[0])

    def set_branch_repos(
        self,
        origin: str,
        destination: str,
        prefixes: list[str] | None,
        exclude: set[str] | None,
        repos: list[dict],
    ) -> None:
        key = _cache_key(origin, destination, prefixes, exclude)
        self._execute(
            "INSERT OR REPLACE INTO branch_repos (cache_key, origin_branch, destination_branch, repos_json, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (key, origin, destination, json.dumps(repos), time.time()),
        )

    # -- scan -----------------------------------------------------------------

    def get_scan(
        self,
        origin: str,
        destination: str,
        project_prefixes: list[str] | None = None,
        exclude: set[str] | None = None,
        deploy_prefixes: list[str] | None = None,
    ) -> dict | None:
        key = _cache_key(origin, destination, project_prefixes, exclude)
        if deploy_prefixes:
            key = f"{key}|{','.join(sorted(deploy_prefixes))}"
        row = self._fetchone(
            "SELECT data_json FROM scan_cache WHERE cache_key = ?", (key,)
        )
        if row is None:
            return None
        log.debug("cache hit scan: %s", key)
        return json.loads(row[0])

    def set_scan(
        self,
        origin: str,
        destination: str,
        project_prefixes: list[str] | None,
        exclude: set[str] | None,
        data: dict,
        deploy_prefixes: list[str] | None = None,
    ) -> None:
        key = _cache_key(origin, destination, project_prefixes, exclude)
        if deploy_prefixes:
            key = f"{key}|{','.join(sorted(deploy_prefixes))}"
        self._execute(
            "INSERT OR REPLACE INTO scan_cache (cache_key, origin, destination, data_json, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (key, origin, destination, json.dumps(data), time.time()),
        )

    # -- diff -----------------------------------------------------------------

    def get_diff(
        self,
        origin: str,
        destination: str,
        prefixes: list[str] | None = None,
        exclude: set[str] | None = None,
        ssm_prefixes: list[str] | None = None,
    ) -> dict | None:
        key = _cache_key(origin, destination, prefixes, exclude)
        if ssm_prefixes:
            key = f"{key}|{','.join(sorted(ssm_prefixes))}"
        row = self._fetchone(
            "SELECT data_json FROM diff_cache WHERE cache_key = ?", (key,)
        )
        if row is None:
            return None
        log.debug("cache hit diff: %s", key)
        return json.loads(row[0])

    def set_diff(
        self,
        origin: str,
        destination: str,
        prefixes: list[str] | None,
        exclude: set[str] | None,
        data: dict,
        ssm_prefixes: list[str] | None = None,
    ) -> None:
        key = _cache_key(origin, destination, prefixes, exclude)
        if ssm_prefixes:
            key = f"{key}|{','.join(sorted(ssm_prefixes))}"
        self._execute(
            "INSERT OR REPLACE INTO diff_cache (cache_key, origin, destination, data_json, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (key, origin, destination, json.dumps(data), time.time()),
        )

    # -- master params --------------------------------------------------------

    def get_master(self, slug: str, destination: str) -> set | None:
        key = f"{slug}|{destination}"
        row = self._fetchone(
            "SELECT data_json FROM master_cache WHERE cache_key = ?", (key,)
        )
        if row is None:
            return None
        log.debug("cache hit master: %s", key)
        return set(tuple(p) for p in json.loads(row[0]))

    def set_master(self, slug: str, destination: str, params: set) -> None:
        key = f"{slug}|{destination}"
        self._execute(
            "INSERT OR REPLACE INTO master_cache (cache_key, slug, destination, data_json, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (key, slug, destination, json.dumps(list(params)), time.time()),
        )

    # -- invalidation ---------------------------------------------------------

    def invalidate(
        self,
        origin: str,
        destination: str,
        prefixes: list[str] | None = None,
        exclude: set[str] | None = None,
        ssm_prefixes: list[str] | None = None,
    ) -> None:
        base = _cache_key(origin, destination, prefixes, exclude)
        keys = {base}
        if ssm_prefixes:
            keys.add(f"{base}|{','.join(sorted(ssm_prefixes))}")
        with self._lock:
            for table in ("repo_cache", "scan_cache", "diff_cache", "branch_repos"):
                cur = self._conn.cursor()
                try:
                    for key in keys:
                        cur.execute(f"DELETE FROM {table} WHERE cache_key = ?", (key,))
                finally:
                    cur.close()
            self._conn.commit()
        log.debug("invalidated cache: %s", sorted(keys))

    def invalidate_all(self) -> None:
        with self._lock:
            for table in ("repo_cache", "scan_cache", "diff_cache", "master_cache", "branch_repos"):
                cur = self._conn.cursor()
                try:
                    cur.execute(f"DELETE FROM {table}")
                finally:
                    cur.close()
            self._conn.commit()
        log.debug("invalidated all cache")

    def close(self) -> None:
        with self._lock:
            self._conn.close()


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
