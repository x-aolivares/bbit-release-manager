import json
import time
from pathlib import Path

import pytest
from concurrent.futures import ThreadPoolExecutor

from bbit_release.cache import ReleaseCache, _cache_key, reset_cache


@pytest.fixture(autouse=True)
def _clean():
    reset_cache()
    yield
    reset_cache()


def _make_cache(tmp_path: Path) -> ReleaseCache:
    return ReleaseCache(db_path=tmp_path / "test.db")


def test_cache_key_deterministic():
    k1 = _cache_key("release/x", "master", ["trans"], {"billing"})
    k2 = _cache_key("release/x", "master", ["trans"], {"billing"})
    assert k1 == k2


def test_cache_key_differs_with_different_origin():
    k1 = _cache_key("release/a", "master", None, None)
    k2 = _cache_key("release/b", "master", None, None)
    assert k1 != k2


def test_cache_key_differs_with_different_exclude():
    k1 = _cache_key("release/x", "master", None, {"a"})
    k2 = _cache_key("release/x", "master", None, {"b"})
    assert k1 != k2


def test_repos_roundtrip(tmp_path):
    cache = _make_cache(tmp_path)
    repos = [{"slug": "r1", "name": "R1", "workspace": "ws", "default_branch": "master"}]
    cache.set_repos("release/x", "master", ["trans"], {"billing"}, repos)
    got = cache.get_repos("release/x", "master", ["trans"], {"billing"})
    assert got == repos


def test_repos_miss(tmp_path):
    cache = _make_cache(tmp_path)
    assert cache.get_repos("release/x", "master", None, None) is None


def test_scan_roundtrip(tmp_path):
    cache = _make_cache(tmp_path)
    data = {"origin": "release/x", "repos": [{"slug": "r1"}], "stats": {"repos": 1}}
    cache.set_scan("release/x", "master", ["uat"], set(), data)
    got = cache.get_scan("release/x", "master", ["uat"], set())
    assert got == data


def test_scan_miss(tmp_path):
    cache = _make_cache(tmp_path)
    assert cache.get_scan("release/x", "master", None, None) is None


def test_diff_roundtrip(tmp_path):
    cache = _make_cache(tmp_path)
    data = {"origin": "release/x", "params": [{"param": "/config/a"}]}
    cache.set_diff("release/x", "master", None, None, data)
    got = cache.get_diff("release/x", "master", None, None)
    assert got == data


def test_diff_miss(tmp_path):
    cache = _make_cache(tmp_path)
    assert cache.get_diff("release/x", "master", None, None) is None


def test_master_roundtrip(tmp_path):
    cache = _make_cache(tmp_path)
    params = {("/config/a", ""), ("/config/b", "arn:aws:ssm:::b")}
    cache.set_master("r1", "master", params)
    got = cache.get_master("r1", "master")
    assert got == params


def test_master_miss(tmp_path):
    cache = _make_cache(tmp_path)
    assert cache.get_master("r1", "master") is None


def test_invalidate_repos(tmp_path):
    cache = _make_cache(tmp_path)
    cache.set_repos("release/x", "master", None, None, [{"slug": "r1"}])
    cache.invalidate("release/x", "master", None, None)
    assert cache.get_repos("release/x", "master", None, None) is None


def test_invalidate_scan(tmp_path):
    cache = _make_cache(tmp_path)
    cache.set_scan("release/x", "master", None, None, {"origin": "x"})
    cache.invalidate("release/x", "master", None, None)
    assert cache.get_scan("release/x", "master", None, None) is None


def test_invalidate_diff(tmp_path):
    cache = _make_cache(tmp_path)
    cache.set_diff("release/x", "master", None, None, {"origin": "x"})
    cache.invalidate("release/x", "master", None, None)
    assert cache.get_diff("release/x", "master", None, None) is None


def test_invalidate_does_not_touch_master(tmp_path):
    cache = _make_cache(tmp_path)
    cache.set_master("r1", "master", {("/config/a", "")})
    cache.invalidate("release/x", "master", None, None)
    assert cache.get_master("r1", "master") == {("/config/a", "")}


def test_invalidate_all_clears_everything(tmp_path):
    cache = _make_cache(tmp_path)
    cache.set_repos("release/x", "master", None, None, [{"slug": "r1"}])
    cache.set_scan("release/x", "master", None, None, {"origin": "x"})
    cache.set_diff("release/x", "master", None, None, {"origin": "x"})
    cache.set_master("r1", "master", {("/config/a", "")})
    cache.invalidate_all()
    assert cache.get_repos("release/x", "master", None, None) is None
    assert cache.get_scan("release/x", "master", None, None) is None
    assert cache.get_diff("release/x", "master", None, None) is None
    assert cache.get_master("r1", "master") is None


def test_overwrite_replaces(tmp_path):
    cache = _make_cache(tmp_path)
    cache.set_repos("release/x", "master", None, None, [{"slug": "old"}])
    cache.set_repos("release/x", "master", None, None, [{"slug": "new"}])
    got = cache.get_repos("release/x", "master", None, None)
    assert got == [{"slug": "new"}]


def test_persists_after_reopen(tmp_path):
    path = tmp_path / "test.db"
    c1 = ReleaseCache(db_path=path)
    c1.set_repos("release/x", "master", None, None, [{"slug": "r1"}])
    c1.close()
    c2 = ReleaseCache(db_path=path)
    got = c2.get_repos("release/x", "master", None, None)
    assert got == [{"slug": "r1"}]
    c2.close()


def test_empty_prefixes_and_exclude():
    k1 = _cache_key("release/x", "master", None, None)
    k2 = _cache_key("release/x", "master", [], set())
    assert k1 == k2


def test_master_not_overwritten_by_different_slug(tmp_path):
    cache = _make_cache(tmp_path)
    cache.set_master("r1", "master", {("/config/a", "")})
    cache.set_master("r2", "master", {("/config/b", "")})
    assert cache.get_master("r1", "master") == {("/config/a", "")}
    assert cache.get_master("r2", "master") == {("/config/b", "")}


def test_branch_repos_roundtrip(tmp_path):
    cache = _make_cache(tmp_path)
    repos = [{"repo_name": "r1", "name": "R1", "workspace": "ws", "default_branch": "master"}]
    cache.set_branch_repos("release/x", "master", ["trans"], {"billing"}, repos)
    got = cache.get_branch_repos("release/x", "master", ["trans"], {"billing"})
    assert got == repos


def test_branch_repos_miss(tmp_path):
    cache = _make_cache(tmp_path)
    assert cache.get_branch_repos("release/x", "master", None, None) is None


def test_branch_repos_distinct_by_destination(tmp_path):
    cache = _make_cache(tmp_path)
    cache.set_branch_repos("release/x", "master", None, None, [{"repo_name": "a"}])
    cache.set_branch_repos("release/x", "staging", None, None, [{"repo_name": "b"}])
    assert cache.get_branch_repos("release/x", "master", None, None) == [{"repo_name": "a"}]
    assert cache.get_branch_repos("release/x", "staging", None, None) == [{"repo_name": "b"}]


def test_branch_repos_overwrite(tmp_path):
    cache = _make_cache(tmp_path)
    cache.set_branch_repos("release/x", "master", None, None, [{"repo_name": "old"}])
    cache.set_branch_repos("release/x", "master", None, None, [{"repo_name": "new"}])
    assert cache.get_branch_repos("release/x", "master", None, None) == [{"repo_name": "new"}]


def test_invalidate_clears_branch_repos(tmp_path):
    cache = _make_cache(tmp_path)
    cache.set_branch_repos("release/x", "master", None, None, [{"repo_name": "a"}])
    cache.invalidate("release/x", "master", None, None)
    assert cache.get_branch_repos("release/x", "master", None, None) is None


def test_invalidate_all_clears_branch_repos(tmp_path):
    cache = _make_cache(tmp_path)
    cache.set_branch_repos("release/x", "master", None, None, [{"repo_name": "a"}])
    cache.invalidate_all()
    assert cache.get_branch_repos("release/x", "master", None, None) is None


def test_thread_safe_concurrent_access(tmp_path):
    """Muchos hilos leen y escriben la misma conexión sin InterfaceError."""
    cache = _make_cache(tmp_path)
    n = 32

    def worker(i: int) -> None:
        slug = f"r{i}"
        cache.set_master(slug, "master", {(f"/config/{i}", "")})
        got = cache.get_master(slug, "master")
        assert got == {(f"/config/{i}", "")}
        cache.set_branch_repos(slug, "master", None, None, [{"repo_name": slug}])

    with ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(worker, range(n)))

    for i in range(n):
        assert cache.get_master(f"r{i}", "master") == {(f"/config/{i}", "")}


