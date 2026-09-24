"""Logica de sesion: CRUD de credenciales contra ``authentication``.

Despacha la operación pedida (``CREATE``/``UPDATE``/``DELETE``) sobre todas las
credenciales del body y devuelve la lista vigente. Los errores esperados
(credencial inexistente o duplicada) se reportan como ``ValueError`` para que
el command los convierta en ``Failure`` con mensaje de negocio.
"""

from __future__ import annotations

import json
from typing import List

from ..adapter.entities.authentication_entity import AuthenticationEntity
from ..adapter.repositories.authentication_repository import AuthenticationRepository
from ..models.session_model import CredentialModel, SessionBodyModel, SessionResponseModel


class SessionLogic:
    def __init__(self, authentication_repository: AuthenticationRepository) -> None:
        self._auth = authentication_repository

    def write(self, body: SessionBodyModel) -> SessionResponseModel:
        for credential in body.credentials:
            self._apply(body.options.value, credential)
        return self.read()

    def read(self) -> SessionResponseModel:
        entities = self._auth.get_all()
        return SessionResponseModel(
            credentials=[self._entity_to_model(entity) for entity in entities]
        )

    def _apply(self, options: str, credential: CredentialModel) -> None:
        auth_type = credential.type.value
        auth_name = credential.name
        if options == "CREATE":
            if self._auth.get_by_type_name(auth_type, auth_name) is not None:
                raise ValueError(f"ya existe una credencial {auth_type}:{auth_name}")
            self._auth.insert(auth_type, auth_name, credential.value, self._details(credential))
        elif options == "UPDATE":
            self._auth.upsert(auth_type, auth_name, credential.value, self._details(credential))
        elif options == "DELETE":
            if not self._auth.delete(auth_type, auth_name):
                raise ValueError(f"no existe una credencial {auth_type}:{auth_name}")

    @staticmethod
    def _details(credential: CredentialModel) -> str:
        return json.dumps(
            {"type": credential.type.value, "name": credential.name, "value": credential.value}
        )

    @staticmethod
    def _entity_to_model(entity: AuthenticationEntity) -> CredentialModel:
        return CredentialModel(
            type=entity.auth_type,
            name=entity.auth_name,
            value=entity.auth_value,
        )