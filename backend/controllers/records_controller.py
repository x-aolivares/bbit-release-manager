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
from ..enums import BCStatusEnum
from ..models.commons.envelope_model import (
    BackendRequestEntity,
    BackendResponseEntity,
    StatusDTO,
)
from ..models.record_model import RecordCreate, RecordOut

router = APIRouter(prefix="/api", tags=["records"])


@router.post(
    "/records",
    response_model=BackendResponseEntity[RecordOut],
    status_code=201,
)
def create_record(
    payload: BackendRequestEntity[RecordCreate],
    command: SaveRecordCommand = Depends(get_save_record_command),
) -> JSONResponse | BackendResponseEntity[RecordOut]:
    result: ResultSet[RecordOut] = command.run(payload.body)
    if not result.ok:
        status = StatusDTO.from_status(BCStatusEnum.INTERNAL_ERROR)
        envelope = BackendResponseEntity[RecordOut].model_construct(
            body=None,
            status=status,
        )
        return JSONResponse(
            status_code=status.httpStatus,
            content=envelope.model_dump(mode="json"),
        )
    return BackendResponseEntity[RecordOut](
        body=result.body,
        status=StatusDTO.from_status(BCStatusEnum.OK),
    )
