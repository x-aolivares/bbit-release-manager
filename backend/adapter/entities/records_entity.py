"""Entidad de base de datos para un registro de prueba.

Mapea la tabla ``records`` a un objeto Python.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RecordsEntity:
    id: int
    name: str
    created_at: str