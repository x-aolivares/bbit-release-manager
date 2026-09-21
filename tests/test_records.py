"""Tests del flujo de registro de prueba (endpoint -> sqlite)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from backend.commands.result import Failure
from backend.config import Settings
from backend.handler import create_app

def _client(tmp_path: Path) -> TestClient:
    settings = Settings(db_dir=tmp_path, max_workers=2, worker_mode="thread")
    return TestClient(create_app(settings))


def test_create_record_persists_and_returns_envelope(tmp_path: Path) -> None:
    client = _client(tmp_path)
    response = client.post("/api/records", json={"body": {"name": "x"}})

    assert response.status_code == 201
    data = response.json()
    assert data["body"]["name"] == "x"
    assert data["body"]["id"] == 1
    assert data["body"]["created_at"]
    assert data["status"]["code"] == "BBIT-000"
    assert data["status"]["description"] == "todos los procesos se ejecutaron correctamente"
    assert data["status"]["httpStatus"] == 200


def test_record_row_exists_in_sqlite(tmp_path: Path) -> None:
    _client(tmp_path).post("/api/records", json={"body": {"name": "prueba"}})

    conn = sqlite3.connect(tmp_path / "bbit_record.db")
    try:
        row = conn.execute(
            "SELECT id, name FROM records WHERE name = ?", ("prueba",)
        ).fetchone()
    finally:
        conn.close()

    assert row is not None
    assert row[0] == 1
    assert row[1] == "prueba"


def test_validation_fails_without_body(tmp_path: Path) -> None:
    client = _client(tmp_path)
    response = client.post("/api/records", json={})
    assert response.status_code == 422


def test_validation_fails_with_empty_inner_body(tmp_path: Path) -> None:
    client = _client(tmp_path)
    response = client.post("/api/records", json={"body": {}})
    assert response.status_code == 422


def test_domain_failure_returns_error_envelope(tmp_path: Path) -> None:
    client = _client(tmp_path)

    class _FailingCommand:
        def run(self, payload_body: object) -> Failure:
            return Failure("boom")

    client.app.state.container["save_record_command"] = _FailingCommand()

    response = client.post("/api/records", json={"body": {"name": "x"}})
    assert response.status_code == 500
    data = response.json()
    assert data["body"] is None
    assert data["status"]["code"] == "BBIT-999"
    assert data["status"]["description"] == "ocurrió un error inesperado en el servidor"
    assert data["status"]["httpStatus"] == 500