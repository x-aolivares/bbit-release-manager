"""Repositorio de la tabla ``records`` con queries nativas (sin ORM).

Preferimos queryNativo: cada metodo escribe el SQL a mano y mapea filas a
``Entity``. La conexion se crea por operacion; para escrituras concurrentes
se delega en un lock o en ``check_same_thread=False`` según la config del
pool.

Nomenclatura: ``{tabla}_repository.py`` (esta tabla es ``records``).
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from ..entities.records_entity import RecordsEntity


class RepositoryRepository:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init(self) -> None:
        with self._connect() as conn:
            # WAL: lectores y escritor conviven; el lock real queda por archivo.
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS repositories (
                    r_id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    r_name        TEXT    NOT NULL,
                    r_created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
                )
                """
            )

    def insert_record(self, name: str) -> int:
        with self._connect() as conn:
            cur = conn.execute("INSERT INTO repositories (r_name) VALUES (?)", (name,))
            return int(cur.lastrowid)

    def get_record(self, record_id: int) -> RepositoryRepository | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT r_id, r_name, r_created_at FROM r_repositories r WHERE r_id = ?",
                (record_id,),
            ).fetchone()
        if row is None:
            return None
        return RepositoryRepository(row)
