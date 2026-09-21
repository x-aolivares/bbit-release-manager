"""Entidades de base de datos.

No confundir con DTOs: representan la info tal como se persiste/lee de DB.

Nomenclatura: ``{tabla}_entity.py``.
"""

from .records_entity import RecordsEntity

__all__ = ["RecordsEntity"]