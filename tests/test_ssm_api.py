"""Tests de la SSM View API (BBIT-2)."""

import pytest
from fastapi.testclient import TestClient

from bbit_release.cache import DEFAULT_AWS_REGION, get_cache, reset_cache
from bbit_release.ssm.store import SsmStore
from bbit_release.web.main import app

client = TestClient(app)


class FakeCfg:
    ssm_read_secrets = False
    ssm_environments: dict = {}
    aws_region = ""
    aws_environments: list = []
    aws_profile = ""
    aws_access_key_id = ""
    aws_secret_access_key = ""
    aws_session_token = ""
    aws_env: dict = {}

    def save_ssm_settings(self, ssm_environments=None, ssm_read_secrets=None):
        if ssm_environments is not None:
            if isinstance(ssm_environments, dict):
                self.ssm_environments = {k: v for k, v in ssm_environments.items() if v}
            else:
                self.ssm_environments = {
                    row["name"]: row["region"]
                    for row in ssm_environments
                    if isinstance(row, dict) and row.get("region")
                }
                self.aws_environments = [
                    {
                        "name": row.get("name"),
                        "region": row.get("region"),
                        "localstack": bool(row.get("localstack")),
                        "endpoint_url": row.get("endpoint_url") or "",
                    }
                    for row in ssm_environments
                    if isinstance(row, dict) and row.get("name")
                ]
        if ssm_read_secrets is not None:
            self.ssm_read_secrets = bool(ssm_read_secrets)
        return 1


@pytest.fixture(autouse=True)
def cfg(monkeypatch):
    fake = FakeCfg()
    monkeypatch.setattr(
        "bbit_release.web.api.ssm._require_session",
        lambda: {"client_id": "fake"},
    )
    monkeypatch.setattr(
        "bbit_release.web.api.ssm._session_config",
        lambda data: fake,
    )
    return fake


@pytest.fixture(autouse=True)
def cache(tmp_path):
    reset_cache()
    cache = get_cache(tmp_path / f"cache_{id(tmp_path)}.db")
    yield cache
    cache.invalidate_all()
    reset_cache()


def _seed(param="/config/app/db", env="uat", value="5", **kw):
    SsmStore(get_cache()).upsert(param, env, value, **kw)


def test_patch_and_get_values():
    resp = client.patch(
        "/api/ssm/values",
        json={"param": "/config/app", "environment": "uat", "value": '{"value": 1}'},
    )
    assert resp.status_code == 200
    got = client.get("/api/ssm/values", params={"param": "/config/app"}).json()
    assert got["param"] == "/config/app"
    assert len(got["environments"]) == 1
    assert got["environments"][0]["value"] == '{"value":1}'


def test_values_requires_param():
    assert client.get("/api/ssm/values", params={"param": ""}).status_code == 400


def test_patch_requires_fields():
    resp = client.patch("/api/ssm/values", json={"param": "", "environment": ""})
    assert resp.status_code == 400


def test_patch_toggle_preserves_value():
    _seed(env="uat", value='{"value": {"details": {"max": 100}}}', is_secret=True)
    resp = client.patch(
        "/api/ssm/values",
        json={"param": "/config/app/db", "environment": "uat", "is_secret": False},
    )
    assert resp.status_code == 200
    got = client.get("/api/ssm/values", params={"param": "/config/app/db"}).json()
    row = got["environments"][0]
    assert row["is_secret"] is False
    assert row["masked"] is False
    assert row["value"] == '{"value":{"details":{"max":100}}}'


def test_patch_toggle_preserves_source_and_arn():
    _seed(env="uat", value="s3cr3t", is_secret=True, source="secretsmanager", secret_arn="arn:aws:secretsmanager:sa-east-1:123:secret:x")
    resp = client.patch(
        "/api/ssm/values",
        json={"param": "/config/app/db", "environment": "uat", "is_secret": False},
    )
    assert resp.status_code == 200
    got = client.get("/api/ssm/values", params={"param": "/config/app/db"}).json()
    row = got["environments"][0]
    assert row["value"] == "s3cr3t"
    assert row["source"] == "secretsmanager"
    assert row["secret_arn"] == "arn:aws:secretsmanager:sa-east-1:123:secret:x"


def test_patch_toggle_missing_row_400():
    resp = client.patch(
        "/api/ssm/values",
        json={"param": "/config/app/db", "environment": "uat", "is_secret": True},
    )
    assert resp.status_code == 400


def test_patch_toggle_without_flags_400():
    _seed(env="uat", value="1")
    resp = client.patch(
        "/api/ssm/values",
        json={"param": "/config/app/db", "environment": "uat"},
    )
    assert resp.status_code == 400


def test_compare_updates_and_warning():
    _seed(env="uat", value='{"a": 1, "b": {"c": [1, 2]}}')
    _seed(env="prod", value='{"a": 1}')
    resp = client.post(
        "/api/ssm/compare",
        json={"param": "/config/app/db", "origin_env": "uat", "dest_env": "prod"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["origin_env"] == "uat"
    assert body["dest_env"] == "prod"
    assert body["warning"] is not None
    assert {"op": "insert", "path": "$.b", "value": '{"c":[1,2]}', "json": True} in body["updates"]


def test_compare_missing_value_400():
    _seed(env="uat", value="1")
    resp = client.post(
        "/api/ssm/compare",
        json={"param": "/config/app/db", "origin_env": "uat", "dest_env": "prod"},
    )
    assert resp.status_code == 400


def test_compare_same_env_400():
    _seed(env="uat", value="1")
    resp = client.post(
        "/api/ssm/compare",
        json={"param": "/config/app/db", "origin_env": "uat", "dest_env": "uat"},
    )
    assert resp.status_code == 400


def test_update_query_generates_aws_command():
    _seed(env="uat", value='{"value": {"details": {"max": 100}}}')
    _seed(env="prod", value='{"value": {"details": {"max": 90}}}')
    resp = client.get(
        "/api/ssm/update-query",
        params={"param": "/config/app/db", "env": "prod", "origin": "uat"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["region"] == DEFAULT_AWS_REGION
    assert body["type"] == "String"
    assert "aws ssm put-parameter --overwrite --type String" in body["command"]
    assert "--name '/config/app/db'" in body["command"]
    assert '--value \'{"value":{"details":{"max":100}}}\'' in body["command"]


def test_update_query_selected_changes_only():
    _seed(env="uat", value='{"a": 1, "b": 2}')
    _seed(env="prod", value='{"a": 1, "b": 9, "c": 3}')
    resp = client.get(
        "/api/ssm/update-query",
        params={"param": "/config/app/db", "env": "prod", "origin": "uat",
                "changes": "$.a,$.b"},
    )
    body = resp.json()
    assert body["changes"] == ["$.a", "$.b"]


def test_secret_masked_without_permission(cache):
    _seed(env="uat", value="s3cr3t", is_secret=True, source="ssm")
    got = client.get("/api/ssm/values", params={"param": "/config/app/db"}).json()
    assert got["environments"][0]["masked"] is True
    assert got["environments"][0]["value"] is None


def test_secret_revealed_with_permission(monkeypatch, cfg):
    _seed(env="uat", value="s3cr3t", is_secret=True)
    cfg.ssm_read_secrets = True
    got = client.get("/api/ssm/values", params={"param": "/config/app/db"}).json()
    assert got["environments"][0]["masked"] is False
    assert got["environments"][0]["value"] == "s3cr3t"


def test_secret_compare_forbidden_without_permission():
    _seed(env="uat", value="s3cr3t", is_secret=True)
    _seed(env="prod", value="p", is_secret=True)
    resp = client.post(
        "/api/ssm/compare",
        json={"param": "/config/app/db", "origin_env": "uat", "dest_env": "prod"},
    )
    assert resp.status_code == 403


def test_secret_update_query_forbidden_without_permission():
    _seed(env="uat", value="s3cr3t", is_secret=True)
    _seed(env="prod", value="p", is_secret=True)
    resp = client.get(
        "/api/ssm/update-query",
        params={"param": "/config/app/db", "env": "prod", "origin": "uat"},
    )
    assert resp.status_code == 403


def test_get_environments_lists_map_and_flag(cache):
    client.patch(
        "/api/ssm/environments",
        json={"ssm_environments": {"uat": "sa-east-1", "prod": "us-east-1"},
              "ssm_read_secrets": True},
    )
    resp = client.get("/api/ssm/environments")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ssm_environments"]["uat"] == "sa-east-1"
    assert body["ssm_environments"]["prod"] == "us-east-1"
    assert body["ssm_read_secrets"] is True
    assert "environments" in body


def test_get_environments_lists_full_rows(cache):
    resp = client.get("/api/ssm/environments")
    assert resp.status_code == 200
    body = resp.json()
    assert "environments" in body
    assert "ssm_environments" in body


def test_patch_environments_accepts_rows_with_docker_endpoint():
    resp = client.patch(
        "/api/ssm/environments",
        json={"environments": [
            {"name": "qa", "region": "us-west-1", "localstack": True,
             "endpoint_url": "http://localhost:4566"},
            {"name": "dev", "region": "us-west-2"},
        ]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["environments"][0]["localstack"] is True
    assert body["environments"][0]["endpoint_url"] == "http://localhost:4566"
    assert body["ssm_environments"]["dev"] == "us-west-2"


def test_update_query_appends_endpoint_url_for_docker_env(cfg):
    cfg.aws_environments = [
        {"name": "qa", "region": "us-west-1", "localstack": True,
         "endpoint_url": "http://localhost:4566"},
    ]
    cfg.ssm_environments = {"qa": "us-west-1"}
    _seed(env="uat", value='{"value": {"details": {"max": 100}}}')
    _seed(env="qa", value='{"value": {"details": {"max": 90}}}')
    resp = client.get(
        "/api/ssm/update-query",
        params={"param": "/config/app/db", "env": "qa", "origin": "uat"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["endpoint_url"] == "http://localhost:4566"
    assert "--endpoint-url 'http://localhost:4566'" in body["command"]


def test_patch_environments_persists(cache):
    resp = client.patch(
        "/api/ssm/environments",
        json={"ssm_environments": {"uat": "sa-east-1", "prod": "us-east-1"}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ssm_environments"]["uat"] == "sa-east-1"
    assert body["ssm_environments"]["prod"] == "us-east-1"

    # el query ahora usa la región configurada para el destino
    _seed(env="uat", value="1")
    _seed(env="prod", value="2")
    q = client.get(
        "/api/ssm/update-query",
        params={"param": "/config/app/db", "env": "prod", "origin": "uat"},
    ).json()
    assert "--region us-east-1" in q["command"]
    assert q["endpoint_url"] == ""


def test_values_fetch_live_reports_unavailable(cfg):
    cfg.aws_environments = [{"name": "qa", "region": "us-west-2"}]
    _seed(env="qa", value='{"value": 1}')
    resp = client.get("/api/ssm/values", params={"param": "/config/app/db", "fetch": 1})
    assert resp.status_code == 200
    body = resp.json()
    assert "live" in body
    assert body["live"]["qa"]["status"] == "unavailable"


def test_apply_requires_fields():
    resp = client.post("/api/ssm/apply", json={"param": "", "env": ""})
    assert resp.status_code == 400


def test_apply_secret_forbidden_without_permission():
    _seed(env="uat", value="s3cr3t", is_secret=True)
    _seed(env="prod", value="p", is_secret=True)
    resp = client.post(
        "/api/ssm/apply",
        json={"param": "/config/app/db", "env": "prod", "origin": "uat"},
    )
    assert resp.status_code == 403


def test_apply_no_credentials_400(cfg):
    _seed(env="uat", value="1")
    _seed(env="prod", value="2")
    cfg.aws_environments = [{"name": "prod", "region": "us-east-1"}]
    cfg.ssm_environments = {"prod": "us-east-1"}
    resp = client.post(
        "/api/ssm/apply",
        json={"param": "/config/app/db", "env": "prod", "origin": "uat"},
    )
    assert resp.status_code == 400
    assert "Sin credenciales AWS" in resp.json()["detail"]


def test_apply_executes_put_parameter(monkeypatch, cfg):
    _seed(env="uat", value='{"value": {"details": {"max": 100}}}')
    _seed(env="prod", value='{"value": {"details": {"max": 90}}}')
    cfg.aws_environments = [{"name": "prod", "region": "us-east-1"}]
    cfg.ssm_environments = {"prod": "us-east-1"}

    calls: dict = {}

    class FakeClient:
        def put_parameter(self, **kw):
            calls.update(kw)

    class FakeSession:
        available = True

        def client(self, service):
            assert service == "ssm"
            return FakeClient()

    monkeypatch.setattr("bbit_release.aws.session.AwsSession", lambda **kw: FakeSession())

    resp = client.post(
        "/api/ssm/apply",
        json={"param": "/config/app/db", "env": "prod", "origin": "uat"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert calls["Name"] == "/config/app/db"
    assert calls["Type"] == "String"
    assert calls["Overwrite"] is True
    assert calls["Value"] == '{"value":{"details":{"max":100}}}'
    assert "--region us-east-1" in body["command"]
    assert "aws ssm put-parameter" in body["command"]