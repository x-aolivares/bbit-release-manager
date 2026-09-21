"""Servicios del adapter (consultas a servicios externos, pool de ejecucion).

Nomenclatura: ``{name}_service.py``.
"""

from .execution_pool_service import ExecutionPoolService

__all__ = ["ExecutionPoolService"]