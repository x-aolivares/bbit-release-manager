"""Base para entidades: mapeo automatico de filas sqlite a dataclasses.

Cada entidad extiende ``EntityBase`` y solo declara sus campos; ``from_row``
arma la instancia por NOMBRE DE COLUMNA. Reglas:

- Las columnas del ``SELECT`` deben coincidir con los campos de la entidad.
- Columnas de mas en la fila se ignoran (sirve para ``SELECT *`` y joins).
- ``frozen`` y ``slots`` del dataclass no se ven afectados.
"""

from __future__ import annotations

import sqlite3
from dataclasses import fields
from typing import Self


class EntityBase:
    """Base comun de entities: provee ``from_row`` sin tocar ``frozen``/``slots``."""

    __slots__ = ()

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Self:
        """Construye la entidad a partir de una fila ``sqlite3.Row``.

        Requisito: ``conn.row_factory = sqlite3.Row`` en la conexion que
        originó la fila. Los campos se toman del dataclass; columnas ajenas
        al SELECT quedan fuera.
        """
        field_names = {item.name for item in fields(cls)}
        return cls(**{name: row[name] for name in row.keys() if name in field_names})