"""Repositorio de la tabla ``authentication`` con queries nativas (sin ORM).

Credenciales de sesión (tokens de Bitbucket, CircleCI, etc.) que el monolito
consulta para autenticarse contra servicios externos.

Convención de naming: columnas ``auth_*`` (siglas de ``authentication``) y
unicidad por ``(auth_type, auth_name)``: una credencial por servicio + nombre.
"""

from __future__ import annotations

import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from backend.library.enums import GlobalConfigEnum
from ..entities.authentication_entity import AuthenticationEntity


class AuthenticationRepository:
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
                CREATE TABLE IF NOT EXISTS authentication (
                    auth_id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    auth_uuid        TEXT    NOT NULL UNIQUE,
                    auth_type        TEXT    NOT NULL,
                    auth_name        TEXT    NOT NULL,
                    auth_value       TEXT    NOT NULL,
                    auth_details     TEXT    NOT NULL,
                    auth_created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
                    auth_updated_at  TEXT    NOT NULL DEFAULT (datetime('now')),
                    UNIQUE (auth_type, auth_name)
                )
                """
            )

    def get_all(self) -> list[AuthenticationEntity]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT auth_id, auth_uuid, auth_type, auth_name, auth_value, "
                "auth_details, auth_created_at, auth_updated_at FROM authentication"
            ).fetchall()
        return [AuthenticationEntity.from_row(row) for row in rows]

    def get_by_type_name(self, auth_type: str, auth_name: str) -> AuthenticationEntity | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT auth_id, auth_uuid, auth_type, auth_name, auth_value, "
                "auth_details, auth_created_at, auth_updated_at "
                "FROM authentication WHERE auth_type = ? AND auth_name = ?",
                (auth_type, auth_name),
            ).fetchone()
        if row is None:
            return None
        return AuthenticationEntity.from_row(row)

    def insert(
        self,
        auth_type: str,
        auth_name: str,
        auth_value: str,
        auth_details: str,
    ) -> None:
        """Crea la credencial (falla si ya existe ``type + name``)."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO authentication (auth_uuid, auth_type, auth_name, auth_value, auth_details)
                VALUES (?, ?, ?, ?, ?)
                """,
                (str(uuid.uuid4()), auth_type, auth_name, auth_value, auth_details),
            )

    def upsert(
        self,
        auth_type: str,
        auth_name: str,
        auth_value: str,
        auth_details: str,
    ) -> None:
        """Inserta o actualiza la credencial, tocando ``auth_updated_at``."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO authentication (auth_uuid, auth_type, auth_name, auth_value, auth_details)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(auth_type, auth_name) DO UPDATE SET
                    auth_value = excluded.auth_value,
                    auth_details = excluded.auth_details,
                    auth_updated_at = datetime('now')
                """,
                (str(uuid.uuid4()), auth_type, auth_name, auth_value, auth_details),
            )

    def delete(self, auth_type: str, auth_name: str) -> bool:
        """Borra la credencial; devuelve ``False`` si no existía."""
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM authentication WHERE auth_type = ? AND auth_name = ?",
                (auth_type, auth_name),
            )
            return cursor.rowcount > 0