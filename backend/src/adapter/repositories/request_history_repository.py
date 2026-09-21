"""Repositorio de la tabla ``request_history`` con queries nativas.

Registra el ultimo llamado a cada endpoint: ``rh_updated_at`` alimenta la
decision de cache del flujo de scan (fresco vs vencido).
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from backend.library.enums import GlobalConfigEnum
from ..entities.request_history_entity import RequestHistoryEntity


class RequestHistoryRepository:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._db_path, timeout=GlobalConfigEnum.SQLITE_CONNECT_TIMEOUT.value)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init(self) -> None:
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS request_history (
                    rh_id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    rh_endpoint    TEXT    NOT NULL UNIQUE,
                    rh_updated_at  TEXT    NOT NULL
                )
                """
            )

    def get_by_endpoint(self, endpoint: str) -> RequestHistoryEntity | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT rh_id, rh_endpoint, rh_updated_at "
                "FROM request_history WHERE rh_endpoint = ?",
                (endpoint,),
            ).fetchone()
        if row is None:
            return None
        return RequestHistoryEntity.from_row(row)

    def touch(self, endpoint: str) -> None:
        """Marca el endpoint como consultado ahora (upsert)."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO request_history (rh_endpoint, rh_updated_at)
                VALUES (?, datetime('now'))
                ON CONFLICT(rh_endpoint) DO UPDATE SET
                    rh_updated_at = datetime('now')
                """,
                (endpoint,),
            )