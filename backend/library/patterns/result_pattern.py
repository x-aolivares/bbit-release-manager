"""Patron ResultSet: resultado de orquestacion sin excepciones.

Encapsula ``ok`` mas el valor o el error. Los commands devuelven esto en
vez de propagar excepciones: el controlador decide el HTTP status segun
``ok``, manteniendo el cuerpo de los commands casi sin try/except.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class ResultSet(Generic[T]):
    ok: bool
    body: T | None = None
    error: str | None = None


class Success(ResultSet[T]):
    def __init__(self, body: T) -> None:
        super().__init__(ok=True, body=body)


class Failure(ResultSet[T]):
    def __init__(self, error: str) -> None:
        super().__init__(ok=False, error=error)
