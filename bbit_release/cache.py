"""Caché persistente en SQLite para resultados de Bitbucket.

Almacena repos, scan, diff y master params en una DB SQLite que sobrevive
reinicios del servidor.  La key principal es ``{origin}|{destination}``
acompañada de los filtros de prefijos y exclusión para que combinaciones
distintas no colisionen.
"""

from __future__ import annotations

import json
import logging
import sqlite3
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
    """Caché persistente SQLite singleton-style."""

    def __init__(self, db_path: Path | None = None):
        self._db_path = db_path or _DEFAULT_DB
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
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
        row = self._conn.execute(
            "SELECT data_json FROM repo_cache WHERE cache_key = ?", (key,)
        ).fetchone()
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
        self._conn.execute(
            "INSERT OR REPLACE INTO repo_cache (cache_key, origin, destination, data_json, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (key, origin, destination, json.dumps(repos), time.time()),
        )
        self._conn.commit()

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
        row = self._conn.execute(
            "SELECT data_json FROM scan_cache WHERE cache_key = ?", (key,)
        ).fetchone()
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
        self._conn.execute(
            "INSERT OR REPLACE INTO scan_cache (cache_key, origin, destination, data_json, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (key, origin, destination, json.dumps(data), time.time()),
        )
        self._conn.commit()

    # -- diff -----------------------------------------------------------------

    def get_diff(
        self,
        origin: str,
        destination: str,
        prefixes: list[str] | None = None,
        exclude: set[str] | None = None,
    ) -> dict | None:
        key = _cache_key(origin, destination, prefixes, exclude)
        row = self._conn.execute(
            "SELECT data_json FROM diff_cache WHERE cache_key = ?", (key,)
        ).fetchone()
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
    ) -> None:
        key = _cache_key(origin, destination, prefixes, exclude)
        self._conn.execute(
            "INSERT OR REPLACE INTO diff_cache (cache_key, origin, destination, data_json, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (key, origin, destination, json.dumps(data), time.time()),
        )
        self._conn.commit()

    # -- master params --------------------------------------------------------

    def get_master(self, slug: str, destination: str) -> set | None:
        key = f"{slug}|{destination}"
        row = self._conn.execute(
            "SELECT data_json FROM master_cache WHERE cache_key = ?", (key,)
        ).fetchone()
        if row is None:
            return None
        log.debug("cache hit master: %s", key)
        return set(tuple(p) for p in json.loads(row[0]))

    def set_master(self, slug: str, destination: str, params: set) -> None:
        key = f"{slug}|{destination}"
        self._conn.execute(
            "INSERT OR REPLACE INTO master_cache (cache_key, slug, destination, data_json, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (key, slug, destination, json.dumps(list(params)), time.time()),
        )
        self._conn.commit()

    # -- invalidation ---------------------------------------------------------

    def invalidate(
        self,
        origin: str,
        destination: str,
        prefixes: list[str] | None = None,
        exclude: set[str] | None = None,
    ) -> None:
        key = _cache_key(origin, destination, prefixes, exclude)
        for table in ("repo_cache", "scan_cache", "diff_cache"):
            self._conn.execute(f"DELETE FROM {table} WHERE cache_key = ?", (key,))
        self._conn.commit()
        log.debug("invalidated cache: %s", key)

    def invalidate_all(self) -> None:
        for table in ("repo_cache", "scan_cache", "diff_cache", "master_cache"):
            self._conn.execute(f"DELETE FROM {table}")
        self._conn.commit()
        log.debug("invalidated all cache")

    def close(self) -> None:
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
