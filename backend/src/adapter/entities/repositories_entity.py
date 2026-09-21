"""Entidad de base de datos para un registro de prueba.

Mapea la tabla ``records`` a un objeto Python.
"""

from __future__ import annotations

from dataclasses import dataclass

from library.patterns import EntityBase


@dataclass(frozen=True, slots=True)
class RepositoryEntity(EntityBase):
    r_id: int
    r_name: str
    r_created_at: str
