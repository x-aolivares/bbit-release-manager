"""Acceso a datos: repositorios con queries SQL nativas (queryNativo).

Nomenclatura: un archivo por tabla, ``{tabla}_repository.py``.
"""

from .records_repository import RecordsRepository

__all__ = ["RecordsRepository"]