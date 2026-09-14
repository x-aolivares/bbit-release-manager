from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from bbit_release._version import read_version
from bbit_release.cache import DEFAULT_AWS_REGION, get_cache, reset_cache
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
    project_prefixes: list[str] = []
    exclude_repos: list[str] = []
    aws_profile = ""
    aws_region = ""
    aws_localstack = False
    aws_endpoint_url = ""
    aws_access_key_id = ""
    aws_secret_access_key = ""
    aws_session_token = ""
    bitbucket_url = "https://bitbucket.org"
    client_alias = "local"
    client_id = "00000000-0000-0000-0000-000000000000"
    is_configured = False
    ssm_environments: dict = {}
    ssm_read_secrets = False
    git_clones_dir = ""

    @classmethod
    def for_client(cls, c_id):
        self = cls.__new__(cls)
        self.client_id = c_id
        return self

    @property
    def aws_env(self):
        return {}

    def save_tokens(self, **kw):
        return None

    def save_service(self, provider_name, env, expires_at=None):
        self.bitbucket_token = env.get("BITBUCKET_TOKEN") or self.bitbucket_token
        return len(self.bitbucket_token)

    def remove_credentials(self):
        return None

    def save_filters(self, project_prefixes: str = "", exclude_repos: str = ""):
        return None

    def clear_filters(self):
        return None

    def service_states(self):
        return {
            "bitbucket": {"stored": bool(self.bitbucket_token), "expires_at": None, "warning": None},
            "circleci": {"stored": bool(self.circleci_token), "expires_at": None, "warning": None},
            "aws": {"stored": bool(self.aws_profile), "expires_at": None, "warning": None},
        }


@pytest.fixture(autouse=True)
def _fake_config(monkeypatch):
    FakeConfig.ssm_prefixes = ["/config", "/common"]
    FakeConfig.project_prefixes = []
    FakeConfig.exclude_repos = []
    monkeypatch.setattr("bbit_release.web.api.repos.Config", FakeConfig)


@pytest.fixture(autouse=True)
def _clean_sessions():
    for sid in list(session_mod._sessions):
        destroy_session(sid)
    yield
    for sid in list(session_mod._sessions):
        destroy_session(sid)


@pytest.fixture(autouse=True)
def _clean_cache(tmp_path):
    from bbit_release.cache import get_cache
    reset_cache()
    cache = get_cache(tmp_path / f"cache_{id(tmp_path)}.db")
    yield cache
    cache.invalidate_all()
    reset_cache()


def test_health_ok():
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["version"] == read_version()
    assert body["connected"] is False


def test_flow_requires_session():
    resp = client.get("/api/flow", params={"origin": "release/x"})
    assert resp.status_code == 401


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
    body = resp.json()
    assert resp.status_code == 200
    assert body["active"] is False
    assert body["needs_tokens"] is True
    assert body["stored"] is False
    assert body["client_alias"] == "local"
    assert body["services"]["aws"]["stored"] is False


def test_session_create_with_stub(monkeypatch):
    class StubClient:
        def __init__(self, ws, tok, **kw):
            self.workspace = ws
        def session(self):
            return (
                SimpleNamespace(uuid="a1b2", name="WS", slug="ws", is_private=True),
                "Jane (@jane)",
            )
        def diff(self, repo, destination, origin):
            from types import SimpleNamespace
            return SimpleNamespace(files=[])
        def list_files(self, repo, ref, tree=""):
            return []
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
    assert body["repo_count"] == 0
    assert body["stored"] is True
    assert saved["bitbucket_token"] == "tok"
    assert saved["workspace"] == "ws"

    status = client.get("/api/session").json()
    assert status["active"] is True
    assert status["identity"] == "Jane (@jane)"


def test_session_register_with_alias_seeds_uuid(monkeypatch, tmp_path):
    """Registrar con alias crea cliente con uuid5 y persiste el alias."""
    from bbit_release.config import Config as RealConfig
    import uuid as _uuid

    monkeypatch.setenv("BBIT_CLIENT_MARKER", str(tmp_path / "client_id_marker"))
    monkeypatch.setattr(repos_mod, "Config", RealConfig)

    class StubClient:
        def __init__(self, ws, tok, **kw):
            self.workspace = ws
        def session(self):
            return (
                SimpleNamespace(uuid="a1b2", name="WS", slug="ws", is_private=True),
                "Jane (@jane)",
            )
        def diff(self, repo, destination, origin):
            from types import SimpleNamespace
            return SimpleNamespace(files=[])
        def list_files(self, repo, ref, tree=""):
            return []
        def close(self):
            pass

    monkeypatch.setattr("bbit_release.web.session.BitbucketClient", StubClient)
    resp = client.post("/api/session", json={
        "alias": "aolivares", "workspace": "bg-ti", "token": "tok-seed",
    })
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert resp.json()["client_alias"] == "aolivares"

    cfg = RealConfig()
    assert cfg.client_alias == "aolivares"
    assert _uuid.UUID(cfg.client_id).version == 5  # uuid5 por semilla
    saved_id = cfg.client_id

    # reentrar con el mismo alias (nuevo login, token distinto)
    # NO regenera el id: el cliente ya existe por alias UNIQUE
    client.post("/api/session", json={
        "alias": "aolivares", "workspace": "bg-ti", "token": "tok-renovado",
    })
    assert RealConfig().client_id == saved_id


def test_session_reuse_by_alias(monkeypatch, tmp_path):
    """Volver a iniciar sesión con SOLO el alias resuelve el cliente guardado."""
    from bbit_release.config import Config as RealConfig
    import uuid as _uuid

    monkeypatch.setenv("BBIT_CLIENT_MARKER", str(tmp_path / "client_id_alt"))
    monkeypatch.setattr(repos_mod, "Config", RealConfig)

    class StubClient:
        last_ws = None
        def __init__(self, ws, tok, **kw):
            self.workspace = ws
            StubClient.last_ws = ws
        def session(self):
            return (
                SimpleNamespace(uuid="a1b2", name="WS", slug="ws", is_private=True),
                "Jane (@jane)",
            )
        def diff(self, repo, destination, origin):
            from types import SimpleNamespace
            return SimpleNamespace(files=[])
        def list_files(self, repo, ref, tree=""):
            return []
        def close(self):
            pass

    monkeypatch.setattr("bbit_release.web.session.BitbucketClient", StubClient)

    # registrar "bg-ti" con alias
    r = client.post("/api/session", json={
        "alias": "aolivares", "workspace": "bg-ti", "token": "tok",
    })
    assert r.status_code == 200

    # desconectar para simular reinicio (drops active session state global)
    # reuse con alias
    client.delete("/api/session")
    reuse = client.post("/api/session/reuse", json={"alias": "aolivares"})
    assert reuse.status_code == 200
    assert reuse.json()["ok"] is True
    assert StubClient.last_ws == "bg-ti"
    assert _uuid.UUID(RealConfig().client_id).version == 5


def test_session_reuse_unknown_alias_400(monkeypatch):
    resp = client.post("/api/session/reuse", json={"alias": "nadie"})
    assert resp.status_code == 400
    assert "Registrate" in resp.json()["error"]


def test_session_reuse_expired_401(monkeypatch, tmp_path):
    """Reuse con alias cuyas credenciales vencieron devuelve 401 con aviso."""
    from bbit_release.config import Config as RealConfig
    from bbit_release.bitbucket.client import BitbucketAuthError

    monkeypatch.setenv("BBIT_CLIENT_MARKER", str(tmp_path / "client_exp"))
    monkeypatch.setattr(repos_mod, "Config", RealConfig)

    class VencidoClient:
        def __init__(self, ws, tok, **kw):
            self.workspace = ws
        def session(self):
            raise BitbucketAuthError("expired")
        def close(self):
            pass

    # el primer registro falla (credenciales malas) -> no se guarda
    monkeypatch.setattr("bbit_release.web.session.BitbucketClient", VencidoClient)
    first = client.post("/api/session", json={
        "alias": "aolivares", "workspace": "bg-ti", "token": "tok",
    })
    assert first.status_code == 401

    # sin credenciales guardadas, reuse -> 400 pidiendo registrarse
    client.delete("/api/session")
    reuse = client.post("/api/session/reuse", json={"alias": "aolivares"})
    assert reuse.status_code == 400


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
    assert "/api/flow" in paths
    assert "/api/session" in paths
    assert "/api/client" in paths
    assert "/api/auth/{service}" in paths
    assert "/api/auth/{service}/validate" in paths


def test_client_status_after_login(monkeypatch, tmp_path):
    """GET /api/client con Config real: alias + estado de servicios."""
    from bbit_release.config import Config as RealConfig

    monkeypatch.setenv("BBIT_CLIENT_MARKER", str(tmp_path / "client_id"))
    monkeypatch.setattr(repos_mod, "Config", RealConfig)

    resp = client.get("/api/client")
    body = resp.json()
    assert resp.status_code == 200
    assert body["client"]["alias"] == "local"
    assert "id" not in body["client"]  # el uuid NO se expone al front
    assert body["configured"] is False
    assert body["services"]["bitbucket"]["stored"] is False
    assert body["settings"]["ssm_prefixes"] == ["/config", "/common"]
    assert body["auth"]["bitbucket"]["workspace"] == ""
    assert body["auth"]["aws"]["region"] == DEFAULT_AWS_REGION

    cash = get_cache().get_client_by_alias("local")
    assert cash is not None


def test_client_status_aws_never_exposes_direct_credentials(monkeypatch, tmp_path):
    """BBIT-15: client_status muestra endpoint pero NO access key/secret/token."""
    from bbit_release.config import Config as RealConfig

    monkeypatch.setenv("BBIT_CLIENT_MARKER", str(tmp_path / "client_id_aws"))
    monkeypatch.setattr(repos_mod, "Config", RealConfig)
    cfg = RealConfig()
    cfg.save_tokens(
        aws_endpoint_url="http://localhost:4566",
        aws_access_key_id="AK",
        aws_secret_access_key="SK",
        aws_session_token="TOK",
    )

    body = client.get("/api/client").json()
    aws_auth = body["auth"]["aws"]
    assert aws_auth["endpoint_url"] == "http://localhost:4566"
    assert "access_key_id" not in aws_auth
    assert "secret_access_key" not in aws_auth
    assert "session_token" not in aws_auth


def test_auth_validates_aws(monkeypatch):
    def fake_probe(profile, region, **kw):
        return (True, f"STS OK {profile}")
    monkeypatch.setattr(repos_mod, "_aws_probe", fake_probe)
    resp = client.post("/api/auth/aws/validate", json={"profile": "prod", "region": "us-east-1"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "service": "aws", "detail": "STS OK prod"}


def test_auth_validates_aws_with_direct_credentials(monkeypatch):
    """BBIT-15: validate acepta endpoint + credenciales directas y las pasa."""
    seen = {}

    def fake_probe(profile, region, **kw):
        seen.update(kw)
        return (True, f"STS OK {profile}")

    monkeypatch.setattr(repos_mod, "_aws_probe", fake_probe)
    resp = client.post("/api/auth/aws/validate", json={
        "region": "us-west-2",
        "localstack": True,
        "endpoint_url": "http://localhost:4566",
        "access_key_id": "AK",
        "secret_access_key": "SK",
        "session_token": "TOK",
    })
    assert resp.status_code == 200
    assert seen["endpoint_url"] == "http://localhost:4566"
    assert seen["access_key_id"] == "AK"
    assert seen["secret_access_key"] == "SK"
    assert seen["session_token"] == "TOK"


def test_auth_aws_ignores_endpoint_without_localstack(monkeypatch):
    """Sin flag localstack el endpoint no se aplica (default de seeders)."""
    seen = {}

    def fake_probe(profile, region, **kw):
        seen.update(kw)
        return (True, "STS OK prod")

    monkeypatch.setattr(repos_mod, "_aws_probe", fake_probe)
    resp = client.post("/api/auth/aws/validate", json={
        "profile": "prod",
        "region": "us-west-2",
        "endpoint_url": "http://localhost:4566",
    })
    assert resp.status_code == 200
    assert seen["endpoint_url"] == ""


def test_auth_aws_requires_profile_or_access_key(monkeypatch):
    resp = client.post("/api/auth/aws/validate", json={"region": "us-east-1"})
    assert resp.status_code == 200
    assert resp.json()["ok"] is False
    assert "profile o credenciales directas" in resp.json()["detail"]


def test_auth_unknown_service_404():
    resp = client.post("/api/auth/github/validate", json={})
    assert resp.status_code == 404


def test_auth_save_bitbucket_requires_creds():
    resp = client.post("/api/auth/bitbucket", json={"workspace": "ws"})
    assert resp.status_code == 401


def test_auth_save_service_persists(monkeypatch, tmp_path):
    """POST /api/auth/{service}: valida y guarda la credencial del cliente."""
    from bbit_release.config import Config as RealConfig

    monkeypatch.setenv("BBIT_CLIENT_MARKER", str(tmp_path / "client_id"))
    monkeypatch.setattr(repos_mod, "Config", RealConfig)
    monkeypatch.setattr(
        repos_mod, "_bb_probe",
        lambda ws, tok, url: (True, "Jane (@jane)"),
    )
    resp = client.post(
        "/api/auth/bitbucket",
        json={"workspace": "ws", "token": "tok", "username": "u"},
    )
    assert resp.status_code == 200
    assert resp.json()["stored"] is True

    cfg = RealConfig()
    assert cfg.bitbucket_token == "tok"
    saved = get_cache().get_authentication(cfg.client_id, "Bitbucket")
    assert saved["env"]["BITBUCKET_WORKSPACE"] == "ws"
    assert saved["env"]["BITBUCKET_USERNAME"] == "u"


def test_auth_save_requires_valid_credentials(monkeypatch):
    monkeypatch.setattr(repos_mod, "_bb_probe", lambda ws, tok, url: (False, "invalid"))
    resp = client.post("/api/auth/bitbucket", json={"workspace": "ws", "token": "malo"})
    assert resp.status_code == 401


def test_scan_returns_repos_with_pr_and_params(monkeypatch):
    class StubClient:
        def __init__(self, ws, tok, **kw):
            self.workspace = ws
        def session(self):
            return (
                SimpleNamespace(uuid="x", name="WS", slug="ws", is_private=True),
                "Jane (@jane)",
            )
        def close(self):
            pass
        def repos_with_branch(self, origin, prefixes=None):
            return [SimpleNamespace(slug="r1", name="R1", workspace="ws", default_branch="master")]
        def commit_for_branch(self, repo, branch, resolved=""):
            return "abc123"
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
        def list_files(self, repo, ref, tree=""):
            return []
        def raw_file(self, repo, ref, path):
            return None

    monkeypatch.setattr("bbit_release.web.session.BitbucketClient", StubClient)
    monkeypatch.setattr("bbit_release.web.api.repos._circleci", lambda: None)

    ok = client.post("/api/session", json={"workspace": "ws", "token": "tok"})
    assert ok.status_code == 200

    resp = client.get("/api/flow", params={"origin": "release/x", "destination": "master", "prefixes": "uat"})
    body = resp.json()
    assert resp.status_code == 200
    assert body["scan"]["ci_configured"] is False
    assert body["scan"]["prefixes"] == ["uat"]
    assert body["scan"]["repos"][0]["slug"] == "r1"
    assert body["scan"]["repos"][0]["commit"] == "abc123"
    assert "behind" not in body["scan"]["repos"][0]
    assert body["scan"]["repos"][0]["no_changes"] is True
    assert body["scan"]["stats"]["repos"] == 1
    assert "synced" not in body["scan"]["stats"]
    assert body["scan"]["repos"][0]["pr"]["exists"] is False
    assert body["scan"]["repos"][0]["deploys"] == {"uat": None}
    assert body["scan"]["repos"][0]["match_tag"] == {"uat": None}
    assert body["scan"]["repos"][0]["tags"][0]["name"] == "v1"


def test_scan_repo_42_slice_vivo_se_re_resuelve_sin_reescanear(monkeypatch, tmp_path):
    from bbit_release.cache import get_cache
    from bbit_release.circleci.client import DeployJob

    calls = {"scan_commit": 0, "tags": 0, "deploy_for_tag": 0}
    deploy_ready = {"ok": False}
    clock = {"t": 1000.0}
    monkeypatch.setattr("bbit_release.cache.time.time", lambda: clock["t"])

    class StubClient:
        def __init__(self, ws, tok, **kw):
            self.workspace = ws
        def session(self):
            return (
                SimpleNamespace(uuid="x", name="WS", slug="ws", is_private=True),
                "Jane (@jane)",
            )
        def close(self):
            pass
        def repos_with_branch(self, origin, prefixes=None):
            return [SimpleNamespace(slug="r1", name="R1", workspace="ws", default_branch="master")]
        def commit_for_branch(self, repo, branch, resolved=""):
            if branch == "release/x":
                calls["scan_commit"] += 1
            return "abc123"
        def has_commits_ahead(self, repo, branch, base):
            return True
        def tags_on_commit(self, repo, commit):
            calls["tags"] += 1
            return [{"name": "uat-7", "date": "x"}]
        def find_pr(self, repo, origin, destination):
            return None
        def branch_url(self, repo, branch):
            return "http://atlassian/branch"
        def diff(self, repo, destination, origin):
            return SimpleNamespace(files=[])
        def list_files(self, repo, ref, tree=""):
            return []
        def raw_file(self, repo, ref, path):
            return None

    class StubCi:
        vcs = "bb"
        def deploys_for_tags(self, repo, tags):
            return {t: None for t in tags}
        def project_id(self, repo):
            return "proj-1"
        def deploy_for_tag(self, repo, tag, commit, env):
            calls["deploy_for_tag"] += 1
            if not deploy_ready["ok"]:
                return None
            return DeployJob(
                workflow="deploy-uat", pipeline_id="p1", pipeline_number=7,
                status="success", created_at="x", url="http://ci", job="deploy",
                approval="",
            )

    monkeypatch.setattr("bbit_release.web.session.BitbucketClient", StubClient)
    monkeypatch.setattr("bbit_release.web.api.repos._circleci", lambda: StubCi())
    client.post("/api/session", json={"workspace": "ws", "token": "tok"})

    def _scan():
        resp = client.get("/api/flow", params={"origin": "release/x", "destination": "master", "prefixes": "uat"})
        return resp.json()["scan"]["repos"][0]

    item1 = _scan()
    after1 = calls["scan_commit"]
    assert item1["commit"] == "abc123"
    assert item1["tags"][0]["name"] == "uat-7"
    assert calls["tags"] == 1
    assert item1["deploys"]["uat"] is None

    # Hit inmediato: ni commit ni tags se re-consultam; deploy sigue None.
    item2 = _scan()
    assert calls["scan_commit"] == after1
    assert calls["tags"] == 1
    assert item2["deploys"]["uat"] is None

    # Slice de CircleCI vence (TTL 120s); el item estático (TTL 1800s) sigue
    # vigente: tags/deploys se re-resuelven on-demand sin re-escancar Bitbucket.
    clock["t"] = 1160.0
    deploy_ready["ok"] = True
    item3 = _scan()
    assert calls["scan_commit"] == after1  # el estático no se re-escanea
    assert calls["tags"] == 2              # el slice vencido se re-resuelve
    assert item3["deploys"]["uat"]["status"] == "success"


def test_scan_requires_session():
    resp = client.get("/api/flow", params={"origin": "release/x"})
    assert resp.status_code == 401


def test_flow_failed_repo_is_marked_not_500(monkeypatch):
    from bbit_release.bitbucket.client import BitbucketError

    class StubClient:
        def __init__(self, ws, tok, **kw):
            self.workspace = ws
        def session(self):
            return (
                SimpleNamespace(uuid="x", name="WS", slug="ws", is_private=True),
                "Jane (@jane)",
            )
        def close(self):
            pass
        def repos_with_branch(self, origin, prefixes=None):
            return [SimpleNamespace(slug="r1", name="R1", workspace="ws", default_branch="master"),
                    SimpleNamespace(slug="r2", name="R2", workspace="ws", default_branch="master")]
        def commit_for_branch(self, repo, branch, resolved=""):
            return "abc123"
        def has_commits_ahead(self, repo, branch, base):
            return False
        def tags_on_commit(self, repo, commit):
            return []
        def find_pr(self, repo, origin, destination):
            return None
        def branch_url(self, repo, branch):
            return f"http://atlassian/{repo}/{branch}"
        def diff(self, repo, destination, origin):
            from types import SimpleNamespace
            return SimpleNamespace(files=[])
        def list_files(self, repo, ref, tree=""):
            return []
        def raw_file(self, repo, ref, path):
            return None

    original_repo_scan = repos_mod._repo_scan

    def _failing_repo_scan(client, ci, repo, origin, destination, clean, ctx=None, with_tags=True):
        if repo.slug == "r1":
            raise BitbucketError("repo r1 rompido")
        return original_repo_scan(client, ci, repo, origin, destination, clean, ctx, with_tags=with_tags)

    monkeypatch.setattr("bbit_release.web.session.BitbucketClient", StubClient)
    monkeypatch.setattr("bbit_release.web.api.repos._circleci", lambda: None)
    monkeypatch.setattr(repos_mod, "_repo_scan", _failing_repo_scan)
    client.post("/api/session", json={"workspace": "ws", "token": "tok"})

    body = client.get("/api/flow", params={"origin": "release/x", "prefixes": "uat"}).json()
    assert body["scan"]["stats"]["repos"] == 2
    r1 = next(r for r in body["scan"]["repos"] if r["slug"] == "r1")
    assert "repo r1 rompido" in r1["error"]
    assert "behind" not in r1
    assert r1["commit"] == ""
    assert r1["pr"]["exists"] is False
    r2 = next(r for r in body["scan"]["repos"] if r["slug"] == "r2")
    assert r2.get("error") is None
    assert "behind" not in r2


def test_flow_repos_param_limits_scan_retry(monkeypatch):
    from bbit_release.bitbucket.client import BitbucketError

    class StubClient:
        def __init__(self, ws, tok, **kw):
            self.workspace = ws
        def session(self):
            return (
                SimpleNamespace(uuid="x", name="WS", slug="ws", is_private=True),
                "Jane (@jane)",
            )
        def close(self):
            pass
        def repos_with_branch(self, origin, prefixes=None):
            return [SimpleNamespace(slug="r1", name="R1", workspace="ws", default_branch="master"),
                    SimpleNamespace(slug="r2", name="R2", workspace="ws", default_branch="master")]
        def commit_for_branch(self, repo, branch, resolved=""):
            return "abc123"
        def has_commits_ahead(self, repo, branch, base):
            return False
        def tags_on_commit(self, repo, commit):
            return []
        def find_pr(self, repo, origin, destination):
            return None
        def branch_url(self, repo, branch):
            return f"http://atlassian/{repo}/{branch}"
        def diff(self, repo, destination, origin):
            from types import SimpleNamespace
            return SimpleNamespace(files=[])
        def list_files(self, repo, ref, tree=""):
            return []
        def raw_file(self, repo, ref, path):
            return None

    original_repo_scan = repos_mod._repo_scan

    def _failing_repo_scan(client, ci, repo, origin, destination, clean, ctx=None, with_tags=True):
        if repo.slug == "r1":
            raise BitbucketError("repo r1 rompido")
        return original_repo_scan(client, ci, repo, origin, destination, clean, ctx, with_tags=with_tags)

    monkeypatch.setattr("bbit_release.web.session.BitbucketClient", StubClient)
    monkeypatch.setattr("bbit_release.web.api.repos._circleci", lambda: None)
    monkeypatch.setattr(repos_mod, "_repo_scan", _failing_repo_scan)
    client.post("/api/session", json={"workspace": "ws", "token": "tok"})

    # Reintento a demanda: se re-escanza SOLO r1, que vuelve a fallar; r2
    # no aparece en el payload porque el scan queda limitado a los fallidos.
    body = client.get("/api/flow", params={"origin": "release/x", "prefixes": "uat", "repos": "r1"}).json()
    assert len(body["scan"]["repos"]) == 1
    assert body["scan"]["repos"][0]["slug"] == "r1"
    assert "repo r1 rompido" in body["scan"]["repos"][0]["error"]


def test_scan_reuses_pr_hash(monkeypatch):
    commit_calls = []

    class StubClient:
        def __init__(self, ws, tok, **kw):
            self.workspace = ws
        def session(self):
            return (
                SimpleNamespace(uuid="x", name="WS", slug="ws", is_private=True),
                "Jane (@jane)",
            )
        def diff(self, repo, destination, origin):
            from types import SimpleNamespace
            return SimpleNamespace(files=[])
        def list_files(self, repo, ref, tree=""):
            return []
        def close(self):
            pass
        def repos_with_branch(self, origin, prefixes=None):
            return [SimpleNamespace(slug="r1", name="R1", workspace="ws", default_branch="master")]
        def commit_for_branch(self, repo, branch, resolved=""):
            commit_calls.append(branch)
            return "fromCommit"
        def tags_on_commit(self, repo, commit):
            return []
        def find_pr(self, repo, origin, destination):
            return {"id": 1, "title": "T", "url": "u", "state": "OPEN", "source_commit": "abc123"}
        def branch_url(self, repo, branch):
            return "http://atlassian/branch"

    monkeypatch.setattr("bbit_release.web.session.BitbucketClient", StubClient)
    monkeypatch.setattr("bbit_release.web.api.repos._circleci", lambda: None)
    client.post("/api/session", json={"workspace": "ws", "token": "tok"})

    body = client.get("/api/flow", params={"origin": "release/x", "prefixes": "uat"}).json()
    assert body["scan"]["repos"][0]["commit"] == "abc123"
    # el hash de la rama origen vino del PR, no de GET /commits/{origin}; el
    # único commit_for_branch es la resolución de master del lado del diff.
    assert commit_calls == ["master"]
    assert body["scan"]["stats"]["with_pr"] == 1
    assert body["scan"]["repos"][0]["deploys"] == {"uat": None}
    assert body["scan"]["repos"][0]["match_tag"] == {"uat": None}


def test_scan_deploys_from_tag(monkeypatch):
    class StubClient:
        def __init__(self, ws, tok, **kw):
            self.workspace = ws
        def session(self):
            return (
                SimpleNamespace(uuid="x", name="WS", slug="ws", is_private=True),
                "Jane (@jane)",
            )
        def diff(self, repo, destination, origin):
            from types import SimpleNamespace
            return SimpleNamespace(files=[])
        def list_files(self, repo, ref, tree=""):
            return []
        def close(self):
            pass
        def repos_with_branch(self, origin, prefixes=None):
            return [SimpleNamespace(slug="r1", name="R1", workspace="ws", default_branch="master")]
        def commit_for_branch(self, repo, branch, resolved=""):
            return "abc123"
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
            return {"uat-7": SimpleNamespace(workflow="deploy-uat", status="success", created_at="x", url="http://cci/7", job="deploy-uat", approval="success")}
        def deploy_for_tag(self, repo, tag, commit, prefix):
            if tag == "uat-7" and commit == "abc123" and prefix == "uat":
                return SimpleNamespace(workflow="deploy-uat", status="success", created_at="x", url="http://cci/7", job="deploy-uat", approval="success")
            return None
        def project_id(self, repo):
            return "9beb07c8-cc3b-4da1-8bc4-e9121667fbb7"

    monkeypatch.setattr("bbit_release.web.session.BitbucketClient", StubClient)
    monkeypatch.setattr("bbit_release.web.api.repos._circleci", lambda: StubCi())
    client.post("/api/session", json={"workspace": "ws", "token": "tok"})

    body = client.get("/api/flow", params={"origin": "release/x", "prefixes": "uat,stgp"}).json()
    repo = body["scan"]["repos"][0]
    assert repo["match_tag"] == {"uat": "uat-7", "stgp": None}
    assert repo["deploys"]["uat"] == {"workflow": "deploy-uat", "status": "success", "created_at": "x", "url": "http://cci/7", "job": "deploy-uat", "approval": "success"}
    assert repo["deploys"]["stgp"] is None
    assert repo["ci_project"] == "9beb07c8-cc3b-4da1-8bc4-e9121667fbb7"
    assert repo["ci_vcs"] == "bb"
    assert body["scan"]["stats"]["prod"] == 0


def test_scan_resolves_full_hash_with_pr(monkeypatch):
    calls = []

    class StubClient:
        def __init__(self, ws, tok, **kw):
            self.workspace = ws
        def session(self):
            return (
                SimpleNamespace(uuid="x", name="WS", slug="ws", is_private=True),
                "Jane (@jane)",
            )
        def diff(self, repo, destination, origin):
            from types import SimpleNamespace
            return SimpleNamespace(files=[])
        def list_files(self, repo, ref, tree=""):
            return []
        def close(self):
            pass
        def repos_with_branch(self, origin, prefixes=None):
            return [SimpleNamespace(slug="r1", name="R1", workspace="ws", default_branch="master")]
        def commit_for_branch(self, repo, branch, resolved=""):
            calls.append(branch)
            return "abc123456789000000000000000000000000000000"
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
            return SimpleNamespace(workflow="deploy-uat", status="success", created_at="x", url="http://cci/7", job="deploy-uat", approval="success")
        def project_id(self, repo):
            return None

    monkeypatch.setattr("bbit_release.web.session.BitbucketClient", StubClient)
    monkeypatch.setattr("bbit_release.web.api.repos._circleci", lambda: StubCi())
    client.post("/api/session", json={"workspace": "ws", "token": "tok"})

    body = client.get("/api/flow", params={"origin": "release/x", "prefixes": "uat"}).json()
    assert body["scan"]["repos"][0]["deploys"]["uat"]["status"] == "success"
    assert calls.count("release/x") == 1  # hash completo resuelto una sola vez


def test_generate_tags(monkeypatch):
    class StubClient:
        def __init__(self, ws, tok, **kw):
            self.workspace = ws
        def session(self):
            return (
                SimpleNamespace(uuid="x", name="WS", slug="ws", is_private=True),
                "Jane (@jane)",
            )
        def diff(self, repo, destination, origin):
            from types import SimpleNamespace
            return SimpleNamespace(files=[])
        def list_files(self, repo, ref, tree=""):
            return []
        def close(self):
            pass
        def repos_with_branch(self, origin, prefixes=None):
            return [SimpleNamespace(slug="r1", name="R1", workspace="ws", default_branch="master")]
        def find_pr(self, repo, origin, destination):
            return None
        def commit_for_branch(self, repo, branch, resolved=""):
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


def test_generate_tags_alimenta_resolved_branch(monkeypatch):
    calls = []

    class StubClient:
        def __init__(self, ws, tok, **kw):
            self.workspace = ws
        def session(self):
            return (
                SimpleNamespace(uuid="x", name="WS", slug="ws", is_private=True),
                "Jane (@jane)",
            )
        def close(self):
            pass
        def repos_with_branch(self, origin, prefixes=None):
            return [
                SimpleNamespace(slug="r1", name="R1", workspace="ws", default_branch="master",
                                resolved_branch="release/x-V2"),
                SimpleNamespace(slug="r2", name="R2", workspace="ws", default_branch="master",
                                resolved_branch="release/x"),
            ]
        def commit_for_branch(self, repo, branch, resolved=""):
            calls.append((repo, branch, resolved))
            return {"r1": "abc111", "r2": "abc222"}[repo]
        def tag_exists(self, slug, name):
            return False
        def create_tag(self, slug, name, commit):
            pass

    class StubCi:
        def pipeline_id_for_commit(self, repo, branch, commit):
            return {"r1": 1, "r2": 2}[repo]

    monkeypatch.setattr("bbit_release.web.session.BitbucketClient", StubClient)
    monkeypatch.setattr("bbit_release.web.api.repos._circleci", lambda: StubCi())
    client.post("/api/session", json={"workspace": "ws", "token": "tok"})

    body = client.post("/api/tags", params={"origin": "release/x", "prefixes": "uat"}).json()
    assert body["ok"] is True
    assert [i["repo"] for i in body["items"]] == ["r1", "r2"]
    assert set(calls) == {("r1", "release/x", "release/x-V2"), ("r2", "release/x", "release/x")}


def test_circleci_config_creates(monkeypatch):
    calls = []

    class StubClient:
        def __init__(self, ws, tok, **kw):
            self.workspace = ws
        def session(self):
            return (
                SimpleNamespace(uuid="x", name="WS", slug="ws", is_private=True),
                "Jane (@jane)",
            )
        def close(self):
            pass
        def repos_with_branch(self, origin, prefixes=None):
            return [SimpleNamespace(slug="r1", name="R1", workspace="ws", default_branch="master")]
        def commit_for_branch(self, repo, branch, resolved=""):
            return "head1"
        def diff(self, repo, destination, origin):
            from types import SimpleNamespace
            return SimpleNamespace(files=[])
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
            )
        def close(self):
            pass
        def repos_with_branch(self, origin, prefixes=None):
            return [SimpleNamespace(slug="r1", name="R1", workspace="ws", default_branch="master")]
        def commit_for_branch(self, repo, branch, resolved=""):
            return "head1"
        def diff(self, repo, destination, origin):
            from types import SimpleNamespace
            return SimpleNamespace(files=[])
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
            )
        def close(self):
            pass
        def repos_with_branch(self, origin, prefixes=None):
            return [SimpleNamespace(slug="r1", name="R1", workspace="ws", default_branch="master")]
        def commit_for_branch(self, repo, branch, resolved=""):
            return "head1"
        def diff(self, repo, destination, origin):
            from types import SimpleNamespace
            return SimpleNamespace(files=[])
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
            )
        def has_commits_ahead(self, repo, branch, base):
            return False
        def tags_on_commit(self, repo, commit):
            return []
        def branch_url(self, repo, branch):
            return "http://atlassian/branch"
        def close(self):
            pass
        def list_repos(self, prefixes=None):
            return [SimpleNamespace(slug="r1", name="R1", workspace="ws", default_branch="master")]
        def repos_with_branch(self, origin, prefixes=None, repos=None):
            return [SimpleNamespace(slug="r1", name="R1", workspace="ws", default_branch="master")]
        def diff(self, repo, destination, origin):
            return SimpleNamespace(files=[plain_file, ssm_file, deleted_ssm])
        def find_pr(self, repo, origin, destination):
            return None
        def commit_for_branch(self, repo, branch, resolved=""):
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

    body = client.get("/api/flow", params={"origin": "release/x", "destination": "master"}).json()
    assert "app.py" not in [c[2] for c in raw_calls]
    assert body["diff"]["mode"] == "diff"
    assert body["diff"]["params"] == [
        {"param": "/config/app/key", "arn": "", "tipo": "nuevo", "repos": ["r1"], "count": 1},
    ]
    assert body["diff"]["removed"] == [{"param": "/config/gone", "repos": ["r1"]}]
    assert body["diff"]["repos"][0]["added"] == ["/config/app/key"]
    assert body["diff"]["repos"][0]["removed"] == ["/config/gone"]


def test_diff_mode_all_lists_whole_repo(monkeypatch):
    seen = {"list": [], "raw": []}

    class StubClient:
        def __init__(self, ws, tok, **kw):
            self.workspace = ws
        def session(self):
            return (
                SimpleNamespace(uuid="x", name="WS", slug="ws", is_private=True),
                "Jane (@jane)",
            )
        def has_commits_ahead(self, repo, branch, base):
            return False
        def tags_on_commit(self, repo, commit):
            return []
        def branch_url(self, repo, branch):
            return "http://atlassian/branch"
        def close(self):
            pass
        def list_repos(self, prefixes=None):
            return [SimpleNamespace(slug="r1", name="R1", workspace="ws", default_branch="master")]
        def repos_with_branch(self, origin, prefixes=None, repos=None):
            return [SimpleNamespace(slug="r1", name="R1", workspace="ws", default_branch="master")]
        def diff(self, repo, destination, origin):
            from types import SimpleNamespace
            return SimpleNamespace(files=[])
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
        def commit_for_branch(self, repo, branch, resolved=""):
            return "headOrigin" if branch == "release/x" else "headDest"

    monkeypatch.setattr("bbit_release.web.session.BitbucketClient", StubClient)
    monkeypatch.setattr("bbit_release.web.api.repos._circleci", lambda: None)
    client.post("/api/session", json={"workspace": "ws", "token": "tok"})

    body = client.get("/api/flow", params={"origin": "release/x", "mode": "all"}).json()
    assert body["diff"]["mode"] == "all"
    assert seen["list"] == [("r1", "headOrigin"), ("r1", "headDest")]
    assert ("r1", "headOrigin", "logo.png") not in seen["raw"]
    assert body["diff"]["params"] == [
        {"param": "/config/a/b", "arn": "", "tipo": "nuevo", "repos": ["r1"], "count": 1},
    ]
    assert body["diff"]["repos"][0]["added"] == ["/config/a/b"]


def test_diff_resolve_master_lista_por_sha(monkeypatch):
    """El modo diff lista master por el SHA del head de la rama (list_files
    agrega el trailing slash en la raíz) y usa el mismo ref para raw_file."""
    list_refs, raw_refs = [], []

    class StubClient:
        def __init__(self, ws, tok, **kw):
            self.workspace = ws
        def session(self):
            return (
                SimpleNamespace(uuid="x", name="WS", slug="ws", is_private=True),
                "Jane (@jane)",
            )
        def has_commits_ahead(self, repo, branch, base):
            return False
        def tags_on_commit(self, repo, commit):
            return []
        def branch_url(self, repo, branch):
            return "http://atlassian/branch"
        def close(self):
            pass
        def list_repos(self, prefixes=None):
            return [SimpleNamespace(slug="r1", name="R1", workspace="ws", default_branch="master")]
        def repos_with_branch(self, origin, prefixes=None, repos=None):
            return [SimpleNamespace(slug="r1", name="R1", workspace="ws", default_branch="master")]
        def diff(self, repo, destination, origin):
            f = SimpleNamespace(
                path="config/x.yaml", status="modified",
                added_lines=("k: {{resolve:ssm:/config/app/key}}",), removed_lines=(),
            )
            return SimpleNamespace(files=[f])
        def find_pr(self, repo, origin, destination):
            return None
        def commit_for_branch(self, repo, branch, resolved=""):
            return "headOrigin" if branch == "release/x" else "headDest"
        def list_files(self, repo, ref):
            list_refs.append(ref)
            return ["config/x.yaml"]
        def raw_file(self, repo, ref, path):
            raw_refs.append(ref)
            return "k: {{resolve:ssm:/config/app/key}}" if ref == "headOrigin" else "no ssm"

    monkeypatch.setattr("bbit_release.web.session.BitbucketClient", StubClient)
    monkeypatch.setattr("bbit_release.web.api.repos._circleci", lambda: None)
    client.post("/api/session", json={"workspace": "ws", "token": "tok"})

    body = client.get("/api/flow", params={"origin": "release/x", "destination": "master"}).json()
    assert list_refs == ["headDest"]
    assert {"headOrigin", "headDest"} <= set(raw_refs)
    assert body["diff"]["params"] == [
        {"param": "/config/app/key", "arn": "", "tipo": "nuevo", "repos": ["r1"], "count": 1},
    ]


def test_diff_lee_params_del_snapshot_si_el_cliente_lo_soporta(monkeypatch):
    """El diff usa snapshot() cuando el cliente lo ofrece (en vez de
    list_files + raw_file por archivo): upstreams y master params se leen
    del tarball descargado una vez por (slug, ref)."""
    snapshot_calls, raw_calls = [], []

    class StubClient:
        def __init__(self, ws, tok, **kw):
            self.workspace = ws
        def session(self):
            return (
                SimpleNamespace(uuid="x", name="WS", slug="ws", is_private=True),
                "Jane (@jane)",
            )
        def has_commits_ahead(self, repo, branch, base):
            return False
        def tags_on_commit(self, repo, commit):
            return []
        def branch_url(self, repo, branch):
            return "http://atlassian/branch"
        def close(self):
            pass
        def list_repos(self, prefixes=None):
            return [SimpleNamespace(slug="r1", name="R1", workspace="ws", default_branch="master")]
        def repos_with_branch(self, origin, prefixes=None, repos=None):
            return [SimpleNamespace(slug="r1", name="R1", workspace="ws", default_branch="master")]
        def diff(self, repo, destination, origin):
            f = SimpleNamespace(
                path="config/x.yaml", status="modified",
                added_lines=("k: {{resolve:ssm:/config/app/key}}",), removed_lines=(),
            )
            return SimpleNamespace(files=[f])
        def find_pr(self, repo, origin, destination):
            return None
        def commit_for_branch(self, repo, branch, resolved=""):
            return "headOrigin" if branch == "release/x" else "headDest"
        def snapshot(self, slug, ref, force=False):
            snapshot_calls.append(ref)
            return {
                "config/x.yaml": (
                    "k: {{resolve:ssm:/config/app/key}}" if ref == "headOrigin" else "no ssm"
                ),
                "other/app.json": '{"k": "v"}',
            }
        def raw_file(self, repo, ref, path):
            raw_calls.append((ref, path))
            return None

    monkeypatch.setattr("bbit_release.web.session.BitbucketClient", StubClient)
    monkeypatch.setattr("bbit_release.web.api.repos._circleci", lambda: None)
    client.post("/api/session", json={"workspace": "ws", "token": "tok"})

    body = client.get("/api/flow", params={"origin": "release/x", "destination": "master"}).json()
    assert raw_calls == []
    assert set(snapshot_calls) == {"headOrigin", "headDest"}
    assert body["diff"]["params"] == [
        {"param": "/config/app/key", "arn": "", "tipo": "nuevo", "repos": ["r1"], "count": 1},
    ]


def test_diff_master_sin_params_se_cachea_vacio():
    """BBIT-45: ``_resolve_master`` cachea un master sin params SSM como
    conjunto vacío; el siguiente diff no re-baja el tarball de master.
    ``force=1`` sí re-resuelve."""
    from bbit_release.web.api import repos as repo_api

    snapshot_calls: list[str] = []

    class StubClient:
        def commit_for_branch(self, slug, branch):
            return "headDest"
        def snapshot(self, slug, ref, force=False):
            snapshot_calls.append(ref)
            return {
                "config/x.yaml": "no ssm",
                "other/app.json": '{"k": "v"}',
            }
        def list_files(self, slug, ref):
            return []
        def raw_file(self, slug, ref, path):
            return None

    cache = get_cache()
    repo = SimpleNamespace(slug="r1", name="R1", workspace="ws", default_branch="master")
    client = StubClient()

    res1 = repo_api._resolve_master(client, repo, "master", ["/config"], cache, {})
    assert res1 == set()
    assert snapshot_calls.count("headDest") == 1

    res2 = repo_api._resolve_master(client, repo, "master", ["/config"], cache, {})
    assert res2 == set()
    assert snapshot_calls.count("headDest") == 1  # reusa el vacío cacheado

    res3 = repo_api._resolve_master(client, repo, "master", ["/config"], cache, {}, force=True)
    assert res3 == set()
    assert snapshot_calls.count("headDest") == 2  # force re-resuelve


def test_diff_solo_resuelve_contra_repos_con_rama(monkeypatch):
    """El diff NO barre todos los repos del workspace: solo resuelve master
    params contra los repos que traen la rama origen (branch_repos).
    Un repo que NO trae la rama ya no influye en la clasificación."""

    list_calls = []

    class StubClient:
        def __init__(self, ws, tok, **kw):
            self.workspace = ws
        def session(self):
            return (
                SimpleNamespace(uuid="x", name="WS", slug="ws", is_private=True),
                "Jane (@jane)",
            )
        def has_commits_ahead(self, repo, branch, base):
            return False
        def tags_on_commit(self, repo, commit):
            return []
        def branch_url(self, repo, branch):
            return "http://atlassian/branch"
        def close(self):
            pass
        def list_repos(self, prefixes=None):
            list_calls.append(True)
            return [
                SimpleNamespace(slug="r1", name="R1", workspace="ws", default_branch="master"),
                SimpleNamespace(slug="r2", name="R2", workspace="ws", default_branch="master"),
            ]
        def repos_with_branch(self, origin, prefixes=None, repos=None):
            return [SimpleNamespace(slug="r1", name="R1", workspace="ws", default_branch="master")]
        def diff(self, repo, destination, origin):
            f = SimpleNamespace(
                path="config/x.yaml", status="modified",
                added_lines=("k: {{resolve:ssm:/config/shared/secret}}",), removed_lines=(),
            )
            return SimpleNamespace(files=[f])
        def find_pr(self, repo, origin, destination):
            return None
        def commit_for_branch(self, repo, branch, resolved=""):
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

    body = client.get("/api/flow", params={"origin": "release/x", "destination": "master"}).json()
    # La 1era consulta hace UN list_repos de discovery (cacheable), no vuelve a
    # barrer el workspace en consultas posteriores con la misma rama.
    assert list_calls == [True]
    # r1 master no tiene params → el param se considera 'nuevo' (r2 quedó fuera)
    assert body["diff"]["params"] == [
        {"param": "/config/shared/secret", "arn": "", "tipo": "nuevo", "repos": ["r1"], "count": 1},
    ]

    # 2da consulta con la misma rama: cache hits, no vuelve a list_repos
    client.get("/api/flow", params={"origin": "release/x", "destination": "master"}).json()
    assert list_calls == [True]


def test_diff_cache_key_incluye_ssm_prefixes(monkeypatch):
    """Cambiar SSM_PREFIXES produce otra key de cache: el diff cacheado con
    otros prefixes NO se reutiliza (evita resultado stale)."""
    class StubClient:
        def __init__(self, ws, tok, **kw):
            self.workspace = ws
        def session(self):
            return (
                SimpleNamespace(uuid="x", name="WS", slug="ws", is_private=True),
                "Jane (@jane)",
            )
        def has_commits_ahead(self, repo, branch, base):
            return False
        def tags_on_commit(self, repo, commit):
            return []
        def branch_url(self, repo, branch):
            return "http://atlassian/branch"
        def close(self):
            pass
        def list_repos(self, prefixes=None):
            return [SimpleNamespace(slug="r1", name="R1", workspace="ws", default_branch="master")]
        def repos_with_branch(self, origin, prefixes=None, repos=None):
            return [SimpleNamespace(slug="r1", name="R1", workspace="ws", default_branch="master")]
        def diff(self, repo, destination, origin):
            f = SimpleNamespace(
                path="config/x.yaml", status="modified",
                added_lines=("k: {{resolve:ssm:/config/app/key}}",),
                removed_lines=(),
            )
            return SimpleNamespace(files=[f])
        def find_pr(self, repo, origin, destination):
            return None
        def commit_for_branch(self, repo, branch, resolved=""):
            return "headO" if branch == "release/x" else "headM"
        def list_files(self, repo, ref):
            return ["config/x.yaml"]
        def raw_file(self, repo, ref, path):
            return "k: {{resolve:ssm:/config/app/key}}" if ref == "headO" else "no ssm"

    monkeypatch.setattr("bbit_release.web.session.BitbucketClient", StubClient)
    monkeypatch.setattr("bbit_release.web.api.repos._circleci", lambda: None)
    client.post("/api/session", json={"workspace": "ws", "token": "tok"})

    FakeConfig.ssm_prefixes = ["/config"]
    first = client.get("/api/flow", params={"origin": "release/x", "destination": "master"}).json()
    assert first["diff"]["params"] == [
        {"param": "/config/app/key", "arn": "", "tipo": "nuevo", "repos": ["r1"], "count": 1},
    ]

    FakeConfig.ssm_prefixes = ["/config", "/extra"]
    after = client.get("/api/flow", params={"origin": "release/x", "destination": "master"}).json()
    assert after["diff"]["params"] == first["diff"]["params"]  # ambos extraen el param que matchea con /config y /extra

    FakeConfig.ssm_prefixes = ["/other"]
    changed = client.get("/api/flow", params={"origin": "release/x", "destination": "master"}).json()
    assert changed["diff"]["params"] != first["diff"]["params"]  # sin prefijo que matchee, difiere (no cache stale)


def test_diff_reutilizado_cuando_path_esta_en_master_del_mismo_repo(monkeypatch):
    """Un path presente en release Y en master del MISMO repo pasa a 'reutilizado'
    (antes se descartaba por origin - dest por-repo)."""
    FakeConfig.ssm_prefixes = ["/config", "/common"]
    class StubClient:
        def __init__(self, ws, tok, **kw):
            self.workspace = ws
        def session(self):
            return (
                SimpleNamespace(uuid="x", name="WS", slug="ws", is_private=True),
                "Jane (@jane)",
            )
        def has_commits_ahead(self, repo, branch, base):
            return False
        def tags_on_commit(self, repo, commit):
            return []
        def branch_url(self, repo, branch):
            return "http://atlassian/branch"
        def close(self):
            pass
        def list_repos(self, prefixes=None):
            return [SimpleNamespace(slug="r1", name="R1", workspace="ws", default_branch="master")]
        def repos_with_branch(self, origin, prefixes=None, repos=None):
            return [SimpleNamespace(slug="r1", name="R1", workspace="ws", default_branch="master")]
        def diff(self, repo, destination, origin):
            f = SimpleNamespace(
                path="config/x.yaml", status="modified",
                added_lines=("k: {{resolve:ssm:/config/shared/secret}}",),
                removed_lines=(),
            )
            return SimpleNamespace(files=[f])
        def find_pr(self, repo, origin, destination):
            return None
        def commit_for_branch(self, repo, branch, resolved=""):
            return "headO" if branch == "release/x" else "headM"
        def list_files(self, repo, ref):
            return ["config/x.yaml"]
        def raw_file(self, repo, ref, path):
            if ref == "headO":
                return "k: {{resolve:ssm:/config/shared/secret}}"
            return "k: {{resolve:ssm:/config/shared/secret}}"

    monkeypatch.setattr("bbit_release.web.session.BitbucketClient", StubClient)
    monkeypatch.setattr("bbit_release.web.api.repos._circleci", lambda: None)
    client.post("/api/session", json={"workspace": "ws", "token": "tok"})

    body = client.get("/api/flow", params={"origin": "release/x", "destination": "master"}).json()
    # el path está en release y master → reutilizado (no 'nuevo', ni desaparecido)
    assert body["diff"]["params"] == [
        {"param": "/config/shared/secret", "arn": "", "tipo": "reutilizado", "repos": ["r1"], "count": 1},
    ]


def test_diff_reutilizado_y_count_multirepo(monkeypatch):
    """Un path en release de 2 repos (ambos con la rama) y también en master
    global → 'reutilizado' con count=2 y repos=[r1, r2]."""
    FakeConfig.ssm_prefixes = ["/config", "/common"]
    class StubClient:
        def __init__(self, ws, tok, **kw):
            self.workspace = ws
        def session(self):
            return (
                SimpleNamespace(uuid="x", name="WS", slug="ws", is_private=True),
                "Jane (@jane)",
            )
        def has_commits_ahead(self, repo, branch, base):
            return False
        def tags_on_commit(self, repo, commit):
            return []
        def branch_url(self, repo, branch):
            return "http://atlassian/branch"
        def close(self):
            pass
        def list_repos(self, prefixes=None):
            return [
                SimpleNamespace(slug="r1", name="R1", workspace="ws", default_branch="master"),
                SimpleNamespace(slug="r2", name="R2", workspace="ws", default_branch="master"),
            ]
        def repos_with_branch(self, origin, prefixes=None, repos=None):
            return [
                SimpleNamespace(slug="r1", name="R1", workspace="ws", default_branch="master"),
                SimpleNamespace(slug="r2", name="R2", workspace="ws", default_branch="master"),
            ]
        def diff(self, repo, destination, origin):
            f = SimpleNamespace(
                path="config/x.yaml", status="modified",
                added_lines=("k: {{resolve:ssm:/config/dup}}",),
                removed_lines=(),
            )
            return SimpleNamespace(files=[f])
        def find_pr(self, repo, origin, destination):
            return None
        def commit_for_branch(self, repo, branch, resolved=""):
            return "headO" if branch == "release/x" else "headM"
        def list_files(self, repo, ref):
            return ["config/x.yaml"]
        def raw_file(self, repo, ref, path):
            # en ambos repos el path está tanto en release como en master
            return "k: {{resolve:ssm:/config/dup}}"

    monkeypatch.setattr("bbit_release.web.session.BitbucketClient", StubClient)
    monkeypatch.setattr("bbit_release.web.api.repos._circleci", lambda: None)
    client.post("/api/session", json={"workspace": "ws", "token": "tok"})

    body = client.get("/api/flow", params={"origin": "release/x", "destination": "master"}).json()
    assert body["diff"]["params"] == [
        {"param": "/config/dup", "arn": "", "tipo": "reutilizado", "repos": ["r1", "r2"], "count": 2},
    ]


def test_diff_repos_only_master_no_aparecen(monkeypatch):
    """Un path que SOLO existe en master (ya productivo) no aparece en params."""
    FakeConfig.ssm_prefixes = ["/config", "/common"]
    class StubClient:
        def __init__(self, ws, tok, **kw):
            self.workspace = ws
        def session(self):
            return (
                SimpleNamespace(uuid="x", name="WS", slug="ws", is_private=True),
                "Jane (@jane)",
            )
        def has_commits_ahead(self, repo, branch, base):
            return False
        def tags_on_commit(self, repo, commit):
            return []
        def branch_url(self, repo, branch):
            return "http://atlassian/branch"
        def close(self):
            pass
        def list_repos(self, prefixes=None):
            return [SimpleNamespace(slug="r1", name="R1", workspace="ws", default_branch="master")]
        def repos_with_branch(self, origin, prefixes=None, repos=None):
            return [SimpleNamespace(slug="r1", name="R1", workspace="ws", default_branch="master")]
        def diff(self, repo, destination, origin):
            f = SimpleNamespace(
                path="config/x.yaml", status="modified",
                added_lines=(),
                removed_lines=(),
            )
            return SimpleNamespace(files=[f])
        def find_pr(self, repo, origin, destination):
            return None
        def commit_for_branch(self, repo, branch, resolved=""):
            return "headO" if branch == "release/x" else "headM"
        def list_files(self, repo, ref):
            return ["config/x.yaml"]
        def raw_file(self, repo, ref, path):
            # solo en master (destino) hay el param SSM
            if ref == "headM":
                return "k: {{resolve:ssm:/config/productivo}}"
            return "sin ssm"

    monkeypatch.setattr("bbit_release.web.session.BitbucketClient", StubClient)
    monkeypatch.setattr("bbit_release.web.api.repos._circleci", lambda: None)
    client.post("/api/session", json={"workspace": "ws", "token": "tok"})

    body = client.get("/api/flow", params={"origin": "release/x", "destination": "master"}).json()
    assert body["diff"]["params"] == []  # master-only queda fuera


def test_repos_cache_force_exclude(monkeypatch):
    calls = {"n": 0}

    class StubClient:
        def __init__(self, ws, tok, **kw):
            self.workspace = ws
        def session(self):
            return (
                SimpleNamespace(uuid="x", name="WS", slug="ws", is_private=True),
                "Jane (@jane)",
            )
        def diff(self, repo, destination, origin):
            from types import SimpleNamespace
            return SimpleNamespace(files=[])
        def commit_for_branch(self, repo, branch, resolved=""):
            return "cafebabe"
        def has_commits_ahead(self, repo, branch, base):
            return False
        def tags_on_commit(self, repo, commit):
            return []
        def find_pr(self, repo, origin, destination):
            return None
        def branch_url(self, repo, branch):
            return "http://atlassian/branch"
        def list_files(self, repo, ref, tree=""):
            return []
        def raw_file(self, repo, ref, path):
            return None
        def close(self):
            pass
        def repos_with_branch(self, origin, prefixes=None):
            calls["n"] += 1
            return [SimpleNamespace(slug="orders-app", name="OA", workspace="ws", default_branch="master")]

    monkeypatch.setattr("bbit_release.web.session.BitbucketClient", StubClient)
    client.post("/api/session", json={"workspace": "ws", "token": "tok"})

    first = client.get("/api/flow", params={"origin": "release/x"}).json()
    assert [i["slug"] for i in first["projects"]] == ["orders-app"]

    second = client.get("/api/flow", params={"origin": "release/x"}).json()
    assert second["projects"][0]["slug"] == "orders-app"

    forced = client.get("/api/flow", params={"origin": "release/x", "force": 1}).json()
    assert forced["projects"][0]["slug"] == "orders-app"
    assert calls["n"] >= 2

    excl = client.get("/api/flow", params={"origin": "release/x", "exclude": "orders-app"}).json()
    assert [i["slug"] for i in excl["projects"]] == []


def test_create_pr_endpoint(monkeypatch):
    seen = {}

    class PClient:
        def __init__(self, ws, tok, **kw):
            self.workspace = ws
        def session(self):
            return (
                SimpleNamespace(uuid="x", name="WS", slug="ws", is_private=True),
                "Jane (@jane)",
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


def test_session_persists_filters(monkeypatch):
    saved = {}
    monkeypatch.setattr(FakeConfig, "save_filters",
                        lambda self, project_prefixes="", exclude_repos="": saved.update(
                            project_prefixes=project_prefixes, exclude_repos=exclude_repos))

    class StubClient:
        def __init__(self, ws, tok, **kw):
            self.workspace = ws
        def session(self):
            return (
                SimpleNamespace(uuid="x", name="WS", slug="ws", is_private=True),
                "Jane (@jane)",
            )
        def diff(self, repo, destination, origin):
            from types import SimpleNamespace
            return SimpleNamespace(files=[])
        def close(self):
            pass

    monkeypatch.setattr("bbit_release.web.session.BitbucketClient", StubClient)
    resp = client.post("/api/session", json={
        "workspace": "ws", "token": "tok",
        "project_prefixes": "trans,orders", "exclude_repos": "orders-legacy,billing",
    })
    assert resp.status_code == 200
    assert saved["project_prefixes"] == "trans,orders"
    assert set(saved["exclude_repos"].split(",")) == {"orders-legacy", "billing"}


def test_destroy_session_clears_filters(monkeypatch):
    cleared = []

    def fake_clear(self):
        cleared.append(1)
        return None

    class StubClient:
        def __init__(self, ws, tok, **kw):
            self.workspace = ws
        def session(self):
            return (
                SimpleNamespace(uuid="x", name="WS", slug="ws", is_private=True),
                "Jane (@jane)",
            )
        def diff(self, repo, destination, origin):
            from types import SimpleNamespace
            return SimpleNamespace(files=[])
        def close(self):
            pass

    monkeypatch.setattr("bbit_release.web.session.BitbucketClient", StubClient)
    monkeypatch.setattr(FakeConfig, "clear_filters", fake_clear)
    client.post("/api/session", json={"workspace": "ws", "token": "tok"})

    resp = client.delete("/api/session")
    assert resp.status_code == 200
    assert cleared == [1]


def test_scan_respects_exclude(monkeypatch):
    seen_prefixes = []

    class StubClient:
        def __init__(self, ws, tok, **kw):
            self.workspace = ws
        def session(self):
            return (
                SimpleNamespace(uuid="x", name="WS", slug="ws", is_private=True),
                "Jane (@jane)",
            )
        def list_files(self, repo, ref, tree=""):
            return []
        def close(self):
            pass
        def repos_with_branch(self, origin, prefixes=None):
            seen_prefixes.append(prefixes)
            return [
                SimpleNamespace(slug="trans-a", name="TA", workspace="ws", default_branch="master"),
                SimpleNamespace(slug="trans-b", name="TB", workspace="ws", default_branch="master"),
                SimpleNamespace(slug="core-app", name="CA", workspace="ws", default_branch="master"),
            ]
        def commit_for_branch(self, repo, branch, resolved=""):
            return "abc123"
        def tags_on_commit(self, repo, commit):
            return []
        def find_pr(self, repo, origin, destination):
            return None
        def branch_url(self, repo, branch):
            return "u"
        def has_commits_ahead(self, repo, branch, base):
            return True
        def diff(self, repo, destination, origin):
            return SimpleNamespace(files=[])
        def raw_file(self, repo, ref, path):
            return None

    monkeypatch.setattr("bbit_release.web.session.BitbucketClient", StubClient)
    monkeypatch.setattr("bbit_release.web.api.repos._circleci", lambda: None)
    client.post("/api/session", json={"workspace": "ws", "token": "tok"})

    body = client.get("/api/flow", params={
        "origin": "release/x", "project_prefixes": "trans", "exclude": "trans-b",
    }).json()
    slugs = [r["slug"] for r in body["scan"]["repos"]]
    assert slugs == ["trans-a"]
    # BBIT-36 P1: los filtros son una VISTA, no se pasan al backend. El índice
    # de ramas se obtiene completo (prefixes=None) y se filtra al leer.
    assert seen_prefixes == [None]


def test_create_missing_prs_filters_prefixes(monkeypatch):
    seen = []

    class PClient:
        def __init__(self, ws, tok, **kw):
            self.workspace = ws
        def session(self):
            return (
                SimpleNamespace(uuid="x", name="WS", slug="ws", is_private=True),
                "Jane (@jane)",
            )
        def close(self):
            pass
        def repos_with_branch(self, origin, prefixes=None):
            return [
                SimpleNamespace(slug="trans-a", name="TA", workspace="ws", default_branch="master"),
                SimpleNamespace(slug="core-b", name="CB", workspace="ws", default_branch="master"),
            ]
        def find_pr(self, repo, origin, dest):
            return None
        def has_commits_ahead(self, repo, origin, dest):
            return True
        def create_pr(self, repo, origin, dest, title=None):
            seen.append(repo)
            return {"url": "u", "title": title, "state": "OPEN", "id": 1}

    monkeypatch.setattr("bbit_release.web.session.BitbucketClient", PClient)
    client.post("/api/session", json={"workspace": "ws6", "token": "tok"})

    resp = client.post("/api/prs/create-missing", params={
        "origin": "release/x", "project_prefixes": "trans", "exclude": "trans-a",
    })
    body = resp.json()
    assert body["ok"] is True
    assert body["created"] == []
    assert seen == []


def test_tags_respect_exclude(monkeypatch):
    created = []

    class StubClient:
        def __init__(self, ws, tok, **kw):
            self.workspace = ws
        def session(self):
            return (
                SimpleNamespace(uuid="x", name="WS", slug="ws", is_private=True),
                "Jane (@jane)",
            )
        def diff(self, repo, destination, origin):
            from types import SimpleNamespace
            return SimpleNamespace(files=[])
        def close(self):
            pass
        def repos_with_branch(self, origin, prefixes=None):
            return [
                SimpleNamespace(slug="trans-a", name="TA", workspace="ws", default_branch="master"),
                SimpleNamespace(slug="trans-b", name="TB", workspace="ws", default_branch="master"),
            ]
        def commit_for_branch(self, repo, branch, resolved=""):
            return "abc123"
        def tag_exists(self, slug, name):
            return False
        def create_tag(self, slug, name, commit):
            created.append(slug)

    class StubCi:
        def pipeline_id_for_commit(self, repo, branch, commit):
            return 7

    monkeypatch.setattr("bbit_release.web.session.BitbucketClient", StubClient)
    monkeypatch.setattr("bbit_release.web.api.repos._circleci", lambda: StubCi())
    client.post("/api/session", json={"workspace": "ws7", "token": "tok"})

    body = client.post("/api/tags", params={
        "origin": "release/x", "prefixes": "uat", "project_prefixes": "trans", "exclude": "trans-b",
    }).json()
    assert body["ok"] is True
    assert created == ["trans-a"]


def test_scan_cache_hit_on_second_call(monkeypatch):
    """El segundo scan con la misma rama origen/destino NO vuelve a consultar
    la API (usa branch_repos + scan_cache de SQLite)."""
    calls = {"branch": 0, "scan": 0}

    class StubClient:
        def __init__(self, ws, tok, **kw):
            self.workspace = ws
        def session(self):
            return (
                SimpleNamespace(uuid="x", name="WS", slug="ws", is_private=True),
                "Jane (@jane)",
            )
        def list_files(self, repo, ref, tree=""):
            return []
        def close(self):
            pass
        def repos_with_branch(self, origin, prefixes=None):
            calls["branch"] += 1
            return [SimpleNamespace(slug="r1", name="R1", workspace="ws", default_branch="master")]
        def commit_for_branch(self, repo, branch, resolved=""):
            return "abc123"
        def has_commits_ahead(self, repo, branch, base):
            return False
        def tags_on_commit(self, repo, commit):
            return []
        def find_pr(self, repo, origin, destination):
            return None
        def branch_url(self, repo, branch):
            return "u"
        def diff(self, repo, destination, origin):
            return SimpleNamespace(files=[])
        def raw_file(self, repo, ref, path):
            return None

    monkeypatch.setattr("bbit_release.web.session.BitbucketClient", StubClient)
    monkeypatch.setattr("bbit_release.web.api.repos._circleci", lambda: None)
    client.post("/api/session", json={"workspace": "ws", "token": "tok"})

    params = {"origin": "release/x", "destination": "master", "prefixes": "uat"}
    first = client.get("/api/flow", params=params).json()
    assert first["scan"]["repos"][0]["slug"] == "r1"
    assert calls["branch"] == 1

    second = client.get("/api/flow", params=params).json()
    assert second["scan"]["repos"][0]["slug"] == "r1"
    assert calls["branch"] == 1  # no re-discovery en el segundo scan


def test_scan_force_refreshes(monkeypatch):
    """force=1 vuelve a consultar la API y sobreescribe el scan_cache."""
    calls = {"branch": 0}

    class StubClient:
        def __init__(self, ws, tok, **kw):
            self.workspace = ws
        def session(self):
            return (
                SimpleNamespace(uuid="x", name="WS", slug="ws", is_private=True),
                "Jane (@jane)",
            )
        def list_files(self, repo, ref, tree=""):
            return []
        def close(self):
            pass
        def repos_with_branch(self, origin, prefixes=None):
            calls["branch"] += 1
            return [SimpleNamespace(slug="r1", name="R1", workspace="ws", default_branch="master")]
        def commit_for_branch(self, repo, branch, resolved=""):
            return "abc123"
        def has_commits_ahead(self, repo, branch, base):
            return False
        def tags_on_commit(self, repo, commit):
            return []
        def find_pr(self, repo, origin, destination):
            return None
        def branch_url(self, repo, branch):
            return "u"
        def diff(self, repo, destination, origin):
            return SimpleNamespace(files=[])
        def raw_file(self, repo, ref, path):
            return None

    monkeypatch.setattr("bbit_release.web.session.BitbucketClient", StubClient)
    monkeypatch.setattr("bbit_release.web.api.repos._circleci", lambda: None)
    client.post("/api/session", json={"workspace": "ws", "token": "tok"})

    params = {"origin": "release/x", "destination": "master", "prefixes": "uat"}
    client.get("/api/flow", params=params)
    client.get("/api/flow", params=params)
    assert calls["branch"] == 1  # cache hit en 2do

    client.get("/api/flow", params={**params, "force": 1})
    assert calls["branch"] == 2  # force consulta de nuevo


def test_flow_cambiar_filtro_no_reescanea_nada(monkeypatch):
    """BBIT-36 P1/P2 — cambiar project_prefixes es una VISTA: no se vuelven a
    consultar los repos ya escaneados para el mismo par de ramas.

    El índice branch_repos y el scan por repo se sirven de SQLite; el segundo
    request (otro filtro) no llama find_pr/commit/tags/commits_ahead."""
    calls = {"find_pr": 0, "tags": 0, "ahead": 0, "origin_commits": 0, "branch": 0}

    class StubClient:
        def __init__(self, ws, tok, **kw):
            self.workspace = ws
        def session(self):
            return (
                SimpleNamespace(uuid="x", name="WS", slug="ws", is_private=True),
                "Jane (@jane)",
            )
        def list_files(self, repo, ref, tree=""):
            return []
        def close(self):
            pass
        def repos_with_branch(self, origin, prefixes=None):
            calls["branch"] += 1
            return [
                SimpleNamespace(slug="r1", name="R1", workspace="ws", default_branch="master"),
                SimpleNamespace(slug="r2", name="R2", workspace="ws", default_branch="master"),
            ]
        def commit_for_branch(self, repo, branch, resolved=""):
            if branch == "release/x":
                calls["origin_commits"] += 1
            return "abc123"
        def has_commits_ahead(self, repo, branch, base):
            calls["ahead"] += 1
            return False
        def tags_on_commit(self, repo, commit):
            calls["tags"] += 1
            return []
        def find_pr(self, repo, origin, destination):
            calls["find_pr"] += 1
            return None
        def branch_url(self, repo, branch):
            return "u"
        def diff(self, repo, destination, origin):
            return SimpleNamespace(files=[])
        def raw_file(self, repo, ref, path):
            return None

    monkeypatch.setattr("bbit_release.web.session.BitbucketClient", StubClient)
    monkeypatch.setattr("bbit_release.web.api.repos._circleci", lambda: None)
    client.post("/api/session", json={"workspace": "ws", "token": "tok"})

    # 1º: sin filtros → se escanean los 2 repos.
    first = client.get("/api/flow", params={"origin": "release/x"}).json()
    assert [r["slug"] for r in first["scan"]["repos"]] == ["r1", "r2"]
    assert calls == {"find_pr": 2, "tags": 2, "ahead": 2, "origin_commits": 2, "branch": 1}

    # 2º: con project_prefixes=r1 → solo cambia la vista; CERO consultas nuevas
    # al scan (index branch_repos + scan_repo son cache hits).
    second = client.get("/api/flow", params={"origin": "release/x", "project_prefixes": "r1"}).json()
    assert [r["slug"] for r in second["scan"]["repos"]] == ["r1"]
    assert calls == {"find_pr": 2, "tags": 2, "ahead": 2, "origin_commits": 2, "branch": 1}


def test_flow_ampliar_filtro_consulta_solo_repos_nuevos(monkeypatch):
    """BBIT-36 P2 — al ampliar el filtro se consulta SOLO el repo que aparece
    por primera vez; los ya cacheados no se tocan."""
    calls = {"find_pr": 0}

    class StubClient:
        def __init__(self, ws, tok, **kw):
            self.workspace = ws
        def session(self):
            return (
                SimpleNamespace(uuid="x", name="WS", slug="ws", is_private=True),
                "Jane (@jane)",
            )
        def list_files(self, repo, ref, tree=""):
            return []
        def close(self):
            pass
        def repos_with_branch(self, origin, prefixes=None):
            return [
                SimpleNamespace(slug="r1", name="R1", workspace="ws", default_branch="master"),
                SimpleNamespace(slug="r2", name="R2", workspace="ws", default_branch="master"),
            ]
        def commit_for_branch(self, repo, branch, resolved=""):
            return "abc123"
        def has_commits_ahead(self, repo, branch, base):
            return False
        def tags_on_commit(self, repo, commit):
            return []
        def find_pr(self, repo, origin, destination):
            calls["find_pr"] += 1
            return None
        def branch_url(self, repo, branch):
            return "u"
        def diff(self, repo, destination, origin):
            return SimpleNamespace(files=[])
        def raw_file(self, repo, ref, path):
            return None

    monkeypatch.setattr("bbit_release.web.session.BitbucketClient", StubClient)
    monkeypatch.setattr("bbit_release.web.api.repos._circleci", lambda: None)
    client.post("/api/session", json={"workspace": "ws", "token": "tok"})

    client.get("/api/flow", params={"origin": "release/x", "project_prefixes": "r1"}).json()
    assert calls["find_pr"] == 1  # solo r1

    second = client.get("/api/flow", params={"origin": "release/x", "project_prefixes": "r1,r2"}).json()
    assert [r["slug"] for r in second["scan"]["repos"]] == ["r1", "r2"]
    assert calls["find_pr"] == 2  # r2 es nuevo; r1 vino del cache


def test_flow_tags_on_demand_desde_cache_sin_tags(monkeypatch):
    """BBIT-36 P3 — con with_tags=0 el scan se cachea SIN tags; el pedido
    posterior con with_tags=1 re-resuelve tags/deploys (los datos nuevos que
    el usuario pidió) re-consultando el servicio."""
    calls = {"tags": 0}

    class StubClient:
        def __init__(self, ws, tok, **kw):
            self.workspace = ws
        def session(self):
            return (
                SimpleNamespace(uuid="x", name="WS", slug="ws", is_private=True),
                "Jane (@jane)",
            )
        def list_files(self, repo, ref, tree=""):
            return []
        def close(self):
            pass
        def repos_with_branch(self, origin, prefixes=None):
            return [SimpleNamespace(slug="r1", name="R1", workspace="ws", default_branch="master")]
        def commit_for_branch(self, repo, branch, resolved=""):
            return "abc123"
        def has_commits_ahead(self, repo, branch, base):
            return False
        def tags_on_commit(self, repo, commit):
            calls["tags"] += 1
            return [{"name": "uat-42", "date": "x"}]
        def find_pr(self, repo, origin, destination):
            return None
        def branch_url(self, repo, branch):
            return "u"
        def diff(self, repo, destination, origin):
            return SimpleNamespace(files=[])
        def raw_file(self, repo, ref, path):
            return None

    class StubCi:
        vcs = "bb"
        def deploys_for_tags(self, repo, tags):
            return {}
        def project_id(self, repo):
            return "p1"
        def deploy_for_tag(self, repo, tag, commit, prefix):
            return None

    monkeypatch.setattr("bbit_release.web.session.BitbucketClient", StubClient)
    monkeypatch.setattr("bbit_release.web.api.repos._circleci", lambda: StubCi())
    client.post("/api/session", json={"workspace": "ws", "token": "tok"})

    body = client.get("/api/flow", params={"origin": "release/x", "with_tags": 0}).json()
    assert body["scan"]["repos"][0]["tags"] == []
    assert calls["tags"] == 0

    body = client.get("/api/flow", params={"origin": "release/x", "with_tags": 1}).json()
    tags = body["scan"]["repos"][0]["tags"]
    assert [t["name"] for t in tags] == ["uat-42"]
    assert calls["tags"] == 1  # ya cacheados sin tags → se resuelven recien aca


def test_spa_serves_build_when_present():
    expected = 200 if FRONTEND_DIST.is_dir() else 503
    resp = client.get("/")
    assert resp.status_code == expected
    api = client.get("/api/health")
    assert api.status_code == 200


def test_clear_cache_sin_body_limpia_todo():
    from bbit_release.cache import get_cache
    cache = get_cache()
    cache.set_master("r1", "master", {("/config/a", "")})
    cache.record_service_call(
        source="bitbucket", method="GET", url="/2.0/me", params=None,
        status=200, duration_ms=1.0, response="{}",
    )
    # crear una sesión en la DB del cache
    cache.find_session("release/x", "master", {"repositories": {"excluded": [], "prefixes": []}})

    resp = client.delete("/api/cache")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["cleared"]["request"] > 0
    assert data["cleared"]["init_sesion"] > 0
    assert data["cleared"]["service_call"] > 0
    assert cache._fetchone("SELECT COUNT(*) FROM request", ())[0] == 0
    assert cache._fetchone("SELECT COUNT(*) FROM init_sesion", ())[0] == 0
    assert cache._fetchone("SELECT COUNT(*) FROM service_call", ())[0] == 0


def test_clear_cache_con_sesion_filtra_solo_esa():
    from bbit_release.cache import get_cache
    cache = get_cache()
    # dos sesiones distintas de la DB del cache
    cache.find_session("release/x", "master", {"repositories": {"excluded": [], "prefixes": []}})
    cache.find_session("release/otra", "master", {"repositories": {"excluded": [], "prefixes": []}})
    cache.set_master("r1", "master", {("/config/a", "")})

    resp = client.request("DELETE", "/api/cache", json={
        "sessions": [{"origin": "release/x", "destination": "master"}],
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["cleared"]["sessions"] == 1
    rows = cache._fetchall(
        "SELECT is_source FROM init_sesion WHERE is_source = ?", ("release/x",)
    )
    assert rows == []
    kept = cache._fetchall(
        "SELECT is_source FROM init_sesion", ()
    )
    assert "release/otra" in [r[0] for r in kept]


def test_clear_cache_session_vuelve_a_consultar_apis(monkeypatch):
    """Eliminar una sesión borra el cache del flow (mismo origen/destino):
    re-consultar vuelve a las APIs aunque los filtros no coincidan (BBIT-20)."""
    branch_calls: list[str] = []

    class StubClient:
        def __init__(self, ws, tok, **kw):
            self.workspace = ws
        def session(self):
            return (
                SimpleNamespace(uuid="x", name="WS", slug="ws", is_private=True),
                "Jane (@jane)",
            )
        def close(self):
            pass
        def repos_with_branch(self, origin, prefixes=None, repos=None):
            branch_calls.append(origin)
            return [SimpleNamespace(slug="myapp1", name="My App 1", workspace="ws", default_branch="master")]
        def commit_for_branch(self, repo, branch, resolved=""):
            return "abc123"
        def has_commits_ahead(self, repo, branch, base):
            return False
        def tags_on_commit(self, repo, commit):
            return []
        def find_pr(self, repo, origin, destination):
            return None
        def branch_url(self, repo, branch):
            return "http://atlassian/branch"
        def diff(self, repo, destination, origin):
            from types import SimpleNamespace
            return SimpleNamespace(files=[])
        def list_files(self, repo, ref, tree=""):
            return []
        def raw_file(self, repo, ref, path):
            return None

    monkeypatch.setattr("bbit_release.web.session.BitbucketClient", StubClient)
    monkeypatch.setattr("bbit_release.web.api.repos._circleci", lambda: None)
    client.post("/api/session", json={"workspace": "ws", "token": "tok"})

    body = client.get("/api/flow", params={"origin": "release/x", "prefixes": "uat",
                                           "project_prefixes": "my", "exclude": "billing"}).json()
    assert body["scan"]["stats"]["repos"] == 1
    assert branch_calls == ["release/x"]

    # borrar la sesión por su pareja de ramas (sin repetir los filtros con los
    # que se cacheó el flow): la pareja entera debe invalidarse igual (BBIT-20)
    resp = client.request("DELETE", "/api/cache", json={
        "sessions": [{"origin": "release/x", "destination": "master"}],
    })
    assert resp.status_code == 200

    body = client.get("/api/flow", params={"origin": "release/x", "prefixes": "uat",
                                           "project_prefixes": "my", "exclude": "billing"}).json()
    assert body["scan"]["stats"]["repos"] == 1
    assert branch_calls == ["release/x", "release/x"]


def test_diff_enrich_aws_ok_and_missing(monkeypatch):
    """Con sesión AWS (AwsSession mockeado) el diff marca ok/missing."""
    FakeConfig.aws_profile = "prof"
    FakeConfig.aws_region = "us-east-1"

    class StubClient:
        def __init__(self, ws, tok, **kw):
            self.workspace = ws
        def session(self):
            return (
                SimpleNamespace(uuid="x", name="WS", slug="ws", is_private=True),
                "Jane (@jane)",
            )
        def has_commits_ahead(self, repo, branch, base):
            return False
        def tags_on_commit(self, repo, commit):
            return []
        def branch_url(self, repo, branch):
            return "http://atlassian/branch"
        def close(self):
            pass
        def list_repos(self, prefixes=None):
            return [SimpleNamespace(slug="r1", name="R1", workspace="ws", default_branch="master")]
        def repos_with_branch(self, origin, prefixes=None, repos=None):
            return [SimpleNamespace(slug="r1", name="R1", workspace="ws", default_branch="master")]
        def diff(self, repo, destination, origin):
            f = SimpleNamespace(
                path="config/x.yaml", status="modified",
                added_lines=("a: {{resolve:ssm:/config/a}}",),
                removed_lines=(),
            )
            return SimpleNamespace(files=[f])
        def find_pr(self, repo, origin, destination):
            return None
        def commit_for_branch(self, repo, branch, resolved=""):
            return "headO" if branch == "release/x" else "headM"
        def list_files(self, repo, ref):
            return ["config/x.yaml"]
        def raw_file(self, repo, ref, path):
            if ref == "headM":
                return ""
            return "a: {{resolve:ssm:/config/a}}\nb: {{resolve:ssm:/config/nope}}"

    class FakeSsmClient:
        def __init__(self):
            self.calls = []
        def get_parameters(self, **kw):
            self.calls.append(kw)
            values = {
                "/config/a": {"Name": "/config/a", "Value": "valor-qa", "Type": "String"},
            }
            return {
                "Parameters": [values[n] for n in kw.get("Names", []) if n in values],
                "InvalidParameters": [n for n in kw.get("Names", []) if n not in values],
            }

    class FakeAwsSession:
        def __init__(self, profile="prof", region="us-east-1", client_id="", **kw):
            self.profile = profile
            self.region = region
            self.client_id = client_id
            self.available = bool(self.profile)
            self._ssm = FakeSsmClient()
        @classmethod
        def from_config(cls, cfg=None):
            return cls(profile=getattr(cfg, "aws_profile", ""),
                       region=getattr(cfg, "aws_region", ""),
                       client_id=getattr(cfg, "client_id", ""))
        def client(self, service):
            return self._ssm
        def close(self):
            pass

    monkeypatch.setattr("bbit_release.aws.session.AwsSession", FakeAwsSession)
    monkeypatch.setattr("bbit_release.web.session.BitbucketClient", StubClient)
    monkeypatch.setattr("bbit_release.web.api.repos._circleci", lambda: None)
    client.post("/api/session", json={"workspace": "ws", "token": "tok"})

    body = client.get("/api/flow", params={"origin": "release/x", "destination": "master"}).json()
    assert body["diff"]["params"] == [
        {"param": "/config/a", "arn": "", "tipo": "nuevo", "repos": ["r1"], "count": 1},
        {"param": "/config/nope", "arn": "", "tipo": "nuevo", "repos": ["r1"], "count": 1},
    ]
    # los valores SSM NO se resuelven al armar la tabla (se piden bajo demanda)
    cached = get_cache().get_flow(
        "release/x", "master", None, set(),
        deploy_prefixes=["uat", "stgp", "prod"], ssm_prefixes=["/config", "/common"], mode="diff",
    )
    assert cached is not None
    assert cached["diff"]["params"][0].get("qa_value") is None
    FakeConfig.aws_profile = ""
    FakeConfig.aws_region = ""


# ---------------------------------------------------------------------------
# BBIT-26: SSE streaming tests
# ---------------------------------------------------------------------------


def test_flow_stream_requires_session():
    resp = client.get("/api/flow/stream", params={"origin": "release/x"})
    assert resp.status_code == 401


def test_flow_stream_events(monkeypatch):
    """SSE endpoint streams repo events, stats, and done sentinel without `behind`."""
    from bbit_release.web.api import repos as _repos

    class StubClient:
        def __init__(self, ws, tok, **kw):
            self.workspace = ws
        def session(self):
            return (
                SimpleNamespace(uuid="x", name="WS", slug="ws", is_private=True),
                "Jane (@jane)",
            )
        def close(self):
            pass
        def repos_with_branch(self, origin, prefixes=None):
            return [SimpleNamespace(slug="r1", name="R1", workspace="ws", default_branch="master"),
                    SimpleNamespace(slug="r2", name="R2", workspace="ws", default_branch="master")]
        def commit_for_branch(self, repo, branch, resolved=""):
            return "abc123"
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
        def list_files(self, repo, ref, tree=""):
            return []
        def raw_file(self, repo, ref, path):
            return None

    monkeypatch.setattr("bbit_release.web.session.BitbucketClient", StubClient)
    monkeypatch.setattr(_repos, "_circleci", lambda: None)
    client.post("/api/session", json={"workspace": "ws", "token": "tok"})

    resp = client.get("/api/flow/stream", params={"origin": "release/x", "prefixes": "uat"})
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]

    # Parse SSE events
    events = []
    current_event = None
    for line in resp.text.splitlines():
        if line.startswith("event:"):
            current_event = line.split(":", 1)[1].strip()
        elif line.startswith("data:") and current_event:
            import json
            payload = json.loads(line.split(":", 1)[1].strip())
            events.append({"type": current_event, "data": payload})
            current_event = None

    event_types = [e["type"] for e in events]
    assert "repo" in event_types
    assert "stats" in event_types
    assert "done" in event_types

    # Each repo event has no `behind` key
    repo_events = [e for e in events if e["type"] == "repo"]
    assert len(repo_events) == 2
    for re_ in repo_events:
        assert "behind" not in re_["data"]
        assert re_["data"]["commit"] == "abc123"

    # Stats event has no `synced` key
    stats_event = next(e for e in events if e["type"] == "stats")
    assert "synced" not in stats_event["data"]
    assert stats_event["data"]["repos"] == 2

    # Done sentinel
    done_event = next(e for e in events if e["type"] == "done")
    assert done_event["data"] == {}


def test_flow_stream_failed_repo_emits_error_item(monkeypatch):
    """S4 — a repo failing during `_stream_scan` emits its error item as a
    `repo` event without breaking the stream; other repos continue and
    stats + done are still delivered."""
    from bbit_release.bitbucket.client import BitbucketError

    class StubClient:
        def __init__(self, ws, tok, **kw):
            self.workspace = ws
        def session(self):
            return (
                SimpleNamespace(uuid="x", name="WS", slug="ws", is_private=True),
                "Jane (@jane)",
            )
        def close(self):
            pass
        def repos_with_branch(self, origin, prefixes=None):
            return [SimpleNamespace(slug="r1", name="R1", workspace="ws", default_branch="master"),
                    SimpleNamespace(slug="r2", name="R2", workspace="ws", default_branch="master")]
        def commit_for_branch(self, repo, branch, resolved=""):
            return "abc123"
        def has_commits_ahead(self, repo, branch, base):
            return False
        def tags_on_commit(self, repo, commit):
            return []
        def find_pr(self, repo, origin, destination):
            return None
        def branch_url(self, repo, branch):
            return f"http://atlassian/{repo}/{branch}"
        def diff(self, repo, destination, origin):
            return SimpleNamespace(files=[])
        def list_files(self, repo, ref, tree=""):
            return []
        def raw_file(self, repo, ref, path):
            return None

    original_repo_scan = repos_mod._repo_scan

    def _failing_repo_scan(client, ci, repo, origin, destination, clean, ctx=None, on_field=None, with_tags=True):
        if repo.slug == "r1":
            raise BitbucketError("repo r1 rompido")
        return original_repo_scan(client, ci, repo, origin, destination, clean, ctx, on_field=on_field, with_tags=with_tags)

    monkeypatch.setattr("bbit_release.web.session.BitbucketClient", StubClient)
    monkeypatch.setattr("bbit_release.web.api.repos._circleci", lambda: None)
    monkeypatch.setattr(repos_mod, "_repo_scan", _failing_repo_scan)
    client.post("/api/session", json={"workspace": "ws", "token": "tok"})

    resp = client.get("/api/flow/stream", params={"origin": "release/x", "prefixes": "uat"})
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]

    events = []
    current_event = None
    for line in resp.text.splitlines():
        if line.startswith("event:"):
            current_event = line.split(":", 1)[1].strip()
        elif line.startswith("data:") and current_event:
            import json
            payload = json.loads(line.split(":", 1)[1].strip())
            events.append({"type": current_event, "data": payload})
            current_event = None

    event_types = [e["type"] for e in events]
    assert "repo" in event_types
    assert "stats" in event_types
    assert "done" in event_types

    # Both repos emit a repo event; the failed one carries the error item.
    repo_events = [e for e in events if e["type"] == "repo"]
    assert len(repo_events) == 2

    r1_event = next(e for e in repo_events if e["data"]["slug"] == "r1")
    assert "repo r1 rompido" in r1_event["data"]["error"]
    assert r1_event["data"]["commit"] == ""
    assert r1_event["data"]["pr"]["exists"] is False
    assert "behind" not in r1_event["data"]

    r2_event = next(e for e in repo_events if e["data"]["slug"] == "r2")
    assert r2_event["data"].get("error") is None
    assert r2_event["data"]["commit"] == "abc123"
    assert "behind" not in r2_event["data"]

    # Stats count the failed item too, and never carry `synced`.
    stats_event = next(e for e in events if e["type"] == "stats")
    assert stats_event["data"]["repos"] == 2
    assert "synced" not in stats_event["data"]


def test_flow_stream_missing_repo_emitted_before_scan(monkeypatch):
    """BBIT-35 P4 — repos sin la rama llegan como evento repo visible:false
    ANTES del scan, para que el frontend remueva sus filas placeholder."""
    from bbit_release.web.api import repos as _repos

    class StubClient:
        def __init__(self, ws, tok, **kw):
            self.workspace = ws
        def session(self):
            return (
                SimpleNamespace(uuid="x", name="WS", slug="ws", is_private=True),
                "Jane (@jane)",
            )
        def close(self):
            pass
        def list_repos(self, prefixes=None):
            return [
                SimpleNamespace(slug="r1", name="R1", workspace="ws", default_branch="master"),
                SimpleNamespace(slug="r2", name="R2", workspace="ws", default_branch="master"),
                SimpleNamespace(slug="r3", name="R3", workspace="ws", default_branch="master"),
            ]
        def repos_with_branch(self, origin, prefixes=None, repos=None):
            # r3 no tiene la rama
            return [r for r in repos if r.slug != "r3"]
        def commit_for_branch(self, repo, branch, resolved=""):
            return "abc123"
        def has_commits_ahead(self, repo, branch, base):
            return False
        def tags_on_commit(self, repo, commit):
            return []
        def find_pr(self, repo, origin, destination):
            return None
        def branch_url(self, repo, branch):
            return f"http://atlassian/{repo}/{branch}"
        def diff(self, repo, destination, origin):
            return SimpleNamespace(files=[])
        def list_files(self, repo, ref, tree=""):
            return []
        def raw_file(self, repo, ref, path):
            return None

    monkeypatch.setattr("bbit_release.web.session.BitbucketClient", StubClient)
    monkeypatch.setattr("bbit_release.web.api.repos._circleci", lambda: None)
    client.post("/api/session", json={"workspace": "ws", "token": "tok"})

    resp = client.get("/api/flow/stream", params={"origin": "release/x", "prefixes": "uat"})
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]

    events = []
    current_event = None
    for line in resp.text.splitlines():
        if line.startswith("event:"):
            current_event = line.split(":", 1)[1].strip()
        elif line.startswith("data:") and current_event:
            import json
            payload = json.loads(line.split(":", 1)[1].strip())
            events.append({"type": current_event, "data": payload})
            current_event = None

    repo_events = [e for e in events if e["type"] == "repo"]

    # r3 no tiene la rama: evento visible:false con branch_state explicito,
    # emitido ANTES del scan de los found (r1/r2).
    assert repo_events[0]["data"]["slug"] == "r3"
    r3 = repo_events[0]["data"]
    assert r3["visible"] is False
    assert r3["branch_state"] == "not_found"
    assert r3["resolved_branch"] == ""
    assert r3["reason"] == "branch_not_found"
    assert "commit" not in r3 or r3.get("commit") == ""

    # Los found llegan después con su metadata completa.
    found_slugs = {e["data"]["slug"] for e in repo_events}
    assert {"r1", "r2"} <= found_slugs
    r1 = next(e for e in repo_events if e["data"]["slug"] == "r1")
    assert r1["data"]["branch_state"] == "found"
    assert r1["data"]["commit"] == "abc123"

    # Stats cuentan solo los repos con la rama.
    stats_event = next(e for e in events if e["type"] == "stats")
    assert stats_event["data"]["repos"] == 2


def test_flow_stream_emits_fields_before_repo_complete(monkeypatch):
    """BBIT-35 P5 — el stream emite eventos `field` (commit/pr/tags/deploys)
    antes del evento `repo` completo, para pintar la fila por campo async."""
    from bbit_release.web.api import repos as _repos

    class StubClient:
        def __init__(self, ws, tok, **kw):
            self.workspace = ws
        def session(self):
            return (
                SimpleNamespace(uuid="x", name="WS", slug="ws", is_private=True),
                "Jane (@jane)",
            )
        def close(self):
            pass
        def list_repos(self, prefixes=None):
            return [
                SimpleNamespace(slug="r1", name="R1", workspace="ws", default_branch="master"),
            ]
        def repos_with_branch(self, origin, prefixes=None, repos=None):
            return [r for r in repos if r.slug != "rX"]
        def commit_for_branch(self, repo, branch, resolved=""):
            return "abc123"
        def has_commits_ahead(self, repo, branch, base):
            return False
        def tags_on_commit(self, repo, commit):
            return [{"name": "uat-42", "date": "x"}]
        def find_pr(self, repo, origin, destination):
            return None
        def branch_url(self, repo, branch):
            return f"http://atlassian/{repo}/{branch}"
        def diff(self, repo, destination, origin):
            return SimpleNamespace(files=[])
        def list_files(self, repo, ref, tree=""):
            return []
        def raw_file(self, repo, ref, path):
            return None

    class StubCi:
        vcs = "bitbucket"
        def deploy_for_tag(self, repo, tag, commit, env):
            return SimpleNamespace(status="success", url="http://ci/x", created_at="x", deploy_number=1, workflow="wf", job="job", approval=None)
        def deploys_for_tags(self, repo, tags):
            return {}
        def project_id(self, repo):
            return "proj-1"

    from bbit_release.web.api import repos as repos_mod
    monkeypatch.setattr("bbit_release.web.session.BitbucketClient", StubClient)
    monkeypatch.setattr(repos_mod, "_circleci", lambda: StubCi())
    client.post("/api/session", json={"workspace": "ws", "token": "tok"})

    resp = client.get("/api/flow/stream", params={"origin": "release/x", "prefixes": "uat"})
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]

    events = []
    current_event = None
    for line in resp.text.splitlines():
        if line.startswith("event:"):
            current_event = line.split(":", 1)[1].strip()
        elif line.startswith("data:") and current_event:
            import json
            payload = json.loads(line.split(":", 1)[1].strip())
            events.append({"type": current_event, "data": payload})
            current_event = None

    field_events = [e for e in events if e["type"] == "field"]
    repo_events = [e for e in events if e["type"] == "repo"]

    # Los fields de r1 llegan antes de su repo completo.
    assert {f["data"]["slug"] for f in field_events} == {"r1"}
    field_names = [f["data"]["field"] for f in field_events]
    assert "commit" in field_names
    assert "pr" in field_names
    assert "tags" in field_names
    assert "deploys" in field_names

    # El primer field (commit) se emite antes del primer repo completo.
    first_repo_idx = events.index(repo_events[0])
    first_field_idx = events.index(field_events[0])
    assert first_field_idx < first_repo_idx

    # El repo completo trae todos los campos merged en el item.
    r1_event = next(e for e in repo_events if e["data"]["slug"] == "r1")
    assert r1_event["data"]["commit"] == "abc123"
    assert r1_event["data"]["tags"][0]["name"] == "uat-42"
    assert r1_event["data"]["deploys"]["uat"]["status"] == "success"

    # Stats cuentan el repo visible.
    stats_event = next(e for e in events if e["type"] == "stats")
    assert stats_event["data"]["repos"] == 1


def test_flow_with_tags_0_skip_tags_consulta(monkeypatch):
    """BBIT-35 — `with_tags=0` en el request NO consulta tags ni deploys;
    el default lo sigue haciendo (tags_on_commit llamado)."""
    class StubClient:
        tags_calls: list = []
        def __init__(self, ws, tok, **kw):
            self.workspace = ws
        def session(self):
            return (
                SimpleNamespace(uuid="x", name="WS", slug="ws", is_private=True),
                "Jane (@jane)",
            )
        def close(self):
            pass
        def repos_with_branch(self, origin, prefixes=None):
            return [SimpleNamespace(slug="r1", name="R1", workspace="ws", default_branch="master")]
        def commit_for_branch(self, repo, branch, resolved=""):
            return "abc123"
        def has_commits_ahead(self, repo, branch, base):
            return False
        def tags_on_commit(self, repo, commit):
            StubClient.tags_calls.append((repo, commit))
            return [{"name": "uat-42", "date": "x"}]
        def find_pr(self, repo, origin, destination):
            return None
        def branch_url(self, repo, branch):
            return f"http://atlassian/{repo}/{branch}"
        def diff(self, repo, destination, origin):
            return SimpleNamespace(files=[])
        def list_files(self, repo, ref, tree=""):
            return []
        def raw_file(self, repo, ref, path):
            return None

    from bbit_release.web.api import repos as repos_mod
    monkeypatch.setattr("bbit_release.web.session.BitbucketClient", StubClient)
    monkeypatch.setattr(repos_mod, "_circleci", lambda: None)
    client.post("/api/session", json={"workspace": "ws", "token": "tok"})

    # Sin with_tags: default 1 → se consultan tags.
    StubClient.tags_calls = []
    body = client.get("/api/flow", params={"origin": "release/with-tags", "prefixes": "uat", "force": 1}).json()
    assert len(StubClient.tags_calls) == 1

    # with_tags=0: no se consultan tags; el item llega sin ellos.
    StubClient.tags_calls = []
    body = client.get("/api/flow", params={
        "origin": "release/no-tags", "prefixes": "uat", "with_tags": 0, "force": 1,
    }).json()
    assert StubClient.tags_calls == []
    r1 = next(r for r in body["scan"]["repos"] if r["slug"] == "r1")
    assert r1["tags"] == []
    assert r1["match_tag"].get("uat") is None


def test_stream_scan_emits_as_completed_not_submission_order(monkeypatch):
    """BBIT-32 — `_stream_scan` entrega cada repo apenas termina
    (as_completed): si el primer repo de la lista es el lento, los rápidos
    se emiten primero y no bloquea el render incremental."""
    import threading

    from bbit_release.web.api import repos as repos_mod

    r2_done = threading.Event()
    released: list[str] = []

    def _slow(client, ci, repo, origin, destination, clean, ctx=None, on_field=None, with_tags=True):
        r2_done.wait(timeout=5)
        released.append(repo.slug)
        return ({"slug": repo.slug, "name": repo.name}, None)

    def _fast(client, ci, repo, origin, destination, clean, ctx=None, on_field=None, with_tags=True):
        released.append(repo.slug)
        if repo.slug == "r2":
            r2_done.set()
        return ({"slug": repo.slug, "name": repo.name}, None)

    def _pick(client, ci, repo, origin, destination, clean, ctx=None, on_field=None, with_tags=True):
        return _slow(client, ci, repo, origin, destination, clean, ctx, on_field, with_tags) if repo.slug == "r1" else _fast(client, ci, repo, origin, destination, clean, ctx, on_field, with_tags)

    monkeypatch.setattr(repos_mod, "_repo_scan", _pick)

    r1 = SimpleNamespace(slug="r1", name="R1", workspace="ws", default_branch="master")
    r2 = SimpleNamespace(slug="r2", name="R2", workspace="ws", default_branch="master")
    dummy = object()

    events = list(repos_mod._stream_scan(dummy, None, [r1, r2], "release/x", "master", []))
    repo_items = [ev[1] for ev in events if ev[0] == "repo"]
    assert [it["slug"] for it in repo_items] == ["r2", "r1"]
    assert released == ["r2", "r1"]


def test_flow_stream_error_no_session():
    """SSE endpoint emits error event when session is missing."""
    resp = client.get("/api/flow/stream", params={"origin": "release/x"})
    assert resp.status_code == 401
    body = resp.json()
    assert "detail" in body or "error" in body or body.get("detail") is not None


# ---------------------------------------------------------------------------
# BBIT-27: Scoped generate_tags tests
# ---------------------------------------------------------------------------


def test_generate_tags_scoped_with_repo(monkeypatch):
    """When `repo` param is set, only that repo's commit_for_branch is called
    (not _branch_repos_cached), and prefixes are used as-is."""
    branch_repos_calls = []
    commit_calls = []

    class StubClient:
        def __init__(self, ws, tok, **kw):
            self.workspace = ws
        def session(self):
            return (
                SimpleNamespace(uuid="x", name="WS", slug="ws", is_private=True),
                "Jane (@jane)",
            )
        def diff(self, repo, destination, origin):
            return SimpleNamespace(files=[])
        def list_files(self, repo, ref, tree=""):
            return []
        def close(self):
            pass
        def repos_with_branch(self, origin, prefixes=None):
            branch_repos_calls.append(True)
            return [SimpleNamespace(slug="r1", name="R1", workspace="ws", default_branch="master"),
                    SimpleNamespace(slug="r2", name="R2", workspace="ws", default_branch="master")]
        def find_pr(self, repo, origin, destination):
            return None
        def commit_for_branch(self, repo, branch, resolved=""):
            commit_calls.append(repo)
            return "abc123"
        def tag_exists(self, slug, name):
            return False
        def create_tag(self, slug, name, commit):
            pass

    class StubCi:
        def pipeline_id_for_commit(self, repo, branch, commit):
            return 42

    created_tags = []
    orig_create_tag = StubClient.create_tag
    def capture_tag(self_client, slug, name, commit):
        created_tags.append((slug, name, commit))
    StubClient.create_tag = capture_tag

    monkeypatch.setattr("bbit_release.web.session.BitbucketClient", StubClient)
    monkeypatch.setattr("bbit_release.web.api.repos._circleci", lambda: StubCi())
    client.post("/api/session", json={"workspace": "ws", "token": "tok"})

    body = client.post("/api/tags", params={
        "origin": "release/x", "repo": "r1", "prefixes": "uat",
    }).json()
    assert body["ok"] is True
    assert body["envs"] == ["uat"]
    assert len(body["items"]) == 1
    assert body["items"][0]["repo"] == "r1"
    assert body["items"][0]["pipeline_id"] == 42

    # Only r1 was resolved — no _branch_repos_cached call, no r2 commit_for_branch
    assert commit_calls == ["r1"]
    assert branch_repos_calls == []
    assert created_tags == [("r1", "uat-42", "abc123")]


def test_generate_tags_scoped_repo_not_found(monkeypatch):
    """When `repo` param points to a non-existent repo, returns 400."""
    from bbit_release.bitbucket.client import BitbucketError

    class StubClient:
        def __init__(self, ws, tok, **kw):
            self.workspace = ws
        def session(self):
            return (
                SimpleNamespace(uuid="x", name="WS", slug="ws", is_private=True),
                "Jane (@jane)",
            )
        def diff(self, repo, destination, origin):
            return SimpleNamespace(files=[])
        def list_files(self, repo, ref, tree=""):
            return []
        def close(self):
            pass
        def repos_with_branch(self, origin, prefixes=None):
            return [SimpleNamespace(slug="r1", name="R1", workspace="ws", default_branch="master")]
        def find_pr(self, repo, origin, destination):
            return None
        def commit_for_branch(self, repo, branch, resolved=""):
            raise BitbucketError(f"repo {repo} not found")

    class StubCi:
        pass

    monkeypatch.setattr("bbit_release.web.session.BitbucketClient", StubClient)
    monkeypatch.setattr("bbit_release.web.api.repos._circleci", lambda: StubCi())
    client.post("/api/session", json={"workspace": "ws", "token": "tok"})

    resp = client.post("/api/tags", params={
        "origin": "release/x", "repo": "nonexistent", "prefixes": "uat",
    })
    assert resp.status_code == 400
    assert "nonexistent" in resp.json()["error"]


def test_generate_tags_scoped_no_pipeline(monkeypatch):
    """G6 — when the repo's commit has no CircleCI pipeline (pipeline_id=None),
    the scoped path returns an item with errors instead of crashing."""
    branch_repos_calls = []
    commit_calls = []

    class StubClient:
        def __init__(self, ws, tok, **kw):
            self.workspace = ws
        def session(self):
            return (
                SimpleNamespace(uuid="x", name="WS", slug="ws", is_private=True),
                "Jane (@jane)",
            )
        def diff(self, repo, destination, origin):
            return SimpleNamespace(files=[])
        def list_files(self, repo, ref, tree=""):
            return []
        def close(self):
            pass
        def repos_with_branch(self, origin, prefixes=None):
            branch_repos_calls.append(True)
            return [SimpleNamespace(slug="r1", name="R1", workspace="ws", default_branch="master")]
        def find_pr(self, repo, origin, destination):
            return None
        def commit_for_branch(self, repo, branch, resolved=""):
            commit_calls.append(repo)
            return "abc123"
        def tag_exists(self, slug, name):
            return False
        def create_tag(self, slug, name, commit):
            pass

    class StubCi:
        def pipeline_id_for_commit(self, repo, branch, commit):
            return None

    monkeypatch.setattr("bbit_release.web.session.BitbucketClient", StubClient)
    monkeypatch.setattr("bbit_release.web.api.repos._circleci", lambda: StubCi())
    client.post("/api/session", json={"workspace": "ws", "token": "tok"})

    body = client.post("/api/tags", params={
        "origin": "release/x", "repo": "r1", "prefixes": "uat",
    }).json()
    assert body["ok"] is True
    assert body["envs"] == ["uat"]
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["repo"] == "r1"
    assert item["pipeline_id"] is None
    assert item["created"] == []
    assert item["skipped"] == []
    assert len(item["errors"]) == 1
    assert "no tiene pipeline" in item["errors"][0]

    # Scoped path still applies: no _branch_repos_cached call, only r1 resolved.
    assert branch_repos_calls == []
    assert commit_calls == ["r1"]


def test_generate_tags_no_circleci_token_400(monkeypatch):
    """G5 — without CIRCLECI_TOKEN, POST /api/tags returns 400 with a clear message."""
    class StubClient:
        def __init__(self, ws, tok, **kw):
            self.workspace = ws
        def session(self):
            return (
                SimpleNamespace(uuid="x", name="WS", slug="ws", is_private=True),
                "Jane (@jane)",
            )
        def diff(self, repo, destination, origin):
            return SimpleNamespace(files=[])
        def list_files(self, repo, ref, tree=""):
            return []
        def close(self):
            pass
        def repos_with_branch(self, origin, prefixes=None):
            return [SimpleNamespace(slug="r1", name="R1", workspace="ws", default_branch="master")]
        def find_pr(self, repo, origin, destination):
            return None
        def commit_for_branch(self, repo, branch, resolved=""):
            return "abc123"
        def tag_exists(self, slug, name):
            return False
        def create_tag(self, slug, name, commit):
            pass

    monkeypatch.setattr("bbit_release.web.session.BitbucketClient", StubClient)
    monkeypatch.setattr("bbit_release.web.api.repos._circleci", lambda: None)
    client.post("/api/session", json={"workspace": "ws", "token": "tok"})

    resp = client.post("/api/tags", params={
        "origin": "release/x", "repo": "r1", "prefixes": "uat",
    })
    assert resp.status_code == 400
    body = resp.json()
    assert body["ok"] is False
    assert "CIRCLECI_TOKEN" in body["error"]


def test_generate_tags_batch_unchanged(monkeypatch):
    """When `repo` param is omitted, batch behavior preserved (global resolution + cfg fallback)."""
    class StubClient:
        def __init__(self, ws, tok, **kw):
            self.workspace = ws
        def session(self):
            return (
                SimpleNamespace(uuid="x", name="WS", slug="ws", is_private=True),
                "Jane (@jane)",
            )
        def diff(self, repo, destination, origin):
            return SimpleNamespace(files=[])
        def list_files(self, repo, ref, tree=""):
            return []
        def close(self):
            pass
        def repos_with_branch(self, origin, prefixes=None):
            return [SimpleNamespace(slug="r1", name="R1", workspace="ws", default_branch="master")]
        def find_pr(self, repo, origin, destination):
            return None
        def commit_for_branch(self, repo, branch, resolved=""):
            return "abc123"
        def tag_exists(self, slug, name):
            return False
        def create_tag(self, slug, name, commit):
            pass

    class StubCi:
        def pipeline_id_for_commit(self, repo, branch, commit):
            return 7

    monkeypatch.setattr("bbit_release.web.session.BitbucketClient", StubClient)
    monkeypatch.setattr("bbit_release.web.api.repos._circleci", lambda: StubCi())
    client.post("/api/session", json={"workspace": "ws", "token": "tok"})

    # No repo param → falls back to cfg.deploy_prefixes
    body = client.post("/api/tags", params={"origin": "release/x"}).json()
    assert body["ok"] is True
    assert body["envs"] == ["uat", "stgp", "prod"]  # from FakeConfig.deploy_prefixes
