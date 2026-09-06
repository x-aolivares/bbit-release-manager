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
    aws = cache.get_provider("AWS")
    assert aws is not None
    assert "us-east-1" in aws["details"]["regions"]
    assert cache.get_provider("NoExiste") is None


def test_seed_upserts_existing_aws_regions(tmp_path):
    cache = _make_cache(tmp_path)
    provider = cache.get_provider("AWS")
    cache._conn.execute(
        "UPDATE service_provider SET sp_details = ? WHERE sp_id = ?",
        ('{"regions": ["us-east-1"]}', provider["id"]),
    )
    cache._conn.commit()
    fresh = _make_cache(tmp_path)
    assert fresh.get_provider("AWS")["details"]["regions"] == [
        "us-east-1", "us-east-2", "us-west-1", "us-west-2",
        "eu-west-1", "eu-central-1", "sa-east-1",
    ]


# -- esquema (estándar: sin REFERENCES, nombre = PK referenciada) -------------

def test_schema_without_references(tmp_path):
    """El esquema no usa claves foráneas (REQUIREMENTS del equipo)."""
    cache = _make_cache(tmp_path)
    rows = cache._fetchall(
        "SELECT type, sql FROM sqlite_master WHERE sql IS NOT NULL AND sql LIKE '%REFERENCES%'"
    )
    assert rows == []


def _cols(table: str, cache) -> list[str]:
    return [r[1] for r in cache._fetchall(f"PRAGMA table_info({table})")]


def test_schema_request_type_uses_sp_id(tmp_path):
    cache = _make_cache(tmp_path)
    cols = _cols("request_type", cache)
    assert "sp_id" in cols
    assert "rt_service_provider_id" not in cols


def test_migrates_legacy_request_type_column(tmp_path):
    """Una DB guardada con el nombre viejo se renombra a sp_id al abrir."""
    import sqlite3

    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE service_provider (
            sp_id INTEGER PRIMARY KEY AUTOINCREMENT,
            sp_name TEXT NOT NULL UNIQUE,
            sp_details TEXT NOT NULL
        );
        CREATE TABLE request_type (
            rt_id INTEGER PRIMARY KEY AUTOINCREMENT,
            rt_service_provider_id INTEGER NOT NULL,
            rt_name TEXT NOT NULL UNIQUE,
            rt_ttl_seconds INTEGER NOT NULL DEFAULT 300,
            rt_service_url TEXT NOT NULL,
            rt_details TEXT NOT NULL,
            rt_created_at REAL NOT NULL,
            rt_updated_at REAL NOT NULL
        );
        CREATE INDEX idx_rt_service_provider ON request_type (rt_service_provider_id);
        """
    )
    conn.commit()
    conn.close()

    cache = ReleaseCache(db_path=path)
    assert "sp_id" in _cols("request_type", cache)
    assert "rt_service_provider_id" not in _cols("request_type", cache)
    idx = [r[0] for r in cache._fetchall(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='request_type'"
    )]
    assert "idx_rt_sp_id" in idx
    assert not cache._fetchone(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_rt_service_provider'", ()
    )


# -- clientes y autenticación -----------------------------------------------

def test_client_create_and_resolve_by_alias(tmp_path):
    cache = _make_cache(tmp_path)
    assert cache.get_client_by_alias("local") is None
    c = cache.get_or_create_client("local")
    assert len(c["id"]) == 36  # uuid
    assert cache.get_client_by_alias("local")["id"] == c["id"]
    # idempotente
    again = cache.get_or_create_client("local")
    assert again["id"] == c["id"]


def test_client_alias_unique(tmp_path):
    cache = _make_cache(tmp_path)
    cache.get_or_create_client("alice")
    dup = cache.get_or_create_client("alice")
    assert dup["alias"] == "alice"


def test_auth_save_get_list_clear(tmp_path):
    cache = _make_cache(tmp_path)
    c = cache.get_or_create_client("local")
    sid = cache.save_authentication(c["id"], "Bitbucket", {"BITBUCKET_TOKEN": "t", "_x": ""}, expires_at=123.0)
    assert sid > 0
    auth = cache.get_authentication(c["id"], "Bitbucket")
    assert auth["env"] == {"BITBUCKET_TOKEN": "t"}  # valores vacíos se filtran
    assert auth["expires_at"] == pytest.approx(123.0)

    listed = cache.list_authentications(c["id"])
    assert [a["provider"] for a in listed] == ["Bitbucket"]

    cache.save_authentication(c["id"], "AWS", {"AWS_PROFILE": "prod", "AWS_REGION": "us-east-1"})
    assert len(cache.list_authentications(c["id"])) == 2

    cache.clear_authentications(c["id"])
    assert cache.list_authentications(c["id"]) == []
    assert cache.get_client(c["id"]) is not None  # la fila client sobrevive


def test_auth_scoped_by_client(tmp_path):
    cache = _make_cache(tmp_path)
    alice = cache.get_or_create_client("alice")
    bob = cache.get_or_create_client("bob")
    cache.save_authentication(alice["id"], "Bitbucket", {"BITBUCKET_TOKEN": "a"})
    assert cache.get_authentication(bob["id"], "Bitbucket") is None
    cache.clear_authentications(alice["id"])
    assert cache.get_authentication(alice["id"], "Bitbucket") is None


def test_auth_unknown_provider_raises(tmp_path):
    cache = _make_cache(tmp_path)
    c = cache.get_or_create_client("local")
    with pytest.raises(ValueError):
        cache.save_authentication(c["id"], "NoExiste", {})


def test_auth_unique_index_prevents_duplicates(tmp_path):
    from sqlite3 import IntegrityError

    cache = _make_cache(tmp_path)
    c = cache.get_or_create_client("local")
    cache.save_authentication(c["id"], "Bitbucket", {"BITBUCKET_TOKEN": "a"})
    cache.save_authentication(c["id"], "Bitbucket", {"BITBUCKET_TOKEN": "b"})
    listed = cache.list_authentications(c["id"])
    assert len(listed) == 1
    assert listed[0]["env"]["BITBUCKET_TOKEN"] == "b"

    with pytest.raises(IntegrityError):
        cache._conn.execute(
            "INSERT INTO service_authentication "
            "(c_id, sp_id, sa_details, sa_created_at, sa_updated_at) VALUES "
            "(?, (SELECT sp_id FROM service_provider WHERE sp_name = 'Bitbucket'), ?, ?, ?)",
            (c["id"], '{"sa": {"env": {"BITBUCKET_TOKEN": "dup"}}}', time.time(), time.time()),
        )


def test_auth_migration_dedupes_duplicates(tmp_path):
    cache = _make_cache(tmp_path)
    c = cache.get_or_create_client("local")
    sp_id = cache._fetchone(
        "SELECT sp_id FROM service_provider WHERE sp_name = 'Bitbucket'", ()
    )[0]
    cache._conn.execute("DROP INDEX IF EXISTS uuidx_sa_c_id_sp_id")
    cache._conn.execute(
        "CREATE INDEX IF NOT EXISTS uuidx_sa_c_id_sp_id "
        "ON service_authentication (c_id, sp_id)"
    )
    now = time.time()
    for tok in ("viejo", "nuevo"):
        cache._conn.execute(
            "INSERT INTO service_authentication "
            "(c_id, sp_id, sa_details, sa_created_at, sa_updated_at) VALUES (?, ?, ?, ?, ?)",
            (c["id"], sp_id, f'{{"sa": {{"env": {{"BITBUCKET_TOKEN": "{tok}"}}}}}}', now, now),
        )
    cache._conn.commit()
    assert len(cache._fetchall(
        "SELECT sa_id FROM service_authentication WHERE c_id = ?", (c["id"],)
    )) == 2
    cache._migrate_schema()
    rows = cache._fetchall(
        "SELECT sa_details FROM service_authentication WHERE c_id = ?", (c["id"],)
    )
    assert len(rows) == 1
    assert "nuevo" in json.loads(rows[0][0])["sa"]["env"]["BITBUCKET_TOKEN"]


def test_seeds_request_types(tmp_path):
    cache = _make_cache(tmp_path)
    for name, ttl in [
        ("get_user_repositories", 1800),
        ("get_branch_repositories", 3600),
        ("scan_release", 1800),
        ("diff_ssm", 1800),
        ("get_master_params", 3600),
        ("circleci_project", 3600),
        ("circleci_pipelines", 120),
        ("circleci_workflows", 60),
        ("circleci_workflow_jobs", 60),
    ]:
        rt = cache.get_rt(name)
        assert rt is not None, name
        assert rt["ttl_seconds"] == ttl, name
        assert rt["service_url"], name
    assert cache.get_rt("NoExiste") is None


def test_seed_upserts_existing_rt_ttl(tmp_path):
    cache = _make_cache(tmp_path)
    rt = cache.get_rt("get_branch_repositories")
    cache._conn.execute(
        "UPDATE request_type SET rt_ttl_seconds = ? WHERE rt_id = ?", (777, rt["id"])
    )
    cache._conn.commit()
    fresh = _make_cache(tmp_path)
    assert fresh.get_rt("get_branch_repositories")["ttl_seconds"] == 3600


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


def test_clear_all_vacia_request_sesion_y_service_call(tmp_path):
    cache = _make_cache(tmp_path)
    cache.set_master("r1", "master", {("/config/a", "")})
    cache._insert(
        "INSERT INTO init_sesion (is_source, is_target, is_details, is_created_at, is_updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("release/x", "master", '{"config": {"repositories": {}}}', time.time(), time.time()),
    )
    cache.record_service_call(
        source="bitbucket", method="GET", url="/2.0/me", params=None,
        status=200, duration_ms=1.0, response="{}",
    )

    counts = cache.clear_all()

    assert counts["request"] > 0
    assert counts["init_sesion"] > 0
    assert counts["service_call"] > 0
    assert cache._fetchone("SELECT COUNT(*) FROM request", ())[0] == 0
    assert cache._fetchone("SELECT COUNT(*) FROM init_sesion", ())[0] == 0
    assert cache._fetchone("SELECT COUNT(*) FROM service_call", ())[0] == 0


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