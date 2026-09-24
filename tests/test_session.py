"""Tests del flujo de sesion: CRUD de credenciales en ``authentication``."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from backend.library.config import Settings
from backend.handler import create_app

_CREDENTIALS = [
    {"type": "bitbucket", "name": "TOKEN", "value": "token-bb-1"},
    {"type": "bitbucket", "name": "WORKSPACE", "value": "my_workspace"},
    {"type": "circleci", "name": "TOKEN", "value": "token-cci-1"},
]


def _client(tmp_path: Path) -> TestClient:
    settings = Settings(db_dir=tmp_path)
    return TestClient(create_app(settings))


def _auth_rows(tmp_path: Path) -> list[sqlite3.Row]:
    conn = sqlite3.connect(tmp_path / "bbit_authentication.db")
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            "SELECT auth_id, auth_uuid, auth_type, auth_name, auth_value, "
            "auth_details, auth_created_at, auth_updated_at FROM authentication"
        ).fetchall()
    finally:
        conn.close()


def test_create_credentials_persists_with_auth_prefix(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.post(
        "/api/session",
        json={"body": {"credentials": _CREDENTIALS, "options": "CREATE"}},
    )

    assert response.status_code == 201
    data = response.json()
    assert data["status"]["code"] == "BBIT-000"
    assert len(data["body"]["credentials"]) == 3

    rows = _auth_rows(tmp_path)
    assert len(rows) == 3
    row = rows[0]
    assert row["auth_type"] == "bitbucket"
    assert row["auth_name"] == "TOKEN"
    assert row["auth_value"] == "token-bb-1"
    assert len(row["auth_uuid"]) == 36
    assert row["auth_created_at"]
    assert row["auth_updated_at"]
    assert '"type": "bitbucket"' in row["auth_details"]


def test_create_duplicate_returns_error_envelope(tmp_path: Path) -> None:
    client = _client(tmp_path)
    client.post("/api/session", json={"body": {"credentials": _CREDENTIALS, "options": "CREATE"}})

    response = client.post(
        "/api/session",
        json={"body": {"credentials": [_CREDENTIALS[0]], "options": "CREATE"}},
    )

    assert response.status_code == 500
    data = response.json()
    assert data["body"] is None
    assert data["status"]["code"] == "BBIT-999"


def test_update_upserts_value_and_touches_updated_at(tmp_path: Path) -> None:
    client = _client(tmp_path)
    client.post("/api/session", json={"body": {"credentials": [_CREDENTIALS[0]], "options": "CREATE"}})
    before = _auth_rows(tmp_path)[0]

    response = client.post(
        "/api/session",
        json={
            "body": {
                "credentials": [{"type": "bitbucket", "name": "TOKEN", "value": "token-bb-nuevo"}],
                "options": "UPDATE",
            }
        },
    )

    assert response.status_code == 201
    assert response.json()["body"]["credentials"][0]["value"] == "token-bb-nuevo"
    rows = _auth_rows(tmp_path)
    assert len(rows) == 1
    assert rows[0]["auth_value"] == "token-bb-nuevo"
    assert rows[0]["auth_updated_at"] >= before["auth_updated_at"]


def test_update_creates_when_credential_does_not_exist(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.post(
        "/api/session",
        json={"body": {"credentials": [_CREDENTIALS[0]], "options": "UPDATE"}},
    )

    assert response.status_code == 201
    assert len(_auth_rows(tmp_path)) == 1


def test_delete_removes_credential(tmp_path: Path) -> None:
    client = _client(tmp_path)
    client.post("/api/session", json={"body": {"credentials": _CREDENTIALS, "options": "CREATE"}})

    response = client.post(
        "/api/session",
        json={"body": {"credentials": [_CREDENTIALS[0]], "options": "DELETE"}},
    )

    assert response.status_code == 201
    assert len(_auth_rows(tmp_path)) == 2


def test_delete_missing_returns_error_envelope(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.post(
        "/api/session",
        json={"body": {"credentials": [_CREDENTIALS[0]], "options": "DELETE"}},
    )

    assert response.status_code == 500
    assert response.json()["status"]["code"] == "BBIT-999"


def test_get_returns_stored_credentials(tmp_path: Path) -> None:
    client = _client(tmp_path)
    client.post("/api/session", json={"body": {"credentials": _CREDENTIALS, "options": "CREATE"}})

    response = client.get("/api/session")

    assert response.status_code == 200
    data = response.json()
    assert data["status"]["code"] == "BBIT-000"
    assert [c["name"] for c in data["body"]["credentials"]] == ["TOKEN", "WORKSPACE", "TOKEN"]
    assert data["body"]["credentials"][2]["type"] == "circleci"


def test_get_empty_returns_empty_credentials(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.get("/api/session")

    assert response.status_code == 200
    assert response.json()["body"]["credentials"] == []