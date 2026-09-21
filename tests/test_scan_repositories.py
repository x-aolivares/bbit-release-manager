"""Tests del flujo de scan de repositorios (cache por TTL) y la persistencia."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from backend.library.config import Settings
from backend.handler import create_app


class _FakeBitbucket:
    def __init__(self, repos: list[dict[str, Any]]) -> None:
        self.repos = repos
        self.calls = 0

    def fetch_repositories(self) -> list[dict[str, Any]]:
        self.calls += 1
        return self.repos


class _FailingBitbucket:
    def fetch_repositories(self) -> list[dict[str, Any]]:
        raise RuntimeError("bitbucket exploto")


def _patch_bitbucket(client: TestClient, fake: Any) -> None:
    command = client.app.state.container["scan_repositories_command"]
    command._logic._bitbucket = fake  # noqa: SLF001


def _client(tmp_path: Path) -> TestClient:
    settings = Settings(db_dir=tmp_path)
    return TestClient(create_app(settings))


def _set_updated_at(tmp_path: Path, minutes_ago: int) -> None:
    conn = sqlite3.connect(tmp_path / "bbit_release.db")
    try:
        conn.execute(
            "UPDATE request_history SET rh_updated_at = "
            "datetime('now', ?) WHERE rh_endpoint = '/api/scan-repositories'",
            (f"-{minutes_ago} minutes",),
        )
        conn.commit()
    finally:
        conn.close()


def test_first_scan_fetches_persists_and_touches_history(tmp_path: Path) -> None:
    client = _client(tmp_path)
    fake = _FakeBitbucket(
        [{"slug": "bbit-api-uno", "name": "API Uno"}, {"slug": "bbit-api-dos", "name": "API Dos"}]
    )
    _patch_bitbucket(client, fake)

    response = client.post("/api/scan-repositories")

    assert response.status_code == 201
    data = response.json()
    repos = data["body"]["repositories"]
    assert [r["slug"] for r in repos] == ["bbit-api-uno", "bbit-api-dos"]
    assert [r["name"] for r in repos] == ["API Uno", "API Dos"]
    assert data["status"]["code"] == "BBIT-000"
    assert fake.calls == 1

    conn = sqlite3.connect(tmp_path / "bbit_release.db")
    conn.row_factory = sqlite3.Row
    try:
        repos = conn.execute("SELECT r_name, r_details FROM repositories").fetchall()
        history = conn.execute(
            "SELECT rh_endpoint FROM request_history WHERE rh_endpoint = "
            "'/api/scan-repositories'"
        ).fetchone()
    finally:
        conn.close()

    assert len(repos) == 2
    assert repos[0]["r_name"] == "bbit-api-uno"
    assert "bbit-api-uno" in repos[0]["r_details"]
    assert history is not None


def test_fresh_scan_serves_cache_without_hitting_bitbucket(tmp_path: Path) -> None:
    client = _client(tmp_path)
    fake = _FakeBitbucket([{"slug": "bbit-api-uno"}])
    _patch_bitbucket(client, fake)

    first = client.post("/api/scan-repositories").json()
    second = client.post("/api/scan-repositories").json()

    assert fake.calls == 1
    assert first["body"] == second["body"]


def test_stale_scan_refetches_from_bitbucket(tmp_path: Path) -> None:
    client = _client(tmp_path)
    fake = _FakeBitbucket([{"slug": "bbit-api-uno"}])
    _patch_bitbucket(client, fake)

    client.post("/api/scan-repositories")
    assert fake.calls == 1

    _set_updated_at(tmp_path, minutes_ago=60)
    client.post("/api/scan-repositories")

    assert fake.calls == 2

    conn = sqlite3.connect(tmp_path / "bbit_release.db")
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT rh_updated_at FROM request_history "
            "WHERE rh_endpoint = '/api/scan-repositories'"
        ).fetchone()
    finally:
        conn.close()
    assert _minutes_since(row["rh_updated_at"]) < 1


def _minutes_since(updated_at: str) -> float:
    from datetime import datetime, timezone

    last = datetime.strptime(updated_at, "%Y-%m-%d %H:%M:%S")
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    return (now_utc - last).total_seconds() / 60


def test_refresh_preserves_enriched_details(tmp_path: Path) -> None:
    client = _client(tmp_path)
    fake = _FakeBitbucket([{"slug": "bbit-api-uno"}])
    _patch_bitbucket(client, fake)

    client.post("/api/scan-repositories")

    enriched = {
        "name": "API Uno",
        "slug": "bbit-api-uno",
        "tags": ["uat-1201"],
        "sources": [
            {
                "name": "release/REP-325073",
                "tags": [],
                "cci": [],
                "prs": [{"title": "Fix bug", "target": "master", "url": "http://x", "status": "OPEN"}],
            }
        ],
    }
    conn = sqlite3.connect(tmp_path / "bbit_release.db")
    try:
        conn.execute(
            "UPDATE repositories SET r_details = ? WHERE r_name = 'bbit-api-uno'",
            (json.dumps(enriched),),
        )
        conn.commit()
    finally:
        conn.close()

    _set_updated_at(tmp_path, minutes_ago=60)
    data = client.post("/api/scan-repositories").json()

    repo = data["body"]["repositories"][0]
    assert repo["slug"] == "bbit-api-uno"
    assert repo["name"] == "API Uno"
    assert repo["tags"] == ["uat-1201"]
    assert repo["sources"][0]["name"] == "release/REP-325073"


def test_bitbucket_failure_returns_error_envelope(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _patch_bitbucket(client, _FailingBitbucket())

    response = client.post("/api/scan-repositories")

    assert response.status_code == 500
    data = response.json()
    assert data["body"] is None
    assert data["status"]["code"] == "BBIT-999"
    assert data["status"]["httpStatus"] == 500