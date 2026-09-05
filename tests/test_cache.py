import json
import time
from pathlib import Path

import pytest
from concurrent.futures import ThreadPoolExecutor

from bbit_release import cache as cache_mod
from bbit_release.cache import ReleaseCache, reset_cache


@pytest.fixture(autouse=True)
def _clean():
    reset_cache()
    yield
    reset_cache()


def _make_cache(tmp_path: Path) -> ReleaseCache:
    return ReleaseCache(db_path=tmp_path / "test.db")


def _invoke_details(prefixes=None, exclude=None) -> dict:
    return {
        "repositories": {
            "excluded": sorted(exclude or []),
            "prefixes": sorted(prefixes or []),
        }
    }


# -- catálogo ---------------------------------------------------------------

def test_seeds_providers(tmp_path):
    cache = _make_cache(tmp_path)
    bb = cache.get_provider("Bitbucket")
    assert bb is not None
    assert bb["details"]["base_url"] == "https://api.bitbucket.org/2.0"
    ci = cache.get_provider("CircleCi")
    assert ci is not None
    assert ci["details"]["vcs"] == "bb"
    assert cache.get_provider("NoExiste") is None


def test_seeds_request_types(tmp_path):
    cache = _make_cache(tmp_path)
    for name, ttl in [
        ("get_user_repositories", 300),
        ("get_branch_repositories", 300),
        ("scan_release", 1800),
        ("diff_ssm", 1800),
        ("get_master_params", 3600),
    ]:
        rt = cache.get_rt(name)
        assert rt is not None, name
        assert rt["ttl_seconds"] == ttl, name
        assert rt["service_url"], name
    assert cache.get_rt("NoExiste") is None


# -- sesiones ---------------------------------------------------------------

def test_create_and_get_session(tmp_path):
    cache = _make_cache(tmp_path)
    sid = cache.create_session("release/x", "master", {"repositories": {}})
    got = cache.get_session(sid)
    assert got["source"] == "release/x"
    assert got["target"] == "master"
    assert json.loads(got["details"]) == {
        "source_branch": "release/x",
        "target_branch": "master",
        "config": {"repositories": {}},
    }


def test_find_session_reuses_same_config(tmp_path):
    cache = _make_cache(tmp_path)
    details = {"repositories": {"excluded": ["billing"], "prefixes": ["trans"]}}
    a = cache.find_session("release/x", "master", details)
    b = cache.find_session("release/x", "master", details)
    assert a == b
    assert len(cache._fetchall("SELECT is_id FROM init_sesion")) == 1


def test_find_session_new_when_config_differs(tmp_path):
    cache = _make_cache(tmp_path)
    a = cache.find_session("release/x", "master", {"repositories": {}})
    b = cache.find_session("release/x", "master", {"repositories": {"excluded": ["x"]}})
    assert a != b


# -- requests / TTL ---------------------------------------------------------

def test_add_request_and_latest_success(tmp_path):
    cache = _make_cache(tmp_path)
    sid = cache.create_session("release/x", "master", {})
    rt = cache.get_rt("scan_release")
    rq = cache.add_request(sid, rt["id"], {"origin": "release/x"})
    assert rq > 0
    hit = cache.latest_success(rt["id"], sid)
    assert hit is not None
    assert hit["payload"] == {"origin": "release/x"}


def test_latest_success_ignores_failed_requests(tmp_path):
    cache = _make_cache(tmp_path)
    sid = cache.create_session("release/x", "master", {})
    rt = cache.get_rt("scan_release")
    cache.add_request(sid, rt["id"], {"error": "boom"}, status="FAILED")
    assert cache.latest_success(rt["id"], sid) is None


def test_ttl_expiry_invalidates_hit(tmp_path, monkeypatch):
    cache = _make_cache(tmp_path)
    sid = cache.create_session("release/x", "master", {})
    rt = cache.get_rt("scan_release")

    now = time.time()
    monkeypatch.setattr(cache_mod.time, "time", lambda: now)
    cache.add_request(sid, rt["id"], {"origin": "release/x"})

    monkeypatch.setattr(cache_mod.time, "time", lambda: now + rt["ttl_seconds"] + 1)
    assert cache.latest_success(rt["id"], sid) is None


def test_is_expired_semantics(tmp_path):
    cache = _make_cache(tmp_path)
    now = time.time()
    assert not cache.is_expired(now, 300)
    assert cache.is_expired(now - 301, 300)
    assert not cache.is_expired(now - 10**6, 0)  # TTL 0 = nunca expira
    assert not cache.is_expired(now - 10**6, -1)


# -- facade roundtrips ------------------------------------------------------

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


# -- invalidación -----------------------------------------------------------

def test_invalidate_matching_repositories(tmp_path):
    cache = _make_cache(tmp_path)
    cache.set_repos("release/x", "master", None, None, [{"slug": "r1"}])
    cache.invalidate("release/x", "master", _invoke_details(None, None))
    assert cache.get_repos("release/x", "master", None, None) is None


def test_invalidate_does_not_match_different_repositories(tmp_path):
    """Invalidar con otro bloque repositories no borra (config distinta)."""
    cache = _make_cache(tmp_path)
    cache.set_scan("release/x", "master", ["uat"], {"billing"}, {"origin": "x"})
    cache.invalidate("release/x", "master", _invoke_details(None, None))
    assert cache.get_scan("release/x", "master", ["uat"], {"billing"}) == {"origin": "x"}


def test_invalidate_scan_and_branch(tmp_path):
    cache = _make_cache(tmp_path)
    cache.set_scan("release/x", "master", None, None, {"origin": "x"})
    cache.set_branch_repos("release/x", "master", None, None, [{"repo_name": "a"}])
    cache.invalidate("release/x", "master", _invoke_details(None, None))
    assert cache.get_scan("release/x", "master", None, None) is None
    assert cache.get_branch_repos("release/x", "master", None, None) is None


def test_invalidate_diff(tmp_path):
    cache = _make_cache(tmp_path)
    cache.set_diff("release/x", "master", None, None, {"origin": "x"})
    cache.invalidate("release/x", "master", _invoke_details(None, None))
    assert cache.get_diff("release/x", "master", None, None) is None


def test_invalidate_does_not_touch_master(tmp_path):
    """El master params se referencia por repo, no por rama origen."""
    cache = _make_cache(tmp_path)
    cache.set_master("r1", "master", {("/config/a", "")})
    cache.invalidate("release/x", "master", _invoke_details(None, None))
    assert cache.get_master("r1", "master") == {("/config/a", "")}


def test_invalidate_all_clears_everything(tmp_path):
    cache = _make_cache(tmp_path)
    cache.set_repos("release/x", "master", None, None, [{"slug": "r1"}])
    cache.set_scan("release/x", "master", None, None, {"origin": "x"})
    cache.set_diff("release/x", "master", None, None, {"origin": "x"})
    cache.set_master("r1", "master", {("/config/a", "")})
    cache.set_branch_repos("release/x", "master", None, None, [{"repo_name": "a"}])
    cache.invalidate_all()
    assert cache.get_repos("release/x", "master", None, None) is None
    assert cache.get_scan("release/x", "master", None, None) is None
    assert cache.get_diff("release/x", "master", None, None) is None
    assert cache.get_master("r1", "master") is None
    assert cache.get_branch_repos("release/x", "master", None, None) is None
    # El catálogo (seeds) sobrevive a invalidate_all.
    assert cache.get_rt("scan_release") is not None


# -- histórico --------------------------------------------------------------

def test_overwrite_replaces_with_latest(tmp_path):
    cache = _make_cache(tmp_path)
    cache.set_repos("release/x", "master", None, None, [{"slug": "old"}])
    cache.set_repos("release/x", "master", None, None, [{"slug": "new"}])
    got = cache.get_repos("release/x", "master", None, None)
    assert got == [{"slug": "new"}]


def test_request_history_accumulates(tmp_path):
    cache = _make_cache(tmp_path)
    sid = cache.find_session("release/x", "master", _repo_details_scan())
    rt = cache.get_rt("scan_release")
    cache.add_request(sid, rt["id"], {"n": 1})
    cache.add_request(sid, rt["id"], {"n": 2})
    rows = cache._fetchall("SELECT rq_id FROM request WHERE is_id = ?", (sid,))
    assert len(rows) == 2
    assert cache.latest_success(rt["id"], sid)["payload"] == {"n": 2}


def _repo_details_scan() -> dict:
    return {
        "bypass_cache": False,
        "repositories": {"excluded": [], "prefixes": []},
        "deployment_environments": ["uat", "stgp", "prod"],
        "ssm": {"mode": "DIFF", "prefixes": ["/config", "/common"]},
    }


# -- persistencia / equivalencia ---------------------------------------------

def test_persists_after_reopen(tmp_path):
    path = tmp_path / "test.db"
    c1 = ReleaseCache(db_path=path)
    c1.set_repos("release/x", "master", None, None, [{"slug": "r1"}])
    c1.close()
    c2 = ReleaseCache(db_path=path)
    got = c2.get_repos("release/x", "master", None, None)
    assert got == [{"slug": "r1"}]
    c2.close()


def test_empty_prefixes_and_exclude_equivalent(tmp_path):
    cache = _make_cache(tmp_path)
    cache.set_repos("release/x", "master", None, None, [{"slug": "r1"}])
    got = cache.get_repos("release/x", "master", [], set())
    assert got == [{"slug": "r1"}]


def test_master_not_overwritten_by_different_slug(tmp_path):
    cache = _make_cache(tmp_path)
    cache.set_master("r1", "master", {("/config/a", "")})
    cache.set_master("r2", "master", {("/config/b", "")})
    assert cache.get_master("r1", "master") == {("/config/a", "")}
    assert cache.get_master("r2", "master") == {("/config/b", "")}


# -- service_call (auditoría raw) -------------------------------------------

def test_record_service_call_insert(tmp_path):
    cache = _make_cache(tmp_path)
    sc_id = cache.record_service_call(
        source="bitbucket",
        method="GET",
        url="/2.0/repositories/ws",
        params={"pagelen": "50"},
        status=200,
        duration_ms=12.34,
        response='{"values": []}',
    )
    assert sc_id > 0
    row = cache._fetchone(
        "SELECT sc_source, sc_method, sc_url, sc_params, sc_status, "
        "sc_duration_ms, sc_response FROM service_call WHERE sc_id = ?",
        (sc_id,),
    )
    assert row is not None
    assert row[0] == "bitbucket"
    assert row[1] == "GET"
    assert row[2] == "/2.0/repositories/ws"
    assert json.loads(row[3]) == {"pagelen": "50"}
    assert row[4] == 200
    assert row[5] == pytest.approx(12.34)
    assert row[6] == '{"values": []}'


def test_record_service_call_no_params(tmp_path):
    cache = _make_cache(tmp_path)
    sc_id = cache.record_service_call(
        source="circleci",
        method="GET",
        url="/api/v2/me",
        params=None,
        status=401,
        duration_ms=1.0,
        response="{}",
    )
    assert sc_id > 0


# -- concurrencia -----------------------------------------------------------

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