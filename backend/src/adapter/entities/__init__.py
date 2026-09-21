"""Entidades de base de datos.

No confundir con DTOs: representan la info tal como se persiste/lee de DB.

Nomenclatura: ``{tabla}_entity.py``.
"""

from .records_entity import RecordsEntity
from .repositories_entity import RepositoryEntity
from .request_history_entity import RequestHistoryEntity

__all__ = ["RecordsEntity", "RepositoryEntity", "RequestHistoryEntity"]