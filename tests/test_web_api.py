from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src.web import session as session_mod
from src.web.main import FRONTEND_DIST, app
from src.web.session import destroy_session

client = TestClient(app)


class FakeConfig:
    bitbucket_token = ""
    workspace = ""
    circleci_token = ""
    circleci_vcs = "bb"
    circleci_org = ""
    deploy_prefixes = ["uat", "stgp", "prod"]
    ssm_prefixes = ["/config", "/common"]

    def save_tokens(self, **kw):
        return None

    def remove_credentials(self):
        return None


@pytest.fixture(autouse=True)
def _fake_config(monkeypatch):
    monkeypatch.setattr("src.web.api.repos.Config", FakeConfig)


@pytest.fixture(autouse=True)
def _clean_sessions():
    for sid in list(session_mod._sessions):
        destroy_session(sid)
    yield
    for sid in list(session_mod._sessions):
        destroy_session(sid)


def test_health_ok():
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["version"] == "0.5.1"
    assert body["connected"] is False


def test_repos_not_configured_by_default():
    resp = client.get("/api/repos")
    assert resp.status_code == 200
    body = resp.json()
    assert body["configured"] is False
    assert body["items"] == []


def test_session_requires_token():
    resp = client.post("/api/session", json={"workspace": "ws", "token": ""})
    assert resp.status_code == 400


def test_session_rejects_bad_token(monkeypatch):
    class BadClient:
        def __init__(self, ws, tok, **kw):
            pass
        def session(self):
            from src.bitbucket.client import BitbucketAuthError
            raise BitbucketAuthError("bad")
        def close(self):
            pass

    monkeypatch.setattr("src.web.session.BitbucketClient", BadClient)
    resp = client.post("/api/session", json={"workspace": "ws", "token": "bad"})
    assert resp.status_code == 401


def test_session_status_without_saved_tokens():
    resp = client.get("/api/session")
    assert resp.status_code == 200
    assert resp.json() == {"active": False, "needs_tokens": True, "stored": False}


def test_session_create_with_stub(monkeypatch):
    class StubClient:
        def __init__(self, ws, tok, **kw):
            self.workspace = ws
        def session(self):
            return (
                SimpleNamespace(uuid="a1b2", name="WS", slug="ws", is_private=True),
                "Jane (@jane)",
                [SimpleNamespace(slug="a", name="A", workspace="ws", default_branch="master")],
            )
        def close(self):
            pass

    saved = {}
    monkeypatch.setattr(FakeConfig, "save_tokens",
                        lambda self, **kw: saved.update(kw))
    monkeypatch.setattr("src.web.session.BitbucketClient", StubClient)
    resp = client.post("/api/session", json={"workspace": "ws", "token": "tok"})
    body = resp.json()
    assert body["ok"] is True
    assert body["identity"] == "Jane (@jane)"
    assert body["repo_count"] == 1
    assert body["stored"] is True
    assert saved["bitbucket_token"] == "tok"
    assert saved["workspace"] == "ws"

    status = client.get("/api/session").json()
    assert status["active"] is True
    assert status["identity"] == "Jane (@jane)"


def test_session_rejects_bad_circleci_token(monkeypatch):
    class BadCi:
        def __init__(self, *a, **k):
            pass
        def me(self):
            from src.circleci.client import CircleCiAuthError
            raise CircleCiAuthError("no")
        def close(self):
            pass

    monkeypatch.setattr("src.web.api.repos.CircleCiClient", BadCi)
    resp = client.post("/api/session", json={"workspace": "ws", "token": "t", "circleci_token": "bad"})
    assert resp.status_code == 401
    assert "CircleCI" in resp.json()["error"]


def test_openapi_exposed():
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    assert resp.json()["info"]["title"] == "BBit Release Manager"
    paths = set(resp.json()["paths"])
    assert "/api/repos" in paths
    assert "/api/session" in paths
    assert "/api/diff" in paths


def test_scan_returns_repos_with_pr_and_params(monkeypatch):
    class StubClient:
        def __init__(self, ws, tok, **kw):
            self.workspace = ws
        def session(self):
            return (
                SimpleNamespace(uuid="x", name="WS", slug="ws", is_private=True),
                "Jane (@jane)",
                [SimpleNamespace(slug="r1", name="R1", workspace="ws", default_branch="master")],
            )
        def close(self):
            pass
        def repos_with_branch(self, origin):
            return [SimpleNamespace(slug="r1", name="R1", workspace="ws", default_branch="master")]
        def commit_for_branch(self, repo, branch):
            return "abc123"
        def commits_behind(self, repo, branch, base):
            return 2
        def tags_on_commit(self, repo, commit):
            return [{"name": "v1", "date": "x"}]
        def find_pr(self, repo, origin, destination):
            return None
        def branch_url(self, repo, branch):
            return "http://atlassian/branch"
        def diff(self, repo, destination, origin):
            return SimpleNamespace(files=[])
        def raw_file(self, repo, ref, path):
            return None

    monkeypatch.setattr("src.web.session.BitbucketClient", StubClient)
    monkeypatch.setattr("src.web.api.repos._circleci", lambda: None)

    ok = client.post("/api/session", json={"workspace": "ws", "token": "tok"})
    assert ok.status_code == 200

    resp = client.get("/api/scan", params={"origin": "release/x", "destination": "master", "prefixes": "uat"})
    body = resp.json()
    assert resp.status_code == 200
    assert body["ci_configured"] is False
    assert body["prefixes"] == ["uat"]
    assert body["repos"][0]["slug"] == "r1"
    assert body["repos"][0]["commit"] == "abc123"
    assert body["repos"][0]["behind"] == 2
    assert body["stats"]["repos"] == 1
    assert body["stats"]["synced"] == 0
    assert body["repos"][0]["pr"]["exists"] is False
    assert body["repos"][0]["deploys"] == {}
    assert body["repos"][0]["tags"][0]["name"] == "v1"


def test_scan_requires_session():
    resp = client.get("/api/scan", params={"origin": "release/x"})
    assert resp.status_code == 401


def test_scan_reuses_pr_hash(monkeypatch):
    commit_calls = []

    class StubClient:
        def __init__(self, ws, tok, **kw):
            self.workspace = ws
        def session(self):
            return (
                SimpleNamespace(uuid="x", name="WS", slug="ws", is_private=True),
                "Jane (@jane)",
                [SimpleNamespace(slug="r1", name="R1", workspace="ws", default_branch="master")],
            )
        def close(self):
            pass
        def repos_with_branch(self, origin):
            return [SimpleNamespace(slug="r1", name="R1", workspace="ws", default_branch="master")]
        def commit_for_branch(self, repo, branch):
            commit_calls.append(branch)
            return "fromCommit"
        def commits_behind(self, repo, branch, base):
            return 1
        def tags_on_commit(self, repo, commit):
            return []
        def find_pr(self, repo, origin, destination):
            return {"id": 1, "title": "T", "url": "u", "state": "OPEN", "source_commit": "abc123"}
        def branch_url(self, repo, branch):
            return "http://atlassian/branch"

    monkeypatch.setattr("src.web.session.BitbucketClient", StubClient)
    monkeypatch.setattr("src.web.api.repos._circleci", lambda: None)
    client.post("/api/session", json={"workspace": "ws", "token": "tok"})

    body = client.get("/api/scan", params={"origin": "release/x"}).json()
    assert body["repos"][0]["commit"] == "abc123"
    assert commit_calls == []  # el hash vino del PR, no de GET /commits
    assert body["stats"]["with_pr"] == 1


def test_diff_skips_raw_without_ssm(monkeypatch):
    raw_calls = []

    plain_file = SimpleNamespace(path="app.py", status="modified", added_lines=("print('hola')",))
    ssm_file = SimpleNamespace(
        path="config/x.yaml",
        status="modified",
        added_lines=("key: {{resolve:ssm:config/app/key}}",),
    )
    deleted_ssm = SimpleNamespace(path="gone.yaml", status="deleted", added_lines=("{{resolve:ssm:config/old}}",))

    class StubClient:
        def __init__(self, ws, tok, **kw):
            self.workspace = ws
        def session(self):
            return (
                SimpleNamespace(uuid="x", name="WS", slug="ws", is_private=True),
                "Jane (@jane)",
                [SimpleNamespace(slug="r1", name="R1", workspace="ws", default_branch="master")],
            )
        def close(self):
            pass
        def repos_with_branch(self, origin):
            return [SimpleNamespace(slug="r1", name="R1", workspace="ws", default_branch="master")]
        def diff(self, repo, destination, origin):
            return SimpleNamespace(files=[plain_file, ssm_file, deleted_ssm])
        def find_pr(self, repo, origin, destination):
            return None
        def commit_for_branch(self, repo, branch):
            return "headOrigin" if branch == "release/x" else "headDest"
        def raw_file(self, repo, ref, path):
            raw_calls.append((repo, ref, path))
            return "k: {{resolve:ssm:config/app/key}}" if ref == "headOrigin" else "no ssm"

    monkeypatch.setattr("src.web.session.BitbucketClient", StubClient)
    monkeypatch.setattr("src.web.api.repos._circleci", lambda: None)
    client.post("/api/session", json={"workspace": "ws", "token": "tok"})

    body = client.get("/api/diff", params={"origin": "release/x", "destination": "master"}).json()
    assert [c[2] for c in raw_calls] == ["config/x.yaml", "config/x.yaml"]
    assert body["repos"][0]["new_params"] == [{"param": "config/app/key", "arn": ""}]
    assert body["params"] == [{"param": "config/app/key", "arn": "", "repos": ["r1"]}]
    assert "deleted" not in "".join(c[2] for c in raw_calls)


def test_create_pr_endpoint(monkeypatch):
    seen = {}

    class PClient:
        def __init__(self, ws, tok, **kw):
            self.workspace = ws
        def session(self):
            return (
                SimpleNamespace(uuid="x", name="WS", slug="ws", is_private=True),
                "Jane (@jane)",
                [],
            )
        def close(self):
            pass
        def create_pr(self, repo, origin, destination, title=None):
            seen["repo"], seen["title"] = repo, title
            return {"url": "http://pr/1", "title": title or "T", "state": "OPEN", "id": 1}

    monkeypatch.setattr("src.web.session.BitbucketClient", PClient)
    ok = client.post("/api/session", json={"workspace": "ws2", "token": "tok"})
    assert ok.status_code == 200
    resp = client.post("/api/pr", params={"repo": "r1", "origin": "release/x", "destination": "master", "title": "Titulo comun"})
    body = resp.json()
    assert body["ok"] is True
    assert body["pr"]["url"] == "http://pr/1"
    assert seen["title"] == "Titulo comun"


def test_create_missing_prs(monkeypatch):
    class PClient:
        def __init__(self, ws, tok, **kw):
            self.workspace = ws
        def session(self):
            return (
                SimpleNamespace(uuid="x", name="WS", slug="ws", is_private=True),
                "Jane (@jane)",
                [],
            )
        def close(self):
            pass
        def repos_with_branch(self, origin):
            return [SimpleNamespace(slug=f"r{i}", name=f"R{i}", workspace="ws", default_branch="master") for i in range(2)]
        def find_pr(self, repo, origin, dest):
            return None
        def create_pr(self, repo, origin, dest, title=None):
            return {"url": "u", "title": title, "state": "OPEN", "id": 1}

    monkeypatch.setattr("src.web.session.BitbucketClient", PClient)
    client.post("/api/session", json={"workspace": "ws3", "token": "tok"})
    resp = client.post("/api/prs/create-missing", params={"origin": "release/x", "destination": "master", "title": "Titulo comun"})
    body = resp.json()
    assert body["ok"] is True
    assert body["title"] == "Titulo comun"
    assert body["created"] == ["r0", "r1"]


def test_update_pr_titles(monkeypatch):
    class PClient:
        def __init__(self, ws, tok, **kw):
            self.workspace = ws
        def session(self):
            return (
                SimpleNamespace(uuid="x", name="WS", slug="ws", is_private=True),
                "Jane (@jane)",
                [],
            )
        def close(self):
            pass
        def repos_with_branch(self, origin):
            return [SimpleNamespace(slug=f"r{i}", name=f"R{i}", workspace="ws", default_branch="master") for i in range(3)]
        def find_pr(self, repo, origin, dest):
            titles = {"r0": "viejo", "r1": "Nuevo titulo", "r2": "nuevo titulo"}
            return {"id": 7, "title": titles[repo], "url": "u", "state": "OPEN"}
        def update_pr_title(self, repo, pr_id, title):
            return {"id": pr_id, "title": title, "url": "u", "state": "OPEN"}

    monkeypatch.setattr("src.web.session.BitbucketClient", PClient)
    client.post("/api/session", json={"workspace": "ws4", "token": "tok"})
    resp = client.post("/api/prs/update-titles", params={"origin": "release/x", "destination": "master", "title": "Nuevo titulo"})
    body = resp.json()
    assert body["ok"] is True
    assert body["updated"] == ["r0", "r2"]
    assert body["skipped"] == ["r1"]


def test_destroy_session_with_delete_credentials(monkeypatch):
    calls = []

    class PClient:
        def __init__(self, ws, tok, **kw):
            self.workspace = ws
        def session(self):
            return (
                SimpleNamespace(uuid="x", name="WS", slug="ws", is_private=True),
                "Jane (@jane)",
                [],
            )
        def close(self):
            pass

    def fake_remove(self):
        calls.append(1)
        return None

    monkeypatch.setattr("src.web.session.BitbucketClient", PClient)
    monkeypatch.setattr(FakeConfig, "remove_credentials", fake_remove)
    client.post("/api/session", json={"workspace": "ws5", "token": "tok"})
    resp = client.delete("/api/session", params={"delete_credentials": "1"})
    assert resp.status_code == 200
    assert resp.json()["delete_credentials"] is True
    assert calls == [1]

    status = client.get("/api/session").json()
    assert status["active"] is False


def test_session_reuse_without_stored(monkeypatch):
    monkeypatch.setattr("src.web.session.BitbucketClient", lambda ws, tok, **kw: (_ for _ in ()).throw(AssertionError("no debe instanciar")))
    resp = client.post("/api/session/reuse", json={})
    assert resp.status_code in (400, 401)


def test_spa_serves_build_when_present():
    expected = 200 if FRONTEND_DIST.is_dir() else 503
    resp = client.get("/")
    assert resp.status_code == expected
    api = client.get("/api/health")
    assert api.status_code == 200