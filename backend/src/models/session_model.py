"""DTOs del flujo de sesión: guardado y consulta de credenciales.

Request (``POST /api/session``): un cuerpo de negocio con N credenciales y la
operación a aplicar a TODAS (``CREATE``, ``UPDATE`` o ``DELETE``).
Response: devuelve la lista de credenciales vigentes tras la operación.

La lectura de credenciales es un endpoint aparte (``GET /api/session``).
"""

from __future__ import annotations

from enum import Enum
from typing import List

from pydantic import BaseModel, Field

from backend.src.enums import ExternalService


class SessionOptions(Enum):
    """Operación que se aplica sobre las credenciales recibidas."""

    CREATE = "CREATE"
    UPDATE = "UPDATE"
    DELETE = "DELETE"


class CredentialModel(BaseModel):
    """Una credencial: servicio externo + nombre + secreto."""

    type: ExternalService
    name: str = Field(min_length=1, max_length=200)
    value: str = Field(min_length=1, max_length=2000)


class SessionBodyModel(BaseModel):
    """Cuerpo de negocio del POST: credenciales + operación."""

    credentials: List[CredentialModel] = Field(min_length=1)
    options: SessionOptions


class SessionResponseModel(BaseModel):
    """Credenciales vigentes (respuesta de POST y de GET)."""

    credentials: List[CredentialModel]