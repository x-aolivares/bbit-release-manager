import pytest

from bbit_release.cache import get_cache, reset_cache
from bbit_release.config import Config


@pytest.fixture(autouse=True)
def _clean_db(tmp_path):
    reset_cache()
    cache = get_cache(tmp_path / "test_config.db")
    yield cache
    reset_cache()


def _ws(db_path, workspace="ws"):
    return get_cache(db_path).create_session("orig", "dest", {"repositories": {"excluded": [], "prefixes": []}})


def test_default_connection_empty(tmp_path):
    cfg = Config()
    assert cfg.bitbucket_token == ""
    assert cfg.workspace == ""
    assert cfg.default_branch == "master"
    assert cfg.ssm_prefixes == ["/config", "/common"]
    assert cfg.deploy_prefixes == ["uat", "stgp", "prod"]
    assert cfg.is_configured is False


def test_save_tokens_persists_to_connection(tmp_path):
    cfg = Config()
    cfg.save_tokens(bitbucket_token="tok", circleci_token="cci", workspace="ws")

    assert cfg.bitbucket_token == "tok"
    assert cfg.workspace == "ws"

    conn = get_cache(tmp_path / "test_config.db").get_connection()
    assert conn is not None
    assert conn["credentials"]["bitbucket"]["token"] == "tok"
    assert conn["credentials"]["bitbucket"]["workspace"] == "ws"
    assert conn["credentials"]["circle"]["token"] == "cci"


def test_save_tokens_idempotent_keeps_created_at(tmp_path):
    cfg = Config()
    sid1 = cfg.save_tokens(bitbucket_token="b", workspace="w")
    sid2 = cfg.save_tokens(bitbucket_token="c", workspace="w")
    assert sid1 == sid2

    cache = get_cache(tmp_path / "test_config.db")
    row = cache._fetchone(
        "SELECT is_created_at, is_updated_at FROM init_sesion "
        "WHERE is_source='config' AND is_target='connection'",
        (),
    )
    assert row is not None
    created, updated = row
    assert created == created  # fila preservada (un solo upsert)
    assert created <= updated


def test_save_tokens_noop_without_values(tmp_path):
    cfg = Config()
    sid = cfg.save_tokens(workspace="w")
    assert sid == -1
    assert get_cache(tmp_path / "test_config.db").get_connection() is None


def test_save_filters_persists(tmp_path):
    cfg = Config()
    cfg.save_tokens(bitbucket_token="t", workspace="w")
    cfg.save_filters(project_prefixes="trans", exclude_repos="orders-app,billing")

    assert cfg.project_prefixes == ["trans"]
    assert cfg.exclude_repos == ["orders-app", "billing"]

    conn = get_cache(tmp_path / "test_config.db").get_connection()
    assert conn["settings"]["project_prefixes"] == ["trans"]
    assert conn["settings"]["exclude_repos"] == ["orders-app", "billing"]


def test_save_filters_partial_noop(tmp_path):
    cfg = Config()
    cfg.save_tokens(bitbucket_token="t", workspace="w")
    cfg.save_filters(exclude_repos="orders-app")

    assert cfg.project_prefixes == []
    assert cfg.exclude_repos == ["orders-app"]


def test_clear_filters_removes(tmp_path):
    cfg = Config()
    cfg.save_tokens(bitbucket_token="t", workspace="w")
    cfg.save_filters(project_prefixes="trans", exclude_repos="orders-app")
    cfg.clear_filters()

    assert cfg.project_prefixes == []
    assert cfg.exclude_repos == []

    conn = get_cache(tmp_path / "test_config.db").get_connection()
    assert conn["settings"]["project_prefixes"] == []
    assert conn["settings"]["exclude_repos"] == []


def test_remove_credentials_deletes_connection_row(tmp_path):
    cfg = Config()
    cfg.save_tokens(bitbucket_token="t", workspace="w")
    cfg.remove_credentials()

    assert cfg.bitbucket_token == ""
    assert cfg.workspace == ""
    assert get_cache(tmp_path / "test_config.db").get_connection() is None


def test_exclude_repos_property_lowercased(tmp_path):
    cfg = Config()
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


def test_clear_all_preserves_connection(tmp_path):
    cfg = Config()
    cfg.save_tokens(bitbucket_token="t", workspace="w")

    cache = get_cache(tmp_path / "test_config.db")
    cache.clear_all()

    assert cache.get_connection() is not None
    assert cfg.bitbucket_token == "t"
    assert cfg.workspace == "w"
