"""Parametros de configuracion global del monolito.

Un solo lugar para valores que se leen constantemente al iniciar o validar
procesos (timeouts, limites, defaults). Si un valor cambia, se toca SOLO este
enum y todo el codigo que lo usa queda alineado.
"""

from __future__ import annotations

from enum import Enum


class GlobalConfigEnum(Enum):
    """Configuracion global compartida por la infraestructura."""

    SQLITE_CONNECT_TIMEOUT = 5.0