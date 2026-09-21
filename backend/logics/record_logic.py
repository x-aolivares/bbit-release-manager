"""Logica de negocio para el flujo de registro de prueba."""

from __future__ import annotations

from ..adapter.entities.records_entity import RecordsEntity
from ..adapter.repositories.records_repository import RecordsRepository


class RecordLogic:
    def __init__(self, repository: RecordsRepository) -> None:
        self._repository = repository

    def save_record(self, name: str) -> RecordsEntity:
        record_id = self._repository.insert_record(name)
        entity = self._repository.get_record(record_id)
        if entity is None:
            raise RuntimeError(f"No se pudo recuperar el registro {record_id}")
        return entity