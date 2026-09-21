"""Adapter: consultas a servicios externos y persistencia.

- ``services``: clientes de servicios externos (Bitbucket, CircleCI, AWS...).
- ``repositories``: acceso a datos (queries SQL nativas).
- ``entities``: clases que mapean filas de DB a objetos Python.
"""

from .services import ExecutionPoolService
from .repositories import RecordsRepository

__all__ = ["ExecutionPoolService", "RecordsRepository"]