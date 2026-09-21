"""Entidad de base de datos del historial de requests.

Mapea la tabla ``request_history``: una fila por endpoint consultado, con el
timestamp del ultimo llamado (base del cache por TTL).
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.library.patterns import EntityBase


@dataclass(frozen=True, slots=True)
class RequestHistoryEntity(EntityBase):
    rh_id: int
    rh_endpoint: str
    rh_updated_at: str