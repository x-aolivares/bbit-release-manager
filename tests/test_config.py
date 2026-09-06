import os
import pytest

from bbit_release.cache import get_cache, reset_cache
from bbit_release.config import DEFAULT_CLIENT_ALIAS, Config


@pytest.fixture(autouse=True)
def _clean_db(tmp_path, monkeypatch):
    reset_cache()
    Config.reset()
    monkeypatch.setenv("BBIT_CLIENT_MARKER", str(tmp_path / "client_id"))
    cache = get_cache(tmp_path / "test_config.db")
    yield cache
    Config.reset()
    reset_cache()


def _cfg():
    return Config()


def test_default_connection_empty(tmp_path):
    cfg = _cfg()
    assert cfg.bitbucket_token == ""
    assert cfg.workspace == ""
    assert cfg.circleci_token == ""
    assert cfg.aws_profile == ""
    assert cfg.default_branch == "master"
    assert cfg.ssm_prefixes == ["/config", "/common"]
    assert cfg.deploy_prefixes == ["uat", "stgp", "prod"]
    assert cfg.is_configured is False


def test_default_client_local(tmp_path):
    cfg = _cfg()
    assert cfg.client_alias == DEFAULT_CLIENT_ALIAS
    assert len(cfg.client_id) == 36  # uuid
    client = get_cache().get_client(cfg.client_id)
    assert client["alias"] == DEFAULT_CLIENT_ALIAS


def test_save_tokens_persists_to_authentication(tmp_path):
    cfg = _cfg()
    cfg.save_tokens(bitbucket_token="tok", circleci_token="cci", workspace="ws")

    assert cfg.bitbucket_token == "tok"
    assert cfg.workspace == "ws"
    assert cfg.circleci_token == "cci"

    auth = get_cache().get_authentication(cfg.client_id, "Bitbucket")
    assert auth["env"]["BITBUCKET_TOKEN"] == "tok"
    assert auth["env"]["BITBUCKET_WORKSPACE"] == "ws"
    ci = get_cache().get_authentication(cfg.client_id, "CircleCi")
    assert ci["env"]["CIRCLECI_TOKEN"] == "cci"
    # la fila de conexión NO existe (settings todavía no se guardaron): las
    # credenciales viven solo en service_authentication
    assert get_cache().get_connection() is None


def test_save_tokens_idempotent_keeps_created_at(tmp_path):
    cfg = _cfg()
    cfg.save_tokens(bitbucket_token="b", workspace="w")
    sid1 = cfg.save_tokens(bitbucket_token="c", workspace="w")
    assert sid1 == sid1  # upsert: un solo registro

    cache = get_cache(tmp_path / "test_config.db")
    row = cache._fetchone(
        "SELECT sa_created_at, sa_updated_at FROM service_authentication "
        "WHERE c_id = ? AND sp_id = (SELECT sp_id FROM service_provider WHERE sp_name = 'Bitbucket')",
        (cfg.client_id,),
    )
    assert row is not None
    created, updated = row
    assert created <= updated
    assert cache._fetchone("SELECT COUNT(*) FROM service_authentication", ())[0] == 1


def test_save_tokens_noop_without_secret(tmp_path):
    cfg = _cfg()
    sid = cfg.save_tokens(workspace="w")
    assert sid == -1
    assert get_cache().get_connection() is None
    assert get_cache().get_authentication(cfg.client_id, "Bitbucket") is None


def test_save_service_aws(tmp_path):
    cfg = _cfg()
    cfg.save_service("AWS", {"AWS_PROFILE": "prod", "AWS_REGION": "us-east-1"})
    assert cfg.aws_profile == "prod"
    assert cfg.aws_region == "us-east-1"


def test_stored_services_and_states(tmp_path):
    cfg = _cfg()
    assert cfg.stored_services() == {}
    # workspace solo no cuenta como credencial bitbucket
    cfg.save_tokens(workspace="ws")
    assert "Bitbucket" not in cfg.stored_services()
    cfg.save_tokens(bitbucket_token="t", workspace="ws")
    stored = cfg.stored_services()
    assert "Bitbucket" in stored
    assert "CircleCi" not in stored
    states = cfg.service_states()
    assert states["bitbucket"]["stored"] is True
    assert states["aws"]["stored"] is False
    assert states["bitbucket"]["expires_at"] is None


def test_set_client_alias_switches(tmp_path):
    cfg = _cfg().set_client_alias("compilador-2")
    assert cfg.client_alias == "compilador-2"
    cfg.save_tokens(bitbucket_token="t2", workspace="ws2")

    other = _cfg().set_client_alias("manager")
    assert other.bitbucket_token == ""
    other.save_tokens(bitbucket_token="t3", workspace="ws3")

    back = _cfg().set_client_alias("compilador-2")
    assert back.bitbucket_token == "t2"


def test_for_client_resolves_credentials_without_marker(tmp_path):
    """Config.for_client resuelve credenciales del cliente indicado sin tocar
    el marker global (multi-usuario web)."""
    a = _cfg().set_client_alias("cliente-a")
    a.save_tokens(bitbucket_token="tA", aws_profile="profA", workspace="wA")
    b = _cfg().set_client_alias("cliente-b")
    b.save_tokens(bitbucket_token="tB", aws_profile="profB", workspace="wB")

    cfg_a = Config.for_client(a.client_id)
    cfg_b = Config.for_client(b.client_id)

    assert cfg_a.bitbucket_token == "tA"
    assert cfg_a.aws_profile == "profA"
    assert cfg_b.bitbucket_token == "tB"
    assert cfg_b.aws_profile == "profB"
    assert cfg_a.client_id == a.client_id
    assert cfg_b.client_id == b.client_id

    # no toca el marker: _cfg() sigue en cliente-b (ultimo set)
    assert _cfg().bitbucket_token == "tB"


def test_for_client_aws_env_exposes_ssm_decrypt(tmp_path):
    cfg = _cfg()
    cfg.save_service("AWS", {"AWS_PROFILE": "prod", "AWS_REGION": "us-east-1", "SSM_DECRYPT": "true"})
    for_client = Config.for_client(cfg.client_id)
    assert for_client.aws_env.get("SSM_DECRYPT") == "true"
    assert for_client.aws_profile == "prod"
    assert for_client.aws_region == "us-east-1"


def test_save_filters_persists(tmp_path):
    cfg = _cfg()
    cfg.save_tokens(bitbucket_token="t", workspace="w")
    cfg.save_filters(project_prefixes="trans", exclude_repos="orders-app,billing")

    assert cfg.project_prefixes == ["trans"]
    assert cfg.exclude_repos == ["orders-app", "billing"]

    conn = get_cache().get_connection()
    assert conn["settings"]["project_prefixes"] == ["trans"]
    assert conn["settings"]["exclude_repos"] == ["orders-app", "billing"]


def test_clear_filters_removes(tmp_path):
    cfg = _cfg()
    cfg.save_tokens(bitbucket_token="t", workspace="w")
    cfg.save_filters(project_prefixes="trans", exclude_repos="orders-app")
    cfg.clear_filters()

    assert cfg.project_prefixes == []
    assert cfg.exclude_repos == []

    conn = get_cache().get_connection()
    assert conn["settings"]["project_prefixes"] == []
    assert conn["settings"]["exclude_repos"] == []


def test_remove_credentials_keeps_client_and_settings(tmp_path):
    cfg = _cfg()
    cfg.save_tokens(bitbucket_token="t", workspace="w")
    cfg.save_filters(project_prefixes="trans")
    cid = cfg.client_id
    cfg.remove_credentials()

    assert cfg.bitbucket_token == ""
    assert cfg.workspace == ""
    assert get_cache().get_authentication(cid, "Bitbucket") is None
    assert get_cache().get_client(cid) is not None  # la identidad queda
    assert get_cache().get_connection() is not None  # settings quedan
    assert cfg.project_prefixes == ["trans"]


def test_exclude_repos_property_lowercased(tmp_path):
    cfg = _cfg()
    cfg.save_tokens(bitbucket_token="t", workspace="w")
    cfg.save_filters(exclude_repos="Orders-App,billing-app,X")
    assert cfg.exclude_repos == ["orders-app", "billing-app", "x"]


def test_migrate_legacy_env_base_imports_and_deletes(tmp_path):
    from pathlib import Path

    from bbit_release.config import Config as C
    from bbit_release.config import _migrate_legacy_env_base

    legacy = Path(tmp_path) / "env.base"
    legacy.write_text(
        "BITBUCKET_WORKSPACE=ws\n"
        "BITBUCKET_TOKEN=secret\n"
        "CIRCLECI_TOKEN=cci\n"
        "SSM_PREFIXES=/config,/custom\n",
        encoding="utf-8",
    )

    assert _migrate_legacy_env_base(legacy) is True
    assert not legacy.exists()

    cfg = C()
    assert cfg.workspace == "ws"
    assert cfg.bitbucket_token == "secret"
    assert cfg.circleci_token == "cci"
    assert cfg.ssm_prefixes == ["/config", "/custom"]
    assert cfg.deploy_prefixes == ["uat", "stgp", "prod"]


def test_migrate_connection_credentials_splits(tmp_path, monkeypatch):
    """Fila de conexión con credenciales viejas (BBIT-3) → service_authentication."""
    from bbit_release.config import Config as C
    from bbit_release.config import _migrate_connection_credentials

    cache = get_cache()
    cache.save_connection(
        {
            "bypass_cache": False,
            "settings": {"project_prefixes": ["trans"]},
            "credentials": {
                "bitbucket": {"url": "https://bitbucket.org", "workspace": "ws", "username": "u", "token": "tok"},
                "circle": {"token": "cci", "vcs": "bb", "org": "ws"},
                "aws": {"profile": "prod", "region": "us-east-1"},
            },
        }
    )
    assert _migrate_connection_credentials() is True

    cfg = C()
    assert cfg.bitbucket_token == "tok"
    assert cfg.circleci_token == "cci"
    assert cfg.aws_profile == "prod"
    conn = cache.get_connection()
    assert "credentials" not in conn
    assert conn["settings"]["project_prefixes"] == ["trans"]
    auth = cache.get_authentication(cfg.client_id, "Bitbucket")
    assert auth["env"]["BITBUCKET_TOKEN"] == "tok"


def test_migrate_connection_credentials_idempotent(tmp_path):
    from bbit_release.config import _migrate_connection_credentials

    cache = get_cache()
    cache.save_connection({"bypass_cache": False, "settings": {}})
    assert _migrate_connection_credentials() is False


def test_clear_all_preserves_connection_and_auth(tmp_path):
    cfg = _cfg()
    cfg.save_tokens(bitbucket_token="t", workspace="w")
    cfg.save_filters(project_prefixes="trans")

    cache = get_cache()
    cache.clear_all()

    assert cache.get_connection() is not None
    assert get_cache().get_authentication(cfg.client_id, "Bitbucket") is not None
    assert cfg.bitbucket_token == "t"
    assert cfg.workspace == "w"
    assert cfg.project_prefixes == ["trans"]


def test_expiring_state(tmp_path):
    import time

    cfg = _cfg()
    cfg.save_tokens(bitbucket_token="t", workspace="w", expires_at=time.time() + 3600)
    assert cfg.service_states()["bitbucket"]["warning"] == "expiring"
    cfg.save_tokens(bitbucket_token="t", workspace="w", expires_at=time.time() - 3600)
    assert cfg.service_states()["bitbucket"]["warning"] == "expired"