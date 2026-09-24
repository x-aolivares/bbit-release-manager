"""Command del flujo de sesión: CRUD de credenciales con patrón ResultSet.

Delega en ``SessionLogic`` y envuelve el resultado en ``Success``/``Failure``.
Los errores esperados de negocio (duplicada en CREATE, inexistente en DELETE)
llegan como ``Failure`` para que el controller decida el HTTP status.
"""

from __future__ import annotations

from backend.library.patterns import ResultSet
from backend.library.patterns.result_pattern import Failure, Success
from ..logics.session_logic import SessionLogic
from ..models.session_model import SessionBodyModel, SessionResponseModel


class SessionCommand:
    def __init__(self, logic: SessionLogic) -> None:
        self._logic = logic

    def write(self, body: SessionBodyModel) -> ResultSet[SessionResponseModel]:
        try:
            response = self._logic.write(body)
        except Exception as exc:  # noqa: BLE001 - resultado via ResultSet
            return Failure(str(exc))
        return Success(response)

    def read(self) -> ResultSet[SessionResponseModel]:
        try:
            response = self._logic.read()
        except Exception as exc:  # noqa: BLE001 - resultado via ResultSet
            return Failure(str(exc))
        return Success(response)