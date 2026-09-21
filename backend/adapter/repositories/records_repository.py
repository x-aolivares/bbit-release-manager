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


class RecordsRepository:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS records (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    name        TEXT    NOT NULL,
                    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
                )
                """
            )

    def insert_record(self, name: str) -> int:
        with self._connect() as conn:
            cur = conn.execute("INSERT INTO records (name) VALUES (?)", (name,))
            return int(cur.lastrowid)

    def get_record(self, record_id: int) -> RecordsEntity | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, name, created_at FROM records WHERE id = ?",
                (record_id,),
            ).fetchone()
        if row is None:
            return None
        return RecordsEntity(id=row["id"], name=row["name"], created_at=row["created_at"])