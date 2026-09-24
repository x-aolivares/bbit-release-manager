"""Endpoints del flujo de sesión: credenciales de servicios externos.

- ``POST /api/session``: aplica ``CREATE``/``UPDATE``/``DELETE`` sobre las
  credenciales del body y devuelve la lista vigente.
- ``GET /api/session``: devuelve las credenciales guardadas.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from starlette.responses import JSONResponse

from backend.library.deps import get_session_command
from backend.library.models.envelope_model import (
    BackendRequestEntity,
    BackendResponseEntity,
    StatusDTO,
)
from backend.library.patterns import ResultSet
from backend.src.commands.session_command import SessionCommand
from backend.src.enums import BCStatusEnum
from backend.src.models.session_model import SessionBodyModel, SessionResponseModel

router = APIRouter(prefix="/api", tags=["session"])


@router.post(
    "/session",
    response_model=BackendResponseEntity[SessionResponseModel],
    status_code=201,
)
def manage_session(
    payload: BackendRequestEntity[SessionBodyModel],
    command: SessionCommand = Depends(get_session_command),
) -> JSONResponse | BackendResponseEntity[SessionResponseModel]:
    result: ResultSet[SessionResponseModel] = command.write(payload.body)
    if not result.ok:
        status = StatusDTO.from_status(BCStatusEnum.INTERNAL_ERROR)
        envelope = BackendResponseEntity[SessionResponseModel].model_construct(
            body=None,
            status=status,
        )
        return JSONResponse(
            status_code=status.httpStatus,
            content=envelope.model_dump(mode="json"),
        )
    return BackendResponseEntity[SessionResponseModel](
        body=result.body,
        status=StatusDTO.from_status(BCStatusEnum.OK),
    )


@router.get(
    "/session",
    response_model=BackendResponseEntity[SessionResponseModel],
)
def read_session(
    command: SessionCommand = Depends(get_session_command),
) -> JSONResponse | BackendResponseEntity[SessionResponseModel]:
    result: ResultSet[SessionResponseModel] = command.read()
    if not result.ok:
        status = StatusDTO.from_status(BCStatusEnum.INTERNAL_ERROR)
        envelope = BackendResponseEntity[SessionResponseModel].model_construct(
            body=None,
            status=status,
        )
        return JSONResponse(
            status_code=status.httpStatus,
            content=envelope.model_dump(mode="json"),
        )
    return BackendResponseEntity[SessionResponseModel](
        body=result.body,
        status=StatusDTO.from_status(BCStatusEnum.OK),
    )