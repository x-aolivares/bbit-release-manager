from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from bbit_release._version import read_version
from bbit_release.web import session as session_mod
from bbit_release.web.api import repos as repos_mod
from bbit_release.web.main import FRONTEND_DIST, app
from bbit_release.web.session import destroy_session

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
    monkeypatch.setattr("bbit_release.web.api.repos.Config", FakeConfig)


@pytest.fixture(autouse=True)
def _clean_sessions():
    for sid in list(session_mod._sessions):
        destroy_session(sid)
    yield
    for sid in list(session_mod._sessions):
        destroy_session(sid)


@pytest.fixture(autouse=True)
def _clean_master_cache():
    repos_mod._MASTER_CACHE.clear()
    repos_mod._REPO_CACHE.clear()


def test_health_ok():
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["version"] == read_version()
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
            from bbit_release.bitbucket.client import BitbucketAuthError
            raise BitbucketAuthError("bad")
        def close(self):
            pass

    monkeypatch.setattr("bbit_release.web.session.BitbucketClient", BadClient)
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
    monkeypatch.setattr("bbit_release.web.session.BitbucketClient", StubClient)
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
            from bbit_release.circleci.client import CircleCiAuthError
            raise CircleCiAuthError("no")
        def close(self):
            pass

    monkeypatch.setattr("bbit_release.web.api.repos.CircleCiClient", BadCi)
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
        def repos_with_branch(self, origin, prefixes=None):
            return [SimpleNamespace(slug="r1", name="R1", workspace="ws", default_branch="master")]
        def commit_for_branch(self, repo, branch):
            return "abc123"
        def commits_behind(self, repo, branch, base):
            return 2
        def has_commits_ahead(self, repo, branch, base):
            return False
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

    monkeypatch.setattr("bbit_release.web.session.BitbucketClient", StubClient)
    monkeypatch.setattr("bbit_release.web.api.repos._circleci", lambda: None)

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
    assert body["repos"][0]["no_changes"] is True
    assert body["stats"]["repos"] == 1
    assert body["stats"]["synced"] == 0
    assert body["repos"][0]["pr"]["exists"] is False
    assert body["repos"][0]["deploys"] == {"uat": None}
    assert body["repos"][0]["match_tag"] == {"uat": None}
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
        def repos_with_branch(self, origin, prefixes=None):
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

    monkeypatch.setattr("bbit_release.web.session.BitbucketClient", StubClient)
    monkeypatch.setattr("bbit_release.web.api.repos._circleci", lambda: None)
    client.post("/api/session", json={"workspace": "ws", "token": "tok"})

    body = client.get("/api/scan", params={"origin": "release/x", "prefixes": "uat"}).json()
    assert body["repos"][0]["commit"] == "abc123"
    assert commit_calls == []  # el hash vino del PR, no de GET /commits
    assert body["stats"]["with_pr"] == 1
    assert body["repos"][0]["deploys"] == {"uat": None}
    assert body["repos"][0]["match_tag"] == {"uat": None}


def test_scan_deploys_from_tag(monkeypatch):
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
        def repos_with_branch(self, origin, prefixes=None):
            return [SimpleNamespace(slug="r1", name="R1", workspace="ws", default_branch="master")]
        def commit_for_branch(self, repo, branch):
            return "abc123"
        def commits_behind(self, repo, branch, base):
            return 0
        def has_commits_ahead(self, repo, branch, base):
            return True
        def tags_on_commit(self, repo, commit):
            return [{"name": "uat-7", "date": "x"}, {"name": "v1", "date": "x"}]
        def find_pr(self, repo, origin, destination):
            return None
        def branch_url(self, repo, branch):
            return "http://atlassian/branch"

    class StubCi:
        vcs = "bb"
        def deploys_for_tags(self, repo, tags):
            return {"uat-7": SimpleNamespace(workflow="deploy-uat", status="success", created_at="x", url="http://cci/7")}
        def deploy_for_tag(self, repo, tag, commit, prefix):
            if tag == "uat-7" and commit == "abc123" and prefix == "uat":
                return SimpleNamespace(workflow="deploy-uat", status="success", created_at="x", url="http://cci/7")
            return None
        def project_id(self, repo):
            return "9beb07c8-cc3b-4da1-8bc4-e9121667fbb7"

    monkeypatch.setattr("bbit_release.web.session.BitbucketClient", StubClient)
    monkeypatch.setattr("bbit_release.web.api.repos._circleci", lambda: StubCi())
    client.post("/api/session", json={"workspace": "ws", "token": "tok"})

    body = client.get("/api/scan", params={"origin": "release/x", "prefixes": "uat,stgp"}).json()
    repo = body["repos"][0]
    assert repo["match_tag"] == {"uat": "uat-7", "stgp": None}
    assert repo["deploys"]["uat"] == {"workflow": "deploy-uat", "status": "success", "created_at": "x", "url": "http://cci/7"}
    assert repo["deploys"]["stgp"] is None
    assert repo["ci_project"] == "9beb07c8-cc3b-4da1-8bc4-e9121667fbb7"
    assert repo["ci_vcs"] == "bb"
    assert body["stats"]["prod"] == 0


def test_scan_resolves_full_hash_with_pr(monkeypatch):
    calls = []

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
        def repos_with_branch(self, origin, prefixes=None):
            return [SimpleNamespace(slug="r1", name="R1", workspace="ws", default_branch="master")]
        def commit_for_branch(self, repo, branch):
            calls.append(branch)
            return "abc123456789000000000000000000000000000000"
        def commits_behind(self, repo, branch, base):
            return 0
        def tags_on_commit(self, repo, commit):
            return [{"name": "uat-7", "date": "x"}]
        def find_pr(self, repo, origin, destination):
            return {"id": 1, "title": "T", "url": "u", "state": "OPEN", "source_commit": "abc123456789"}
        def branch_url(self, repo, branch):
            return "http://atlassian/branch"

    class StubCi:
        vcs = "bb"
        def deploys_for_tags(self, repo, tags):
            return {}
        def deploy_for_tag(self, repo, tag, commit, prefix):
            assert commit == "abc123456789000000000000000000000000000000"
            return SimpleNamespace(workflow="deploy-uat", status="success", created_at="x", url="http://cci/7")
        def project_id(self, repo):
            return None

    monkeypatch.setattr("bbit_release.web.session.BitbucketClient", StubClient)
    monkeypatch.setattr("bbit_release.web.api.repos._circleci", lambda: StubCi())
    client.post("/api/session", json={"workspace": "ws", "token": "tok"})

    body = client.get("/api/scan", params={"origin": "release/x", "prefixes": "uat"}).json()
    assert body["repos"][0]["deploys"]["uat"]["status"] == "success"
    assert calls.count("release/x") == 1  # hash completo resuelto una sola vez


def test_generate_tags(monkeypatch):
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
        def repos_with_branch(self, origin, prefixes=None):
            return [SimpleNamespace(slug="r1", name="R1", workspace="ws", default_branch="master")]
        def find_pr(self, repo, origin, destination):
            return None
        def commit_for_branch(self, repo, branch):
            return "abc123"
        def tag_exists(self, slug, name):
            return name == "stgp-7"
        def create_tag(self, slug, name, commit):
            created.append((slug, name, commit))

    class StubCi:
        def pipeline_id_for_commit(self, repo, branch, commit):
            return 7

    created = []
    monkeypatch.setattr("bbit_release.web.session.BitbucketClient", StubClient)
    monkeypatch.setattr("bbit_release.web.api.repos._circleci", lambda: StubCi())
    client.post("/api/session", json={"workspace": "ws", "token": "tok"})

    body = client.post("/api/tags", params={"origin": "release/x", "prefixes": "uat,stgp"}).json()
    assert body["ok"] is True
    item = body["items"][0]
    assert item["pipeline_id"] == 7
    assert item["created"] == ["uat-7"]
    assert item["skipped"] == ["stgp-7"]
    assert created == [("r1", "uat-7", "abc123")]


def test_circleci_config_creates(monkeypatch):
    calls = []

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
        def repos_with_branch(self, origin, prefixes=None):
            return [SimpleNamespace(slug="r1", name="R1", workspace="ws", default_branch="master")]
        def commit_for_branch(self, repo, branch):
            return "head1"
        def raw_file(self, slug, ref, path):
            return None
        def upsert_file(self, slug, branch, path, content, message):
            calls.append((branch, path, "uat-deploy-on-tag" in content, message))
            return {"hash": "h1", "subject": "x"}

    monkeypatch.setattr("bbit_release.web.session.BitbucketClient", StubClient)
    client.post("/api/session", json={"workspace": "ws", "token": "tok"})

    body = client.post("/api/circleci-config", params={"origin": "release/x", "prefixes": "uat"}).json()
    assert body["ok"] is True
    item = body["items"][0]
    assert item["created"] is True and item["updated"] is False
    assert item["envs"] == ["uat"]
    assert item["commit"] == "h1"
    assert calls == [("release/x", ".circleci/config.yml", True, "feat: workflows de tag con aprobacion (uat)")]


def test_circleci_config_skips_when_present(monkeypatch):
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
        def repos_with_branch(self, origin, prefixes=None):
            return [SimpleNamespace(slug="r1", name="R1", workspace="ws", default_branch="master")]
        def commit_for_branch(self, repo, branch):
            return "head1"
        def raw_file(self, slug, ref, path):
            return (
                "version: 2.1\n"
                "jobs:\n"
                "  deploy-uat:\n"
                "    docker:\n"
                "      - image: cimg/base:2024.05\n"
                "    steps:\n"
                "      - checkout\n"
                "workflows:\n"
                "  uat-deploy-on-tag:\n"
                "    jobs:\n"
                "      - approve:\n"
                "          type: approval\n"
                "          filters:\n"
                "            tags:\n"
                "              only: /^uat-[0-9]+$/\n"
                "            branches:\n"
                "              ignore: /.*/\n"
                "      - deploy-uat:\n"
                "          requires:\n"
                "            - approve\n"
                "          filters:\n"
                "            tags:\n"
                "              only: /^uat-[0-9]+$/\n"
                "            branches:\n"
                "              ignore: /.*/\n"
            )
        def upsert_file(self, *a, **k):
            raise AssertionError("no debería escribir cuando ya existe")

    monkeypatch.setattr("bbit_release.web.session.BitbucketClient", StubClient)
    client.post("/api/session", json={"workspace": "ws", "token": "tok"})

    body = client.post("/api/circleci-config", params={"origin": "release/x", "prefixes": "uat"}).json()
    assert body["items"][0]["skipped"] is True


def test_circleci_config_remigra_forma_triggers_invalida(monkeypatch):
    calls = []

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
        def repos_with_branch(self, origin, prefixes=None):
            return [SimpleNamespace(slug="r1", name="R1", workspace="ws", default_branch="master")]
        def commit_for_branch(self, repo, branch):
            return "head1"
        def raw_file(self, slug, ref, path):
            return (
                "version: 2.1\n"
                "workflows:\n"
                "  uat-deploy-on-tag:\n"
                "    triggers:\n"
                "      - tags:\n"
                "          only:\n"
                "            - /^uat-[0-9]+$/\n"
                "    jobs:\n"
                "      - approve:\n"
                "          type: approval\n"
            )
        def upsert_file(self, slug, branch, path, content, message):
            calls.append((branch, path, content, message))
            return {"hash": "h2", "subject": "x"}

    monkeypatch.setattr("bbit_release.web.session.BitbucketClient", StubClient)
    client.post("/api/session", json={"workspace": "ws", "token": "tok"})

    body = client.post("/api/circleci-config", params={"origin": "release/x", "prefixes": "uat"}).json()
    item = body["items"][0]
    assert item["created"] is False and item["updated"] is True
    assert item["envs"] == ["uat"]
    assert len(calls) == 1
    branch, path, content, _ = calls[0]
    assert branch == "release/x" and path == ".circleci/config.yml"
    assert "filters:" in content and "triggers:" not in content


def test_diff_skips_raw_without_ssm(monkeypatch):
    raw_calls = []

    plain_file = SimpleNamespace(path="app.py", status="modified", added_lines=("print('hola')",), removed_lines=())
    ssm_file = SimpleNamespace(
        path="config/x.yaml",
        status="modified",
        added_lines=("key: {{resolve:ssm:config/app/key}}",),
        removed_lines=(),
    )
    deleted_ssm = SimpleNamespace(
        path="gone.yaml",
        status="deleted",
        added_lines=(),
        removed_lines=("v: /config/gone",),
    )

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
        def list_repos(self, prefixes=None):
            return [SimpleNamespace(slug="r1", name="R1", workspace="ws", default_branch="master")]
        def repos_with_branch(self, origin, prefixes=None):
            return [SimpleNamespace(slug="r1", name="R1", workspace="ws", default_branch="master")]
        def diff(self, repo, destination, origin):
            return SimpleNamespace(files=[plain_file, ssm_file, deleted_ssm])
        def find_pr(self, repo, origin, destination):
            return None
        def commit_for_branch(self, repo, branch):
            return "headOrigin" if branch == "release/x" else "headDest"
        def list_files(self, repo, ref):
            return ["config/x.yaml", "gone.yaml"]
        def raw_file(self, repo, ref, path):
            raw_calls.append((repo, ref, path))
            if path == "gone.yaml":
                return None if ref == "headOrigin" else "v: {{resolve:ssm:/config/gone}}"
            if ref == "headOrigin":
                return "k: {{resolve:ssm:config/app/key}}"
            return "no ssm"

    monkeypatch.setattr("bbit_release.web.session.BitbucketClient", StubClient)
    monkeypatch.setattr("bbit_release.web.api.repos._circleci", lambda: None)
    client.post("/api/session", json={"workspace": "ws", "token": "tok"})

    body = client.get("/api/diff", params={"origin": "release/x", "destination": "master"}).json()
    assert "app.py" not in [c[2] for c in raw_calls]
    assert body["mode"] == "diff"
    assert body["params"] == [
        {"param": "/config/app/key", "arn": "", "tipo": "nuevo", "qa_value": None, "repos": ["r1"]},
    ]
    assert body["removed"] == [{"param": "/config/gone", "repos": ["r1"]}]
    assert body["repos"][0]["added"] == ["/config/app/key"]
    assert body["repos"][0]["removed"] == ["/config/gone"]


def test_diff_mode_all_lists_whole_repo(monkeypatch):
    seen = {"list": [], "raw": []}

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
        def list_repos(self, prefixes=None):
            return [SimpleNamespace(slug="r1", name="R1", workspace="ws", default_branch="master")]
        def repos_with_branch(self, origin, prefixes=None):
            return [SimpleNamespace(slug="r1", name="R1", workspace="ws", default_branch="master")]
        def list_files(self, repo, ref):
            seen["list"].append((repo, ref))
            return ["config/a.yaml", "data.sql", "logo.png"]
        def raw_file(self, repo, ref, path):
            seen["raw"].append((repo, ref, path))
            if path == "config/a.yaml" and ref == "headOrigin":
                return "k: {{resolve:ssm:/config/a/b}}"
            return None
        def find_pr(self, repo, origin, destination):
            return None
        def commit_for_branch(self, repo, branch):
            return "headOrigin" if branch == "release/x" else "headDest"

    monkeypatch.setattr("bbit_release.web.session.BitbucketClient", StubClient)
    monkeypatch.setattr("bbit_release.web.api.repos._circleci", lambda: None)
    client.post("/api/session", json={"workspace": "ws", "token": "tok"})

    body = client.get("/api/diff", params={"origin": "release/x", "mode": "all"}).json()
    assert body["mode"] == "all"
    assert seen["list"] == [("r1", "headOrigin"), ("r1", "headDest")]
    assert ("r1", "headOrigin", "logo.png") not in seen["raw"]
    assert body["params"] == [
        {"param": "/config/a/b", "arn": "", "tipo": "nuevo", "qa_value": None, "repos": ["r1"]},
    ]
    assert body["repos"][0]["added"] == ["/config/a/b"]


def test_diff_reclassifies_productivo_from_repo_without_branch(monkeypatch):
    """Un param en el release de r1 pero que ya es productivo en master de r2
    (que NO trae la rama origen) debe clasificarse `reutilizado`, no `nuevo`."""

    class StubClient:
        def __init__(self, ws, tok, **kw):
            self.workspace = ws
        def session(self):
            return (
                SimpleNamespace(uuid="x", name="WS", slug="ws", is_private=True),
                "Jane (@jane)",
                [
                    SimpleNamespace(slug="r1", name="R1", workspace="ws", default_branch="master"),
                    SimpleNamespace(slug="r2", name="R2", workspace="ws", default_branch="master"),
                ],
            )
        def close(self):
            pass
        def list_repos(self, prefixes=None):
            return [
                SimpleNamespace(slug="r1", name="R1", workspace="ws", default_branch="master"),
                SimpleNamespace(slug="r2", name="R2", workspace="ws", default_branch="master"),
            ]
        def repos_with_branch(self, origin, prefixes=None):
            return [SimpleNamespace(slug="r1", name="R1", workspace="ws", default_branch="master")]
        def diff(self, repo, destination, origin):
            f = SimpleNamespace(
                path="config/x.yaml", status="modified",
                added_lines=("k: {{resolve:ssm:/config/shared/secret}}",), removed_lines=(),
            )
            return SimpleNamespace(files=[f])
        def find_pr(self, repo, origin, destination):
            return None
        def commit_for_branch(self, repo, branch):
            return "headO" if branch == "release/x" else "headM"
        def list_files(self, repo, ref):
            return ["config/x.yaml"]
        def raw_file(self, repo, ref, path):
            if ref == "headO":
                return "k: {{resolve:ssm:/config/shared/secret}}"
            if repo == "r2":
                return "v: {{resolve:ssm:/config/shared/secret}}"
            return "no ssm"

    monkeypatch.setattr("bbit_release.web.session.BitbucketClient", StubClient)
    monkeypatch.setattr("bbit_release.web.api.repos._circleci", lambda: None)
    client.post("/api/session", json={"workspace": "ws", "token": "tok"})

    body = client.get("/api/diff", params={"origin": "release/x", "destination": "master"}).json()
    assert body["params"] == [
        {"param": "/config/shared/secret", "arn": "", "tipo": "reutilizado", "qa_value": None, "repos": ["r1"]},
    ]


def test_repos_cache_force_exclude(monkeypatch):
    calls = {"n": 0}

    class StubClient:
        def __init__(self, ws, tok, **kw):
            self.workspace = ws
        def session(self):
            return (
                SimpleNamespace(uuid="x", name="WS", slug="ws", is_private=True),
                "Jane (@jane)",
                [
                    SimpleNamespace(slug="orders-app", name="OA", workspace="ws", default_branch="master"),
                    SimpleNamespace(slug="pay-app", name="PA", workspace="ws", default_branch="master"),
                ],
            )
        def close(self):
            pass
        def repos_with_branch(self, origin, prefixes=None):
            calls["n"] += 1
            return [SimpleNamespace(slug="orders-app", name="OA", workspace="ws", default_branch="master")]

    monkeypatch.setattr("bbit_release.web.session.BitbucketClient", StubClient)
    client.post("/api/session", json={"workspace": "ws", "token": "tok"})

    first = client.get("/api/repos", params={"origin": "release/x"}).json()
    assert first["cached"] is False
    assert [i["slug"] for i in first["items"]] == ["orders-app"]

    second = client.get("/api/repos", params={"origin": "release/x"}).json()
    assert second["cached"] is True
    assert calls["n"] == 1  # no re-discovery

    forced = client.get("/api/repos", params={"origin": "release/x", "force": 1}).json()
    assert forced["cached"] is False
    assert calls["n"] == 2

    excl = client.get("/api/repos", params={"origin": "release/x", "exclude": "orders-app"}).json()
    assert excl["items"] == []


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
        def has_commits_ahead(self, repo, origin, dest):
            return True
        def create_pr(self, repo, origin, destination, title=None):
            seen["repo"], seen["title"] = repo, title
            return {"url": "http://pr/1", "title": title or "T", "state": "OPEN", "id": 1}

    monkeypatch.setattr("bbit_release.web.session.BitbucketClient", PClient)
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
        def repos_with_branch(self, origin, prefixes=None):
            return [SimpleNamespace(slug=f"r{i}", name=f"R{i}", workspace="ws", default_branch="master") for i in range(2)]
        def find_pr(self, repo, origin, dest):
            return None
        def has_commits_ahead(self, repo, origin, dest):
            return True
        def create_pr(self, repo, origin, dest, title=None):
            return {"url": "u", "title": title, "state": "OPEN", "id": 1}

    monkeypatch.setattr("bbit_release.web.session.BitbucketClient", PClient)
    client.post("/api/session", json={"workspace": "ws3", "token": "tok"})
    resp = client.post("/api/prs/create-missing", params={"origin": "release/x", "destination": "master", "title": "Titulo comun"})
    body = resp.json()
    assert body["ok"] is True
    assert body["title"] == "Titulo comun"
    assert body["created"] == ["r0", "r1"]


def test_create_pr_none_when_no_changes(monkeypatch):
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
        def has_commits_ahead(self, repo, origin, dest):
            return False
        def create_pr(self, repo, origin, dest, title=None):
            calls.append(repo)
            return {"url": "u", "title": title, "state": "OPEN", "id": 1}

    monkeypatch.setattr("bbit_release.web.session.BitbucketClient", PClient)
    client.post("/api/session", json={"workspace": "ws4", "token": "tok"})
    resp = client.post("/api/pr", params={"repo": "r1", "origin": "release/x", "destination": "master", "title": "T"})
    assert resp.status_code == 400
    body = resp.json()
    assert body["ok"] is False
    assert body["no_changes"] is True
    assert "no hay cambios" in body["error"]
    assert calls == []


def test_create_missing_skips_no_changes(monkeypatch):
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
        def repos_with_branch(self, origin, prefixes=None):
            return [SimpleNamespace(slug="r0", name="R0", workspace="ws", default_branch="master")]
        def find_pr(self, repo, origin, dest):
            return None
        def has_commits_ahead(self, repo, origin, dest):
            return False
        def create_pr(self, repo, origin, dest, title=None):
            calls.append(repo)
            return {"url": "u", "title": title, "state": "OPEN", "id": 1}

    monkeypatch.setattr("bbit_release.web.session.BitbucketClient", PClient)
    client.post("/api/session", json={"workspace": "ws5", "token": "tok"})
    resp = client.post("/api/prs/create-missing", params={"origin": "release/x", "destination": "master", "title": "T"})
    body = resp.json()
    assert body["ok"] is True
    assert body["created"] == []
    assert body["no_changes"] == ["r0"]
    assert calls == []


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
        def repos_with_branch(self, origin, prefixes=None):
            return [SimpleNamespace(slug=f"r{i}", name=f"R{i}", workspace="ws", default_branch="master") for i in range(3)]
        def find_pr(self, repo, origin, dest):
            titles = {"r0": "viejo", "r1": "Nuevo titulo", "r2": "nuevo titulo"}
            return {"id": 7, "title": titles[repo], "url": "u", "state": "OPEN"}
        def update_pr_title(self, repo, pr_id, title):
            return {"id": pr_id, "title": title, "url": "u", "state": "OPEN"}

    monkeypatch.setattr("bbit_release.web.session.BitbucketClient", PClient)
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

    monkeypatch.setattr("bbit_release.web.session.BitbucketClient", PClient)
    monkeypatch.setattr(FakeConfig, "remove_credentials", fake_remove)
    client.post("/api/session", json={"workspace": "ws5", "token": "tok"})
    resp = client.delete("/api/session", params={"delete_credentials": "1"})
    assert resp.status_code == 200
    assert resp.json()["delete_credentials"] is True
    assert calls == [1]

    status = client.get("/api/session").json()
    assert status["active"] is False


def test_session_reuse_without_stored(monkeypatch):
    monkeypatch.setattr("bbit_release.web.session.BitbucketClient", lambda ws, tok, **kw: (_ for _ in ()).throw(AssertionError("no debe instanciar")))
    resp = client.post("/api/session/reuse", json={})
    assert resp.status_code in (400, 401)


def test_spa_serves_build_when_present():
    expected = 200 if FRONTEND_DIST.is_dir() else 503
    resp = client.get("/")
    assert resp.status_code == expected
    api = client.get("/api/health")
    assert api.status_code == 200