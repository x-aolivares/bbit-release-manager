"""Command del escaneo de repositorios: orquesta cache + Bitbucket.

Delega en el logic central y envuelve el resultado en el patron ResultSet.
Los errores esperados de la capa de negocio (API Bitbucket, parseos fallidos)
se capturan y devuelven como ``Failure`` para que el controller decida el
HTTP status.
"""

from __future__ import annotations

from ..logics.scan_repositories_logic import ScanRepositoriesLogic
from backend.library.patterns.result_pattern import Failure, ResultSet, Success
from backend.src.models.scan_repositories_model import ScanRepositoriesResponse


class ScanRepositoriesCommand:
    def __init__(self, logic: ScanRepositoriesLogic) -> None:
        self._logic = logic

    def run(self) -> ResultSet[ScanRepositoriesResponse]:
        try:
            body = self._logic.run()
        except Exception as exc:  # noqa: BLE001 - resultado via ResultSet
            return Failure(str(exc))
        return Success(body)