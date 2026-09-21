"""Tests del QueryService: lecturas con nombre calificado ``schema.tabla``."""

from __future__ import annotations

from pathlib import Path

from backend.src.adapter.repositories.records_repository import RecordsRepository
from backend.src.adapter.services.query_service import QueryService
from backend.library.config import Settings


def _seed_record(tmp_path: Path) -> None:
    repo = RecordsRepository(tmp_path / "bbit_record.db")
    repo.init()
    repo.insert_record("x")


def test_qualified_read_uses_schema_name(tmp_path: Path) -> None:
    """``bbit_record.records`` resuelve igual que ``main.records`` del repository."""
    _seed_record(tmp_path)
    query_service = QueryService(Settings(db_dir=tmp_path))

    with query_service.connect() as conn:
        row = conn.execute(
            'SELECT name FROM "bbit_record".records WHERE name = ?', ("x",)
        ).fetchone()

    assert row["name"] == "x"


def test_connect_attaches_all_schemas_by_their_name(tmp_path: Path) -> None:
    query_service = QueryService(Settings(db_dir=tmp_path))

    with query_service.connect() as conn:
        attached = {
            row["name"] for row in conn.execute("PRAGMA database_list")
        }

    assert {"bbit_record", "bbit_profile", "bbit_transaction", "bbit_authentication"} <= attached