import json
import time
import uuid
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

def test_seed_aws_environments_defaults(tmp_path):
    cache = _make_cache(tmp_path)
    envs = cache.list_aws_environments()
    assert {e["name"]: e["region"] for e in envs} == {
        "dev": "us-west-2",
        "qa": "us-west-1",
    }


def test_seed_aws_environments_edits_survive_boot(tmp_path):
    cache = _make_cache(tmp_path)
    cache.save_aws_environments({"qa": "eu-west-1", "qa2": "us-east-1"})
    fresh = _make_cache(tmp_path)
    envs = fresh.aws_environments_map()
    # los defaults solo aplican si la fila no existe: la edición de qa (usuario)
    # sobrevive al boot y qa2 (agregada por usuario) se mantiene
    assert envs["qa"] == "eu-west-1"
    assert envs["qa2"] == "us-east-1"
    assert envs["dev"] == "us-west-2"


def test_aws_environments_rows_preserve_docker_endpoint(tmp_path):
    cache = _make_cache(tmp_path)
    cache.save_aws_environments([
        {"name": "qa", "region": "us-west-1", "localstack": True,
         "endpoint_url": "http://localhost:4566"},
        {"name": "dev", "region": "us-west-2"},
    ])
    qa = next(e for e in cache.list_aws_environments() if e["name"] == "qa")
    dev = next(e for e in cache.list_aws_environments() if e["name"] == "dev")
    assert qa["localstack"] is True
    assert qa["endpoint_url"] == "http://localhost:4566"
    assert dev["localstack"] is False
    assert dev["endpoint_url"] == ""
    # el modo dict (legacy) conserva los flags existentes
    cache.save_aws_environments({"qa": "eu-west-1"})
    qa = next(e for e in cache.list_aws_environments() if e["name"] == "qa")
    assert qa["region"] == "eu-west-1"
    assert qa["localstack"] is True
    assert qa["endpoint_url"] == "http://localhost:4566"


def test_migrates_existing_aws_environment_columns(tmp_path):
    db = tmp_path / "test.db"
    cache = ReleaseCache(db_path=db)
    cache.close()
    # simula una DB vieja sin las columnas por ambiente
    import sqlite3

    conn = sqlite3.connect(str(db))
    conn.execute("DROP TABLE aws_environment")
    conn.execute(
        "CREATE TABLE aws_environment ("
        " ae_id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " ae_name TEXT NOT NULL UNIQUE,"
        " ae_region TEXT NOT NULL,"
        " ae_created_at REAL NOT NULL,"
        " ae_updated_at REAL NOT NULL)"
    )
    conn.execute(
        "INSERT INTO aws_environment (ae_name, ae_region, ae_created_at, ae_updated_at)"
        " VALUES ('qa', 'us-west-1', 0, 0)"
    )
    conn.commit()
    conn.close()
    fresh = ReleaseCache(db_path=db)
    cols = {
        row[1] for row in fresh._conn.execute("PRAGMA table_info(aws_environment)")
    }
    assert {"ae_localstack", "ae_endpoint_url"} <= cols
    qa = next(e for e in fresh.list_aws_environments() if e["name"] == "qa")
    assert qa["localstack"] is False
    assert qa["endpoint_url"] == ""
    fresh.close()


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


def test_client_seeded_uuid_deterministic_and_stable(tmp_path):
    cache = _make_cache(tmp_path)
    seed = "alice|1720000000.0|tok-alice"
    c = cache.get_or_create_client("alice", seed=seed)
    assert uuid.UUID(c["id"]).version == 5  # uuid5 derivado del seed
    # mismo seed + alias -> mismo id
    again = cache.get_or_create_client("alice", seed=seed)
    assert again["id"] == c["id"]
    # cliente existente SOLO se resume: un seed distinto (p.ej. token nuevo)
    # NO regenera el id
    renewed = cache.get_or_create_client("alice", seed="alice|1720000000.0|tok-nuevo")
    assert renewed["id"] == c["id"]
    # distintos alias/token -> ids distintos
    bob = cache.get_or_create_client("bob", seed="bob|1720000000.0|tok-bob")
    assert bob["id"] != c["id"]
    # sin seed conserva uuid4 heredado
    plain = cache.get_or_create_client("sin-seed")
    assert uuid.UUID(plain["id"]).version == 4


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


# -- snapshots de consulta (BBIT-56) -----------------------------------------

def _query(prefixes=None, project_prefixes=None, exclude=None, origin="release/x", destination="master"):
    return {
        "origin": origin,
        "destination": destination,
        "prefixes": prefixes or ["uat"],
        "project_prefixes": project_prefixes or [],
        "exclude": exclude or [],
        "mode": "diff",
    }


def test_snapshot_first_consult_reports_identical_false(tmp_path):
    cache = _make_cache(tmp_path)
    res = cache.append_snapshot("release/x", "master", _query())
    assert res["identical"] is False
    assert res["previous"] is None
    assert res["is_id"] > 0


def test_snapshot_identical_repeated_consult(tmp_path):
    cache = _make_cache(tmp_path)
    q = _query()
    first = cache.append_snapshot("release/x", "master", q)
    second = cache.append_snapshot("release/x", "master", q)
    assert second["identical"] is True
    assert second["previous"] == q
    assert second["is_id"] == first["is_id"]


def test_snapshot_changed_consult_prepends_desc(tmp_path):
    cache = _make_cache(tmp_path)
    cache.append_snapshot("release/x", "master", _query(prefixes=["uat"]))
    cache.append_snapshot("release/x", "master", _query(prefixes=["uat", "stgp"]))

    last = cache.get_last_snapshot("release/x", "master")
    assert last is not None
    assert "stgp" in last["prefixes"]


def test_snapshot_ignores_session_rows(tmp_path):
    cache = _make_cache(tmp_path)
    # Una sesión con fingerprint (fila normal de init_sesion) NO debe confundirse
    cache.find_session("release/x", "master", {"repositories": {}})
    assert cache.get_last_snapshot("release/x", "master") is None

    res = cache.append_snapshot("release/x", "master", _query())
    # La fila de snapshots es distinta de la de sesión
    snap_row = cache.get_session(res["is_id"])
    parsed = json.loads(snap_row["details"])
    assert parsed["type"] == "query_history"


def test_diff_snapshots_first_consult_marks_all_added(tmp_path):
    cache = _make_cache(tmp_path)
    d = cache.diff_snapshots(None, _query(prefixes=["uat"], exclude=["billing"]))
    assert d["identical"] is False
    assert d["added"]["prefixes"] == ["uat"]
    assert d["added"]["exclude"] == ["billing"]


def test_diff_snapshots_added_new_environment(tmp_path):
    cache = _make_cache(tmp_path)
    prev = _query(prefixes=["uat"])
    curr = _query(prefixes=["uat", "stgp"])
    d = cache.diff_snapshots(prev, curr)
    assert d["identical"] is False
    assert d["added"]["prefixes"] == ["stgp"]
    assert d["removed"] == {}


def test_diff_snapshots_removed_environment_by_blacklist(tmp_path):
    cache = _make_cache(tmp_path)
    prev = _query(prefixes=["uat", "stgp"], exclude=[])
    curr = _query(prefixes=["uat", "stgp"], exclude=["billing-api"])
    d = cache.diff_snapshots(prev, curr)
    # Agregar el repo a la blacklist → ese repo se QUITA del set consultado.
    # En el diff de queries el campo exclude gana el valor nuevo (added).
    assert d["added"]["exclude"] == ["billing-api"]
    assert "prefixes" not in d["added"]


def test_diff_snapshots_identical_queries(tmp_path):
    cache = _make_cache(tmp_path)
    d = cache.diff_snapshots(_query(), _query())
    assert d["identical"] is True


def test_diff_snapshots_changed_mixed_set(tmp_path):
    # uat se quita, qa se agrega: superposición parcial → changed
    cache = _make_cache(tmp_path)
    prev = _query(prefixes=["uat", "stgp"])
    curr = _query(prefixes=["stgp", "qa"])
    d = cache.diff_snapshots(prev, curr)
    assert d["identical"] is False
    assert d["changed"]["prefixes"]["desde"] == ["stgp", "uat"]
    assert d["changed"]["prefixes"]["hasta"] == ["qa", "stgp"]


def test_invalidate_borra_snapshots(tmp_path):
    cache = _make_cache(tmp_path)
    cache.append_snapshot("release/x", "master", _query(prefixes=["uat"]))
    cache.invalidate("release/x", "master", {})
    assert cache.get_last_snapshot("release/x", "master") is None


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


# -- prune ------------------------------------------------------------------

def test_prune_borra_request_expirado_y_conserva_vigente(tmp_path, monkeypatch):
    cache = _make_cache(tmp_path)
    sid = cache.create_session("release/x", "master", {})
    rt = cache.get_rt("scan_release")

    times = [time.time()]
    monkeypatch.setattr(cache_mod.time, "time", lambda: times[0])
    cache.add_request(sid, rt["id"], {"viejo": True})

    times[0] += rt["ttl_seconds"] + 100
    cache.add_request(sid, rt["id"], {"nuevo": True})

    counts = cache.prune()
    assert counts["request"] == 1
    row = cache._fetchone(
        "SELECT COUNT(*) FROM request WHERE rq_details LIKE '%viejo%'", ()
    )
    assert row[0] == 0
    row = cache._fetchone(
        "SELECT COUNT(*) FROM request WHERE rq_details LIKE '%nuevo%'", ()
    )
    assert row[0] == 1


def test_prune_respeta_pending_reciente_y_poda_anomalo(tmp_path, monkeypatch):
    cache = _make_cache(tmp_path)
    sid = cache.create_session("release/x", "master", {})
    rt = cache.get_rt("scan_release")

    times = [time.time()]
    monkeypatch.setattr(cache_mod.time, "time", lambda: times[0])
    cache.add_request(sid, rt["id"], {"in": "flight"}, status="PENDING")

    times[0] += rt["ttl_seconds"] * 2
    assert cache.prune()["request"] == 0  # colchón amplio: no se toca

    times[0] += rt["ttl_seconds"] * 20 + 3600
    assert cache.prune()["request"] == 1  # PENDING anómalo (stale) sí se poda


def test_prune_borra_raw_stash_expirado(tmp_path, monkeypatch):
    cache = _make_cache(tmp_path)

    times = [time.time()]
    monkeypatch.setattr(cache_mod.time, "time", lambda: times[0])
    cache.set_raw(source="Bitbucket", method="GET", url="/a", status=200,
                  response="{}", ttl_seconds=60)
    times[0] += 61
    cache.set_raw(source="Bitbucket", method="GET", url="/b", status=200,
                  response="{}", ttl_seconds=60)

    counts = cache.prune()
    assert counts["raw_stash"] == 1
    assert cache.count_raw() == 1


def test_prune_poda_service_call_por_antiguedad(tmp_path, monkeypatch):
    cache = _make_cache(tmp_path)

    times = [time.time()]
    monkeypatch.setattr(cache_mod.time, "time", lambda: times[0])
    cache.record_service_call(
        source="circleci", method="GET", url="/api/v2/me", params=None,
        status=200, duration_ms=1.0, response="{}",
    )
    times[0] += 8 * 86400

    counts = cache.prune()
    assert counts["service_call"] == 1
    row = cache._fetchone("SELECT COUNT(*) FROM service_call", ())
    assert row[0] == 0


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


def test_branch_repos_key_filter_free(tmp_path):
    """BBIT-36 P1 — el índice de ramas ignora los filtros en el key.

    El índice persiste TODOS los repos del par de ramas: guardar con
    prefijos/blacklist y leer con otros filtros debe ser hit (los filtros son
    una vista, no parte de la identidad de la consulta).
    """
    cache = _make_cache(tmp_path)
    repos = [
        {"repo_name": "trans-a", "name": "TA", "workspace": "ws", "default_branch": "master", "branch_state": "found"},
        {"repo_name": "trans-b", "name": "TB", "workspace": "ws", "default_branch": "master", "branch_state": "found"},
        {"repo_name": "core-app", "name": "CA", "workspace": "ws", "default_branch": "master", "branch_state": "not_found"},
    ]
    cache.set_branch_repos("release/x", "master", ["trans"], {"trans-b"}, repos)
    assert cache.get_branch_repos("release/x", "master", ["other-prefix"], {"x", "y"}) == repos
    assert cache.get_branch_repos("release/x", "master", None, None) == repos


def test_scan_repo_roundtrip_and_distinct_by_slug(tmp_path):
    """BBIT-36 P2 — el scan se cachea por repositorio, key (origin, dest, slug)."""
    cache = _make_cache(tmp_path)
    cache.set_scan_repo("release/x", "master", "r1", {"item": {"slug": "r1"}, "with_tags": True})
    cache.set_scan_repo("release/x", "master", "r2", {"item": {"slug": "r2"}, "with_tags": False})
    assert cache.get_scan_repo("release/x", "master", "r1")["item"]["slug"] == "r1"
    assert cache.get_scan_repo("release/x", "master", "r2")["item"]["slug"] == "r2"
    assert cache.get_scan_repo("release/x", "master", "r2")["with_tags"] is False
    assert cache.get_scan_repo("release/x", "master", "r3") is None


def test_scan_repo_distinct_by_branch_pair(tmp_path):
    """El mismo repo en otro par de ramas NO comparte item de scan."""
    cache = _make_cache(tmp_path)
    cache.set_scan_repo("release/x", "master", "r1", {"item": {"slug": "r1"}, "with_tags": True})
    assert cache.get_scan_repo("release/x", "staging", "r1") is None


def test_invalidate_borra_scan_repo_por_par(tmp_path):
    """force/TTL invalidan los items scan_repo del par (origin, destination)."""
    cache = _make_cache(tmp_path)
    cache.set_scan_repo("release/x", "master", "r1", {"item": {"slug": "r1"}, "with_tags": True})
    cache.invalidate("release/x", "master", {})
    assert cache.get_scan_repo("release/x", "master", "r1") is None


# -- invalidación -----------------------------------------------------------

def test_invalidate_matching_repositories(tmp_path):
    cache = _make_cache(tmp_path)
    cache.set_repos("release/x", "master", None, None, [{"slug": "r1"}])
    cache.invalidate("release/x", "master", _invoke_details(None, None))
    assert cache.get_repos("release/x", "master", None, None) is None


def test_invalidate_does_not_match_different_repositories(tmp_path):
    """Invalidar borra la pareja de ramas incluso con otro bloque repositories.

    Una sesión de consulta se identifica por su par (origen, destino): al
    eliminar la sesión o forzar consultas se re-consultan las APIs sin
    importar con qué prefijos/exclusiones se cacheó (BBIT-20).
    """
    cache = _make_cache(tmp_path)
    cache.set_scan("release/x", "master", ["uat"], {"billing"}, {"origin": "x"})
    cache.invalidate("release/x", "master", _invoke_details(None, None))
    assert cache.get_scan("release/x", "master", ["uat"], {"billing"}) is None


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


# -- valores SSM (per cliente + decrypt) ------------------------------------

def test_ssm_values_upsert_by_client_path_decrypt(tmp_path):
    cache = _make_cache(tmp_path)
    cache.set_ssm_values("juan", {"/config/a": {"value": "1"}}, decrypt=False)
    cache.set_ssm_values("juan", {"/config/a": {"value": "2"}}, decrypt=False)

    got = cache.get_ssm_values("juan", ["/config/a"])
    assert got["/config/a"]["value"] == "2"
    assert got["/config/a"]["encrypted"] is False
    row = cache._fetchone("SELECT COUNT(*) FROM ssm_value", ())
    assert row[0] == 1  # upsert, no duplica


def test_ssm_values_isolation_per_client_and_decrypt(tmp_path):
    cache = _make_cache(tmp_path)
    # Juan descifra; María no
    cache.set_ssm_values("juan", {"/config/secret": {"value": "S", "encrypted": True}}, decrypt=True)
    cache.set_ssm_values("maria", {"/config/secret": {"value": "M", "encrypted": False}}, decrypt=False)

    juan_dec = cache.get_ssm_values("juan", ["/config/secret"], decrypt=True)
    assert juan_dec["/config/secret"]["value"] == "S"

    maria_plain = cache.get_ssm_values("maria", ["/config/secret"], decrypt=False)
    assert maria_plain["/config/secret"]["value"] == "M"

    # María NO ve la fila de Juan (decrypt=True) aunque pida el mismo path
    maria_dec = cache.get_ssm_values("maria", ["/config/secret"], decrypt=True)
    assert "/config/secret" not in maria_dec

    # Juan NO ve la fila de María (decrypt=False)
    juan_plain = cache.get_ssm_values("juan", ["/config/secret"], decrypt=False)
    assert "/config/secret" not in juan_plain


def test_ssm_values_ttl_expires(tmp_path):
    cache = _make_cache(tmp_path)
    cache.set_ssm_values("juan", {"/config/a": {"value": "1"}}, decrypt=False)
    # ttl<=0 -> nunca expira
    got = cache.get_ssm_values("juan", ["/config/a"], ttl=0)
    assert got["/config/a"]["value"] == "1"
    # ttl chico -> expira tras dormir
    time.sleep(0.02)
    got_expired = cache.get_ssm_values("juan", ["/config/a"], ttl=0.005)
    assert got_expired == {}


def test_ssm_values_clear_per_client_and_all(tmp_path):
    cache = _make_cache(tmp_path)
    cache.set_ssm_values("juan", {"/config/a": {"value": "1"}}, decrypt=False)
    cache.set_ssm_values("maria", {"/config/a": {"value": "2"}}, decrypt=False)

    assert cache.clear_ssm_values("juan") == 1
    assert cache._fetchone("SELECT COUNT(*) FROM ssm_value", ())[0] == 1
    assert cache.clear_ssm_values() == 1
    assert cache._fetchone("SELECT COUNT(*) FROM ssm_value", ())[0] == 0


# -- tabla repositories (persistencia r_details) ----------------------------

def test_repository_none_cuando_no_existe(tmp_path):
    cache = _make_cache(tmp_path)
    assert cache.get_repository("ws", "orders-api") is None
    assert cache.get_repository_flow_tags("ws", "orders-api") == []


def test_repository_r_id_es_uuid_deterministico_por_workspace_slug(tmp_path):
    cache = _make_cache(tmp_path)
    cache.upsert_repository("ws", "orders-api", url="https://x/orders-api", details={"tags": ["fargate"]})
    repo = cache.get_repository("ws", "orders-api")
    assert isinstance(repo["r_id"], str)
    assert repo["r_id"] == str(uuid.uuid5(uuid.NAMESPACE_URL, "orders-api|ws"))
    # mismo slug en distinto workspace -> distinto r_id
    cache.upsert_repository("ws2", "orders-api", details={"tags": []})
    assert cache.get_repository("ws2", "orders-api")["r_id"] != repo["r_id"]
    # recrear la misma fila no cambia el r_id (no repetible)
    cache.upsert_repository("ws", "orders-api", details={"tags": ["batch"]})
    assert cache.get_repository("ws", "orders-api")["r_id"] == repo["r_id"]


def test_repository_r_created_at_updated_at(tmp_path):
    cache = _make_cache(tmp_path)
    cache.upsert_repository("ws", "orders-api", details={"tags": ["fargate"]})
    repo = cache.get_repository("ws", "orders-api")
    assert repo["r_created_at"] > 0
    assert repo["r_updated_at"] > 0
    first = repo["r_updated_at"]
    import time as _time
    _time.sleep(0.01)
    cache.upsert_repository("ws", "orders-api", details={"other": True})
    repo = cache.get_repository("ws", "orders-api")
    assert repo["r_created_at"] == first or repo["r_created_at"] <= repo["r_updated_at"]
    assert repo["r_updated_at"] >= first


def test_repository_migra_r_id_integer_a_uuid(tmp_path):
    """DBs viejas: r_id INTEGER AUTOINCREMENT y sin timestamps se reconstruyen."""
    import sqlite3

    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE repositories (
            r_id INTEGER PRIMARY KEY AUTOINCREMENT,
            r_slug TEXT NOT NULL,
            r_workspace TEXT NOT NULL,
            r_url TEXT NOT NULL DEFAULT '',
            r_details TEXT NOT NULL DEFAULT '{}'
        );
        CREATE UNIQUE INDEX uuidx_repositories_workspace_slug
            ON repositories (r_workspace, r_slug);
        INSERT INTO repositories (r_slug, r_workspace, r_url, r_details)
        VALUES ('orders-api', 'ws', 'https://x', '{"tags": ["fargate"]}');
        """
    )
    conn.commit()
    conn.close()

    cache = ReleaseCache(db_path=path)
    repo = cache.get_repository("ws", "orders-api")
    assert repo["r_id"] == str(uuid.uuid5(uuid.NAMESPACE_URL, "orders-api|ws"))
    assert repo["r_details"]["tags"] == ["fargate"]
    assert repo["r_created_at"] > 0
    # el índice único (workspace, slug) sigue presente tras la migración
    idx = [r[0] for r in cache._fetchall(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='repositories'"
    )]
    assert "uuidx_repositories_workspace_slug" in idx
    # upsert posterior no choca con la PK nueva
    cache.upsert_repository("ws", "orders-api", details={"other": 1})
    assert cache.get_repository("ws", "orders-api")["r_details"]["other"] == 1
    cache.close()


def test_repository_source_upsert_por_branch_y_target(tmp_path):
    cache = _make_cache(tmp_path)
    cache.set_repository_source(
        "ws", "orders-api",
        {"branch": "release/x", "url": "https://x/branch", "head_commit": "abc",
         "tags": [{"name": "uat-1", "url": "https://ci/1"}],
         "targets": [{"branch": "master", "pr": {"status": "open", "title": "PR1", "url": "https://pr/1"},
                      "ssm": ["/config/common/one"]}]},
    )
    repo = cache.get_repository("ws", "orders-api")
    sources = repo["r_details"]["sources"]
    assert len(sources) == 1
    assert sources[0]["branch"] == "release/x"
    assert sources[0]["head_commit"] == "abc"

    # misma branch nuevo target -> upsert target, conserva el otro destino
    cache.set_repository_source(
        "ws", "orders-api",
        {"branch": "release/x", "targets": [{"branch": "prod", "pr": {"status": "open", "title": "PR2", "url": "https://pr/2"}}]},
    )
    sources = cache.get_repository("ws", "orders-api")["r_details"]["sources"]
    assert len(sources) == 1
    dests = {t["branch"] for t in sources[0]["targets"]}
    assert dests == {"master", "prod"}
    # merge sobre source existente conserva head_commit (no lo pisa el target parcial)
    assert sources[0]["head_commit"] == "abc"
    # el ssm del target master ya persistido se conserva al mergear otro destino
    master_t = next(t for t in sources[0]["targets"] if t["branch"] == "master")
    assert master_t["ssm"] == ["/config/common/one"]

    # otra branch origen -> source aparte
    cache.set_repository_source(
        "ws", "orders-api",
        {"branch": "release/y", "head_commit": "def", "targets": [{"branch": "master", "pr": None}]},
    )
    branches = sorted(s["branch"] for s in cache.get_repository("ws", "orders-api")["r_details"]["sources"])
    assert branches == ["release/x", "release/y"]


def test_repository_source_ssm_sobrescribe_total(tmp_path):
    cache = _make_cache(tmp_path)
    cache.set_repository_source(
        "ws", "orders-api",
        {"branch": "release/x", "head_commit": "abc",
         "targets": [{"branch": "master", "pr": {"status": "open", "title": "PR1", "url": ""}}]},
    )
    # primero populate con dos params
    cache.set_repository_source_ssm(
        "ws", "orders-api", "release/x", "master",
        source_ssm=["/config/common/ledger/db-user", "/config/common/amount"],
        target_ssm=["/config/common/amount-round"],
    )
    s = cache.get_repository("ws", "orders-api")["r_details"]["sources"][0]
    assert s["ssm"] == ["/config/common/amount", "/config/common/ledger/db-user"]
    assert s["targets"][0]["ssm"] == ["/config/common/amount-round"]
    # segunda lectura: SOBRESCRIBE (no merge) los arrays de ssm
    cache.set_repository_source_ssm(
        "ws", "orders-api", "release/x", "master",
        source_ssm=["/config/common/amount"],
        target_ssm=[],
        target_head_commit="def456",
    )
    s = cache.get_repository("ws", "orders-api")["r_details"]["sources"][0]
    assert s["ssm"] == ["/config/common/amount"]
    assert s["targets"][0]["ssm"] == []
    assert s["targets"][0]["head_commit"] == "def456"
    # target sin ssm previo: no rompe
    cache.set_repository_source(
        "ws", "orders-api",
        {"branch": "release/z", "head_commit": "z", "targets": [{"branch": "master", "pr": None}]},
    )
    cache.set_repository_source_ssm("ws", "orders-api", "release/z", "master",
                                    source_ssm=["/config/other/x"], target_ssm=[],
                                    target_head_commit="def456")
    s = cache.get_repository("ws", "orders-api")["r_details"]["sources"]
    sz = next(x for x in s if x["branch"] == "release/z")
    assert sz["ssm"] == ["/config/other/x"]
    assert sz["targets"][0]["head_commit"] == "def456"


def test_repository_upsert_crea_y_merge_details(tmp_path):
    cache = _make_cache(tmp_path)
    cache.upsert_repository("ws", "orders-api", url="https://x/orders-api", details={"tags": ["fargate"]})
    repo = cache.get_repository("ws", "orders-api")
    assert repo["r_slug"] == "orders-api"
    assert repo["r_workspace"] == "ws"
    assert repo["r_url"] == "https://x/orders-api"
    assert repo["r_details"]["tags"] == ["fargate"]

    # merge sin pisar lo existente
    cache.upsert_repository("ws", "orders-api", details={"other": 42})
    repo = cache.get_repository("ws", "orders-api")
    assert repo["r_details"]["tags"] == ["fargate"]
    assert repo["r_details"]["other"] == 42


def test_repository_flow_tags_normaliza_minusculas_y_deduplica(tmp_path):
    cache = _make_cache(tmp_path)
    got = cache.set_repository_flow_tags("ws", "orders-api", ["Fargate", " fargate ", "Batch", " step-function "])
    assert got == ["batch", "fargate", "step-function"]
    assert cache.get_repository_flow_tags("ws", "orders-api") == ["batch", "fargate", "step-function"]


def test_repository_flow_tags_clear(tmp_path):
    cache = _make_cache(tmp_path)
    cache.set_repository_flow_tags("ws", "orders-api", ["fargate", "batch"])
    assert cache.get_repository_flow_tags("ws", "orders-api") == ["batch", "fargate"]
    cache.set_repository_flow_tags("ws", "orders-api", [])
    assert cache.get_repository_flow_tags("ws", "orders-api") == []


def test_repository_flow_tags_ignore_none_strings(tmp_path):
    cache = _make_cache(tmp_path)
    cache.set_repository_flow_tags("ws", "r1", ["ok", None, 123])
    assert cache.get_repository_flow_tags("ws", "r1") == ["ok"]


def test_list_repositories_solo_del_workspace(tmp_path):
    cache = _make_cache(tmp_path)
    cache.set_repository_flow_tags("ws-a", "r1", ["fargate"])
    cache.set_repository_flow_tags("ws-b", "r1", ["batch"])
    rows = cache.list_repositories("ws-a")
    assert [r["r_slug"] for r in rows] == ["r1"]
    assert rows[0]["r_workspace"] == "ws-a"
    assert rows[0]["r_details"]["tags"] == ["fargate"]


def test_repository_persists_after_reopen(tmp_path):
    path = tmp_path / "test.db"
    c1 = ReleaseCache(db_path=path)
    c1.set_repository_flow_tags("ws", "orders-api", ["fargate", "batch"])
    c1.close()
    c2 = ReleaseCache(db_path=path)
    assert c2.get_repository_flow_tags("ws", "orders-api") == ["batch", "fargate"]
    c2.close()