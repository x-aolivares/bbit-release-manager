"""DTOs genéricos de request/response para el pipeline del backend.

Envoltura común: requests y responses del backend transportan el
cuerpo de negocio (``body``) y, en el caso de las respuestas, el
estado de la operación (``status``).
"""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class StatusDTO(BaseModel):
    """Estado de una operación expuesta por el backend."""

    code: int
    message: str
    success: bool


class BackendRequestEntity(BaseModel, Generic[T]):
    """Contenedor genérico de request: transporta el cuerpo de negocio."""

    body: T


class BackendResponseEntity(BackendRequestEntity[T], Generic[T]):
    """Contenedor genérico de response: agrega el estado al cuerpo heredado."""

    status: StatusDTO