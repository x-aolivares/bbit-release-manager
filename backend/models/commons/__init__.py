"""DTOs comunes: contenedores genéricos de request/response."""

from .envelope_model import (
    BackendRequestEntity,
    BackendResponseEntity,
    StatusDTO,
)

__all__ = ["BackendRequestEntity", "BackendResponseEntity", "StatusDTO"]