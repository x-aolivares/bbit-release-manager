"""Endpoints del flujo de registro de prueba."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..commands.result import ResultSet
from ..commands.save_record_command import SaveRecordCommand
from ..deps import get_save_record_command
from ..models.record_model import RecordCreate, RecordOut

router = APIRouter(prefix="/api", tags=["records"])


@router.post("/records", response_model=RecordOut, status_code=201)
def create_record(
    payload: RecordCreate,
    command: SaveRecordCommand = Depends(get_save_record_command),
) -> RecordOut:
    result: ResultSet[RecordOut] = command.run(payload)
    if not result.ok:
        raise HTTPException(status_code=500, detail=result.error)
    return result.value
