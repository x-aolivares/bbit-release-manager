"""DTOs del flujo de registro de prueba."""

from __future__ import annotations

from pydantic import BaseModel, Field


class RecordCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class RecordOut(BaseModel):
    id: int
    name: str
    created_at: str