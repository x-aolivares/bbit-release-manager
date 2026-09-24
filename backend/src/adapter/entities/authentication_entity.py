"""Entidad de base de datos de una credencial de sesión.

Mapea la tabla ``authentication``: una fila por credencial (servicio + nombre),
siguiendo la convención de prefijos por tabla (``auth_*``). ``auth_details``
guarda el payload JSON completo de la credencial (``CredentialModel``).
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.library.patterns import EntityBase


@dataclass(frozen=True, slots=True)
class AuthenticationEntity(EntityBase):
    auth_id: int
    auth_uuid: str
    auth_type: str
    auth_name: str
    auth_value: str
    auth_details: str
    auth_created_at: str
    auth_updated_at: str