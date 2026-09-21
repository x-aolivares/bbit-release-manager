"""Endpoints del flujo de registro de prueba.

Referencia del patrón de request/response del monolito: el endpoint recibe
``BackendRequestEntity[T]`` y responde siempre un ``BackendResponseEntity``,
que envuelve el cuerpo de negocio (``body``) y el estado (``status``).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from starlette.responses import JSONResponse

from ..commands.result import ResultSet
from ..commands.save_record_command import SaveRecordCommand
from ..deps import get_save_record_command
from ..models.commons.envelope_model import (
    BackendRequestEntity,
    BackendResponseEntity,
    StatusDTO,
)
from ..models.record_model import RecordCreate, RecordOut

router = APIRouter(prefix="/api", tags=["records"])


@router.post(
    "/records",
    response_model=BackendResponseEntity[RecordOut | None],
    status_code=201,
)
def create_record(
    payload: BackendRequestEntity[RecordCreate],
    command: SaveRecordCommand = Depends(get_save_record_command),
) -> BackendResponseEntity[RecordOut | None]:
    result: ResultSet[RecordOut] = command.run(payload.body)
    if not result.ok:
        envelope = BackendResponseEntity[RecordOut | None](
            body=None,
            status=StatusDTO(
                code=500,
                message=result.error or "Error interno",
                success=False,
            ),
        )
        return JSONResponse(status_code=500, content=envelope.model_dump(mode="json"))
    return BackendResponseEntity[RecordOut | None](
        body=result.value,
        status=StatusDTO(code=201, message="Registro creado", success=True),
    )
