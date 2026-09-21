"""Endpoints del flujo de escaneo de repositorios.

POST /api/scan-repositories ejecuta el scan con cache por TTL: si el ultimo
request fue reciente devuelve el contenido de ``repositories``; si no, consulta
Bitbucket, mappea, persiste y toca ``request_history``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from starlette.responses import JSONResponse

from backend.library.deps import get_scan_repositories_command
from backend.library.models.envelope_model import (
    BackendResponseEntity,
    StatusDTO,
)
from backend.library.patterns import ResultSet
from backend.src.commands.scan_repositories_command import ScanRepositoriesCommand
from backend.src.enums import BCStatusEnum
from backend.src.models.scan_repositories_model import ScanRepositoriesResponse

router = APIRouter(prefix="/api", tags=["repositories"])


@router.post(
    "/scan-repositories",
    response_model=BackendResponseEntity[ScanRepositoriesResponse],
    status_code=201,
)
def scan_repositories(
    command: ScanRepositoriesCommand = Depends(get_scan_repositories_command),
) -> JSONResponse | BackendResponseEntity[ScanRepositoriesResponse]:
    result: ResultSet[ScanRepositoriesResponse] = command.run()
    if not result.ok:
        status = StatusDTO.from_status(BCStatusEnum.INTERNAL_ERROR)
        envelope = BackendResponseEntity[ScanRepositoriesResponse].model_construct(
            body=None,
            status=status,
        )
        return JSONResponse(
            status_code=status.httpStatus,
            content=envelope.model_dump(mode="json"),
        )
    return BackendResponseEntity[ScanRepositoriesResponse](
        body=result.body,
        status=StatusDTO.from_status(BCStatusEnum.OK),
    )