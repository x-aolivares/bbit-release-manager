"""Tests del flujo de registro de prueba (endpoint -> sqlite)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from bbit_release.config import Settings
from bbit_release.handler import create_app

DB_PATH = Path("data/test.db")


def _client(tmp_path: Path) -> TestClient:
    settings = Settings(db_path=tmp_path / "test.db", max_workers=2, worker_mode="thread")
    return TestClient(create_app(settings))


def test_create_record_persists_and_returns_dto(tmp_path: Path) -> None:
    client = _client(tmp_path)
    response = client.post("/api/records", json={"name": "x"})

    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "x"
    assert data["id"] == 1
    assert data["created_at"]


def test_record_row_exists_in_sqlite(tmp_path: Path) -> None:
    _client(tmp_path).post("/api/records", json={"name": "prueba"})

    conn = sqlite3.connect(tmp_path / "test.db")
    try:
        row = conn.execute(
            "SELECT id, name FROM records WHERE name = ?", ("prueba",)
        ).fetchone()
    finally:
        conn.close()

    assert row is not None
    assert row[0] == 1
    assert row[1] == "prueba"


def test_validation_fails_without_name(tmp_path: Path) -> None:
    client = _client(tmp_path)
    response = client.post("/api/records", json={})
    assert response.status_code == 422