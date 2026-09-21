"""Entidad de base de datos para un registro de prueba.

Mapea la tabla ``records`` a un objeto Python. El mapeo fila -> entidad es
automatico: hereda ``EntityBase.from_row`` y las columnas del ``SELECT`` deben
coincidir con los campos de la entidad.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.library.patterns import EntityBase


@dataclass(frozen=True, slots=True)
class RecordsEntity(EntityBase):
    id: int
    name: str
    created_at: str