"""Acceso a datos: repositorios con queries SQL nativas (queryNativo).

Nomenclatura: un archivo por tabla, ``{tabla}_repository.py``.
"""

from .authentication_repository import AuthenticationRepository
from .records_repository import RecordsRepository
from .repositories_repository import RepositoriesRepository
from .request_history_repository import RequestHistoryRepository

__all__ = [
    "AuthenticationRepository",
    "RecordsRepository",
    "RepositoriesRepository",
    "RequestHistoryRepository",
]