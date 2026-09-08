"""Tests del SsmStore (`ssm_values`, BBIT-2)."""

import pytest

from bbit_release.cache import get_cache, reset_cache
from bbit_release.ssm.store import SsmStore


@pytest.fixture(autouse=True)
def cache(tmp_path):
    reset_cache()
    cache = get_cache(tmp_path / f"cache_{id(tmp_path)}.db")
    yield cache
    cache.invalidate_all()
    reset_cache()


def test_upsert_and_get(cache):
    store = SsmStore(cache)
    store.upsert("/config/x/name", "uat", {"value": 1})
    row = store.get("/config/x/name", "uat")
    assert row["param"] == "/config/x/name"
    assert row["environment"] == "uat"
    assert row["value"] == '{"value":1}'
    assert row["is_secret"] is False


def test_upsert_upserts_same_pair(cache):
    store = SsmStore(cache)
    sid1 = store.upsert("/config/x", "uat", {"a": 1})
    store.upsert("/config/x", "uat", {"a": 2})
    rows = store.for_param("/config/x")
    assert len(rows) == 1
    row = rows[0]
    assert row["value"] == '{"a":2}'
    assert row["id"] == sid1  # misma fila (UNIQUE param+env)


def test_for_param_per_env(cache):
    store = SsmStore(cache)
    store.upsert("/config/x", "uat", 1)
    store.upsert("/config/x", "prod", 2)
    rows = store.for_param("/config/x")
    assert [r["environment"] for r in rows] == ["prod", "uat"]  # orden alfabético


def test_require_param_and_env(cache):
    store = SsmStore(cache)
    with pytest.raises(ValueError):
        store.upsert("", "uat", 1)


def test_delete(cache):
    store = SsmStore(cache)
    store.upsert("/config/x", "uat", 1)
    assert store.delete("/config/x", "uat") is True
    assert store.get("/config/x", "uat") is None
    assert store.delete("/config/x", "uat") is False


def test_enrich_param_masks_secrets(cache):
    store = SsmStore(cache)
    store.upsert("/sec/x", "uat", "s3cr3t", is_secret=True)
    store.upsert("/sec/x", "prod", "prod-secret", is_secret=True)
    overlay = store.enrich_param("/sec/x", read_secrets=False)
    assert overlay["type"] == "secret"
    assert overlay["env_values"] == {"uat": "••••••", "prod": "••••••"}
    reveal = store.enrich_param("/sec/x", read_secrets=True)
    assert reveal["env_values"] == {"uat": "s3cr3t", "prod": "prod-secret"}


def test_enrich_param_plain(cache):
    store = SsmStore(cache)
    store.upsert("/config/y", "uat", 5)
    overlay = store.enrich_param("/config/y")
    assert overlay["type"] == "ssm"
    assert overlay["env_values"] == {"uat": 5}

    assert store.enrich_param("/missing") == {"type": "ssm", "env_values": {}}