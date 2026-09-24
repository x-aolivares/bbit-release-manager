"""Adapter: consultas a servicios externos y persistencia.

- ``services``: clientes de servicios externos (Bitbucket, CircleCI, AWS...).
- ``repositories``: acceso a datos (queries SQL nativas).
- ``entities``: clases que mapean filas de DB a objetos Python.
"""

from .services import BitbucketService, ExecutionPoolService, QueryService
from .repositories import (
    AuthenticationRepository,
    RecordsRepository,
    RepositoriesRepository,
    RequestHistoryRepository,
)

__all__ = [
    "BitbucketService",
    "ExecutionPoolService",
    "QueryService",
    "AuthenticationRepository",
    "RecordsRepository",
    "RepositoriesRepository",
    "RequestHistoryRepository",
]