"""Entidades de base de datos.

No confundir con DTOs: representan la info tal como se persiste/lee de DB.

Nomenclatura: ``{tabla}_entity.py``.
"""

from .authentication_entity import AuthenticationEntity
from .records_entity import RecordsEntity
from .repositories_entity import RepositoryEntity
from .request_history_entity import RequestHistoryEntity

__all__ = [
    "AuthenticationEntity",
    "RecordsEntity",
    "RepositoryEntity",
    "RequestHistoryEntity",
]