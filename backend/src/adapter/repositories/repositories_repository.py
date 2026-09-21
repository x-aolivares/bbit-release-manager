"""Repositorio de la tabla ``repositories`` con queries nativas (sin ORM).

Guardan el resultado del scan de Bitbucket: una fila por repositorio, con el
payload completo del scan como JSON en ``r_details``. El cache del endpoint
``/api/scan-repositories`` escribe aca (``replace_all``) y lee de aca
(``get_all``) cuando el ultimo request es fresco.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from backend.library.enums import GlobalConfigEnum
from ..entities.repositories_entity import RepositoryEntity


class RepositoriesRepository:
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
                CREATE TABLE IF NOT EXISTS repositories (
                    r_id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    r_name        TEXT    NOT NULL,
                    r_created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
                    r_details     TEXT    NOT NULL DEFAULT '{}'
                )
                """
            )

    def get_all(self) -> list[RepositoryEntity]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT r_id, r_name, r_created_at, r_details FROM repositories"
            ).fetchall()
        return [RepositoryEntity.from_row(row) for row in rows]

    def replace_all(self, name_details: list[tuple[str, str]]) -> int:
        """Reemplaza el contenido del cache en una transaccion."""
        with self._connect() as conn:
            conn.execute("DELETE FROM repositories")
            conn.executemany(
                "INSERT INTO repositories (r_name, r_details) VALUES (?, ?)",
                name_details,
            )
            return sum(1 for _ in name_details)

    def get_by_name(self, name: str) -> RepositoryEntity | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT r_id, r_name, r_created_at, r_details "
                "FROM repositories WHERE r_name = ?",
                (name,),
            ).fetchone()
        if row is None:
            return None
        return RepositoryEntity.from_row(row)