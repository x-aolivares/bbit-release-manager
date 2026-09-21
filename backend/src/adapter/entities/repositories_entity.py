"""Entidad de base de datos para un repositorio escaneado.

Mapea la tabla ``repositories`` a un objeto Python. ``r_details`` guarda el
payload JSON completo del scan (estructura ``ScanRepositoriesModel``).
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.library.patterns import EntityBase


@dataclass(frozen=True, slots=True)
class RepositoryEntity(EntityBase):
    r_id: int
    r_name: str
    r_created_at: str
    r_details: str