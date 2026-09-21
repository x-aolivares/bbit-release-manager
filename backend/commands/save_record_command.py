"""Command de prueba: orquesta el guardado de un registro en sqlite.

Este es el unico endpoint de prueba pedido: guarda ``x`` en sqlite para
validar el flujo completo controller -> command -> logic -> repository ->
sqlite. Demuestra ademas ejecucion en el pool parametrizable.
"""

from __future__ import annotations

from concurrent.futures import wait
from typing import TYPE_CHECKING

from ..logics.record_logic import RecordLogic
from ..models.record_model import RecordCreate, RecordOut
from .result import Failure, ResultSet, Success

if TYPE_CHECKING:
    from ..adapter.services.execution_pool_service import ExecutionPoolService


class SaveRecordCommand:
    def __init__(self, logic: RecordLogic, pool: "ExecutionPoolService") -> None:
        self._logic = logic
        self._pool = pool

    def run(self, payload: RecordCreate) -> ResultSet[RecordOut]:
        # Flujo concurrente parametrizable: la persistencia corre en el pool.
        with self._pool.build() as executor:
            futures = [executor.submit(self._logic.save_record, payload.name)]
            done, _ = wait(futures)
            future = next(iter(done))
        exc = future.exception()
        if exc is not None:
            return Failure(str(exc))
        entity = future.result()
        return Success(
            RecordOut(id=entity.id, name=entity.name, created_at=entity.created_at)
        )
