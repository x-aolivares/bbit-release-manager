"""QueryService: conexiones de LECTURA que ven los schemas como ``schema.tabla``.

Cada repository de dominio escribe contra su propio archivo (que queda como
``main``). Las queries con nombres completos (``bbit_transaction.rmc``) y los
joins cross-schemas necesitan una conexion que ATACHE los schemas bajo su
propio nombre: este servicio arma esa conexion (alias = nombre del schema =
nombre del archivo).

Es SOLO de lectura/queries: no persiste nada, la conexion vive en memoria y
se cierra al salir del ``with``.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Iterator

from backend.library.config import Settings
from backend.library.enums import GlobalConfigEnum


class QueryService:
    """Conexiones de consulta multi-schema (alias ``bbit_x`` = archivo ``bbit_x.db``)."""

    def __init__(self, settings: Settings) -> None:
        self._schemas = settings.schemas

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """Abre una conexion con todos los schemas attachados por su nombre.

        Uso::

            with query_service.connect() as conn:
                rows = conn.execute(
                    "SELECT r.* FROM bbit_transaction.rmc r "
                    "JOIN bbit_profile.abc p ON p.id = r.id"
                ).fetchall()

        Los nombres de schema salen de ``DB_SCHEMAS`` (constante, no input de
        usuario), por eso el alias se interpola y solo la RUTA va como parametro.
        """
        conn = sqlite3.connect(":memory:", timeout=GlobalConfigEnum.SQLITE_CONNECT_TIMEOUT.value)
        conn.row_factory = sqlite3.Row
        try:
            for schema, path in self._schemas.items():
                conn.execute(f'ATTACH DATABASE ? AS "{schema}"', (str(path),))
            yield conn
        finally:
            conn.close()