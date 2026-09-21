"""Tests del mapeo automatico de filas sqlite a entities (EntityBase)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

import pytest

from backend.library.patterns import EntityBase


@dataclass(frozen=True, slots=True)
class RepoEntity(EntityBase):
    r_id: int
    r_name: str
    r_created_at: str


def _row(query: str) -> sqlite3.Row:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(query).fetchone()
    finally:
        conn.close()


def test_from_row_maps_by_column_name() -> None:
    entity = RepoEntity.from_row(
        _row("SELECT 1 AS r_id, 'orders-api' AS r_name, '2026-09-21' AS r_created_at")
    )

    assert entity == RepoEntity(r_id=1, r_name="orders-api", r_created_at="2026-09-21")


def test_from_row_ignores_extra_columns() -> None:
    entity = RepoEntity.from_row(
        _row("SELECT 1 AS r_id, 'x' AS r_name, 't' AS r_created_at, 'junk' AS other")
    )

    assert entity.r_id == 1
    assert not hasattr(entity, "other")


def test_from_row_missing_required_column_fails() -> None:
    with pytest.raises(TypeError):
        RepoEntity.from_row(_row("SELECT 1 AS r_id, 'x' AS r_name"))


def test_records_entity_inherits_mapper() -> None:
    from backend.src.adapter.entities.records_entity import RecordsEntity

    entity = RecordsEntity.from_row(
        _row("SELECT 7 AS id, 'z' AS name, 't' AS created_at")
    )

    assert entity == RecordsEntity(id=7, name="z", created_at="t")