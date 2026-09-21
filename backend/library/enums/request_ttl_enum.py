"""TTL (en minutos) de cache por request/endpoint.

Vigencia que se valida antes de reconsultar servicios externos: si el valor
cambia, se toca SOLO este enum y todos los flujos lo leen de un solo lugar.
"""

from __future__ import annotations

from enum import Enum


class RequestTtlEnum(Enum):
    """Vigencia del cache por endpoint, en minutos."""

    SCAN_REPOSITORIES = 30