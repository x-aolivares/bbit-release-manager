"""Envelopes genericos de request/response para los endpoints.

Viven en ``library`` (no en ``models``) porque son codigo reutilizable del
monolito, independiente de los DTOs de dominio de cada flujo.

Nomenclatura: ``{name}_model.py``.
"""

from .envelope_model import (
    BackendRequestEntity,
    BackendResponseEntity,
    StatusDTO,
)

__all__ = ["BackendRequestEntity", "BackendResponseEntity", "StatusDTO"]