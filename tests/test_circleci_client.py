import httpx
import pytest

from bbit_release.circleci.client import CircleCiAuthError, CircleCiClient, CircleCiError


def _transport(routes: dict) -> httpx.BaseTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        key = (request.method, request.url.path)
        if key in routes:
            return routes[key](request)
        return httpx.Response(404, json={"message": "not found"})

    return httpx.MockTransport(handler)


def test_me_auth_error():
    client = CircleCiClient("bad", vcs="bb", org="o", transport=_transport({
        ("GET", "/api/v2/me"): lambda r: httpx.Response(401, json={}),
    }))
    try:
        with pytest.raises(CircleCiAuthError):
            client.me()
    finally:
        client.close()


def test_rate_limit_429_no_retry():
    """Con MAX_RETRIES=1 un 429 de CircleCI falla al primer intento (sin backoff)."""
    calls = 0

    def notif(request):
        nonlocal calls
        calls += 1
        return httpx.Response(429, headers={"Retry-After": "0.5"}, json={"message": "rate limit"})

    client = CircleCiClient("tok", vcs="bb", org="o", transport=_transport({
        ("GET", "/api/v2/me"): notif,
    }))
    try:
        with pytest.raises(CircleCiError, match="rate limit alcanzado"):
            client.me()
    finally:
        client.close()
    assert calls == 1


def test_project_slug():
    client = CircleCiClient("tok", vcs="bb", org="my_org", transport=_transport({}))
    assert client.project_slug("repo") == "bb/my_org/repo"
    client.close()


def test_project_id():
    client = CircleCiClient("tok", vcs="bb", org="o", transport=_transport({
        ("GET", "/api/v2/project/bb/o/r1"): lambda r: httpx.Response(
            200, json={"id": "9beb07c8-cc3b-4da1-8bc4-e9121667fbb7", "slug": "bb/o/r1"}
        ),
    }))
    try:
        assert client.project_id("r1") == "9beb07c8-cc3b-4da1-8bc4-e9121667fbb7"
    finally:
        client.close()


def test_pipeline_id_for_commit():
    def pipelines(request):
        assert request.url.params.get("branch") == "release"
        return httpx.Response(200, json={
            "next_page_token": None,
            "items": [
                {"id": "p1", "number": 12, "vcs": {"revision": "abc", "branch": "release"}},
                {"id": "p2", "number": 99, "vcs": {"revision": "abc", "branch": "release"}},
                {"id": "p3", "number": 50, "vcs": {"revision": "other", "branch": "release"}},
            ],
        })

    client = CircleCiClient("tok", vcs="bb", org="o", transport=_transport({
        ("GET", "/api/v2/project/bb/o/r1/pipeline"): pipelines,
    }))
    try:
        assert client.pipeline_id_for_commit("r1", "release", "abc") == 99
        assert client.pipeline_id_for_commit("r1", "release", "xyz") is None
    finally:
        client.close()


def test_deploy_job_for_pipeline():
    def workflows(request):
        assert request.url.path == "/api/v2/pipeline/p1/workflow"
        return httpx.Response(200, json={
            "next_page_token": None,
            "items": [
                {"id": "wf1", "name": "deploy-stgp", "status": "success", "created_at": "x"},
                {"id": "wf2", "name": "build", "status": "success", "created_at": "x"},
            ],
        })

    def jobs(request):
        return httpx.Response(200, json={
            "next_page_token": None,
            "items": [
                {"id": "jobuuid", "type": "approval", "number": 5, "name": "approve"},
            ],
        })

    client = CircleCiClient("tok", vcs="bb", org="o", transport=_transport({
        ("GET", "/api/v2/pipeline/p1/workflow"): workflows,
        ("GET", "/api/v2/workflow/wf1/job"): jobs,
    }))
    pipeline = {"id": "p1", "number": 7}
    try:
        job = client.deploy_job_for_pipeline("r1", pipeline, "stgp")
        assert job.workflow == "deploy-stgp"
        assert job.status == "on_hold"  # approval sin aprobar -> esperando aprobación
        assert job.job == ""  # sin job de deploy real
        assert job.approval == ""
        assert job.url == (
            "https://app.circleci.com/pipelines/bb/o/r1/7/details?useNewPipelines=true"
            "&job=jobuuid&workflowId=wf1&buildNumber=5&jobType=approval"
        )
        assert client.deploy_job_for_pipeline("r1", pipeline, "uat") is None
    finally:
        client.close()


def test_deploy_url_fallback_without_jobs():
    def workflows(request):
        return httpx.Response(200, json={
            "next_page_token": None,
            "items": [{"id": "wf1", "name": "deploy", "status": "success", "created_at": "x"}],
        })

    client = CircleCiClient("tok", vcs="bb", org="o", transport=_transport({
        ("GET", "/api/v2/pipeline/p1/workflow"): workflows,
    }))
    pipeline = {"id": "p1", "number": 7}
    try:
        job = client.deploy_job_for_pipeline("r1", pipeline, "deploy")
        assert "workflows/wf1" in job.url
    finally:
        client.close()


def test_deploy_for_tag():
    def pipelines(request):
        # Los pipelines de tag traen vcs.tag (sin vcs.branch); el filtro de
        # tag es client-side, asi que NO debe enviarse branch al endpoint.
        assert "branch" not in request.url.params
        return httpx.Response(200, json={
            "next_page_token": None,
            "items": [
                {"id": "p1", "number": 12, "vcs": {"tag": "uat-7", "revision": "abc"}},
                {"id": "p2", "number": 7, "vcs": {"tag": "uat-7", "revision": "zzz"}},
            ],
        })

    def workflows(request):
        if request.url.path == "/api/v2/pipeline/p1/workflow":
            items = [{"id": "wf1", "name": "deploy-uat", "status": "running", "created_at": "x"}]
        else:
            items = []
        return httpx.Response(200, json={"next_page_token": None, "items": items})

    def jobs(request):
        return httpx.Response(200, json={
            "next_page_token": None,
            "items": [
                {"id": "jobuuid", "type": "build", "number": 4, "name": "deploy-uat", "status": "running"},
            ],
        })

    client = CircleCiClient("tok", vcs="bb", org="o", transport=_transport({
        ("GET", "/api/v2/project/bb/o/r1/pipeline"): pipelines,
        ("GET", "/api/v2/pipeline/p1/workflow"): workflows,
        ("GET", "/api/v2/pipeline/p2/workflow"): workflows,
        ("GET", "/api/v2/workflow/wf1/job"): jobs,
    }))
    try:
        job = client.deploy_for_tag("r1", "uat-7", "abc", "uat")
        assert job is not None and job.workflow == "deploy-uat"
        assert job.status == "running"  # estado del job de deploy real
        assert job.job == "deploy-uat"
        assert job.approval == ""
        assert job.url == (
            "https://app.circleci.com/pipelines/bb/o/r1/12/details?useNewPipelines=true"
            "&job=jobuuid&workflowId=wf1&buildNumber=4&jobType=build"
        )
        assert client.deploy_for_tag("r1", "uat-7", "abc", "stgp") is None
        assert client.deploy_for_tag("r1", "uat-7", "nope", "uat") is None
    finally:
        client.close()


def test_deploy_status_selecciona_job_por_prefijo():
    def workflows(request):
        return httpx.Response(200, json={
            "next_page_token": None,
            "items": [{"id": "wf1", "name": "prod-deploy-on-tag", "status": "success", "created_at": "x"}],
        })

    def jobs(request):
        return httpx.Response(200, json={
            "next_page_token": None,
            "items": [
                {"id": "ap", "type": "approval", "name": "approve", "status": "success"},
                {"id": "jsetup", "type": "build", "name": "setup", "status": "success"},
                {"id": "jdep", "type": "build", "name": "deploy-prod", "status": "success"},
            ],
        })

    client = CircleCiClient("tok", vcs="bb", org="o", transport=_transport({
        ("GET", "/api/v2/pipeline/p1/workflow"): workflows,
        ("GET", "/api/v2/workflow/wf1/job"): jobs,
    }))
    pipeline = {"id": "p1", "number": 7}
    try:
        job = client.deploy_job_for_pipeline("r1", pipeline, "prod")
    finally:
        client.close()
    assert job.workflow == "prod-deploy-on-tag"
    assert job.status == "success"
    assert job.job == "deploy-prod"  # no el setup, el job que contiene el env
    assert job.approval == "success"
    assert "job=jdep" in job.url  # approval ya aprobado, link al job de deploy

def test_deploy_status_approval_pendiente():
    def workflows(request):
        return httpx.Response(200, json={
            "next_page_token": None,
            "items": [{"id": "wf1", "name": "uat-deploy-on-tag", "status": "on_hold", "created_at": "x"}],
        })

    def jobs(request):
        return httpx.Response(200, json={
            "next_page_token": None,
            "items": [
                {"id": "ap", "type": "approval", "name": "approve", "status": "on_hold"},
                {"id": "jdep", "type": "build", "name": "deploy-uat", "status": "not_run"},
            ],
        })

    client = CircleCiClient("tok", vcs="bb", org="o", transport=_transport({
        ("GET", "/api/v2/pipeline/p1/workflow"): workflows,
        ("GET", "/api/v2/workflow/wf1/job"): jobs,
    }))
    pipeline = {"id": "p1", "number": 7}
    try:
        job = client.deploy_job_for_pipeline("r1", pipeline, "uat")
    finally:
        client.close()
    assert job.status == "on_hold"  # esperando aprobación, aun con deploy not_run
    assert job.job == "deploy-uat"
    assert job.approval == "on_hold"
    assert "job=ap" in job.url  # deep-link al gate de aprobación


def test_deploys_for_tags():
    def pipelines(request):
        assert "branch" not in request.url.params
        items = [{"id": "t1", "number": 3, "vcs": {"tag": "v1", "revision": "x"}}]
        return httpx.Response(200, json={"next_page_token": None, "items": items})

    def workflows(request):
        return httpx.Response(200, json={
            "next_page_token": None,
            "items": [{"id": "w", "name": "deploy", "status": "running", "created_at": "x"}],
        })

    def jobs(request):
        return httpx.Response(200, json={
            "next_page_token": None,
            "items": [{"id": "jd", "type": "build", "name": "deploy", "status": "running"}],
        })

    client = CircleCiClient("tok", vcs="bb", org="o", transport=_transport({
        ("GET", "/api/v2/project/bb/o/r1/pipeline"): pipelines,
        ("GET", "/api/v2/pipeline/t1/workflow"): workflows,
        ("GET", "/api/v2/workflow/w/job"): jobs,
    }))
    try:
        found = client.deploys_for_tags("r1", ["v1", "v2"], "x")
    finally:
        client.close()
    assert found["v1"].status == "running"
    assert found["v1"].job == "deploy"
    assert found["v2"] is None


def test_requires_token():
    with pytest.raises(ValueError):
        CircleCiClient("", vcs="bb", org="o")
    with pytest.raises(ValueError):
        CircleCiClient("tok", vcs="bb", org="")


# -- service_call hook ------------------------------------------------------

def test_response_hook_captures_raw():
    recorded: list[dict] = []

    client = CircleCiClient("tok", vcs="bb", org="o", transport=_transport({
        ("GET", "/api/v2/me"): lambda r: httpx.Response(
            200, json={"name": "ada", "login": "ada"}
        ),
    }), recorder=recorded.append)
    try:
        client.me()
    finally:
        client.close()

    assert len(recorded) == 1
    entry = recorded[0]
    assert entry["source"] == "circleci"
    assert entry["method"] == "GET"
    assert entry["url"] == "/api/v2/me"
    assert entry["status"] == 200
    assert "ada" in entry["response"]
    assert entry["duration_ms"] >= 0


def test_response_hook_captures_params_and_error():
    recorded: list[dict] = []

    client = CircleCiClient("tok", vcs="bb", org="o", transport=_transport({
        ("GET", "/api/v2/project/bb/o/r1"): lambda r: httpx.Response(
            404, json={"message": "not found"}
        ),
    }), recorder=recorded.append)
    try:
        client.project_id("r1")
    except CircleCiError:
        pass
    finally:
        client.close()

    assert len(recorded) >= 1
    assert recorded[0]["status"] == 404
    assert "not found" in recorded[0]["response"]


def test_recorder_failure_does_not_break_client():
    def boom(_entry):
        raise RuntimeError("boom")

    client = CircleCiClient("tok", vcs="bb", org="o", transport=_transport({
        ("GET", "/api/v2/me"): lambda r: httpx.Response(200, json={"name": "ada"}),
    }), recorder=boom)
    try:
        assert client.me()["name"] == "ada"
    finally:
        client.close()


# -- cache SQLite -----------------------------------------------------------

def _make_cache(tmp_path):
    from bbit_release.cache import ReleaseCache
    return ReleaseCache(db_path=tmp_path / "ci-cache.db")


def test_project_id_cache_hit_avoids_http(tmp_path):
    calls: list[str] = []

    def project(request):
        calls.append("http")
        return httpx.Response(200, json={
            "id": "9beb07c8-cc3b-4da1-8bc4-e9121667fbb7", "slug": "bb/o/r1",
        })

    cache = _make_cache(tmp_path)
    client = CircleCiClient("tok", vcs="bb", org="o", transport=_transport({
        ("GET", "/api/v2/project/bb/o/r1"): project,
    }), cache=cache)
    try:
        assert client.project_id("r1") == "9beb07c8-cc3b-4da1-8bc4-e9121667fbb7"
        assert calls == ["http"]
        assert client.project_id("r1") == "9beb07c8-cc3b-4da1-8bc4-e9121667fbb7"
        assert calls == ["http"]  # 2do pega en el cache
    finally:
        client.close()
        cache.close()


def test_pipelines_cache_distinguishes_branch_and_tag(tmp_path):
    calls: list[str] = []

    def pipelines(request):
        branch = request.url.params.get("branch", "")
        calls.append(branch if branch else "<<tags>>")
        if branch:
            items = [{"id": "p", "number": 1, "vcs": {"revision": "abc", "branch": branch}}]
        else:
            items = [
                {"id": "p1", "number": 1, "vcs": {"revision": "abc", "tag": "v1.0"}},
                {"id": "p2", "number": 2, "vcs": {"revision": "def", "tag": "v2.0"}},
            ]
        return httpx.Response(200, json={"next_page_token": None, "items": items})

    cache = _make_cache(tmp_path)
    client = CircleCiClient("tok", vcs="bb", org="o", transport=_transport({
        ("GET", "/api/v2/project/bb/o/r1/pipeline"): pipelines,
    }), cache=cache)
    try:
        assert [p["id"] for p in client.pipelines("r1", branch="release")] == ["p"]
        assert [p["id"] for p in client.pipelines("r1", branch="release")] == ["p"]
        assert calls == ["release"]  # 2do pega en el cache (kind=branch)
        assert [p["id"] for p in client.pipelines("r1", tag="v1.0")] == ["p1"]
        assert calls == ["release", "<<tags>>"]
        assert [p["id"] for p in client.pipelines("r1", tag="v2.0")] == ["p2"]
        assert calls == ["release", "<<tags>>"]  # 2 tags comparten la página 'latest'
        assert [p["id"] for p in client.pipelines("r1", tag="v1.0")] == ["p1"]
        assert calls == ["release", "<<tags>>"]  # y el 2do v1.0 es hit
        rt = cache.get_rt("circleci_pipelines")
        assert rt is not None
        total = cache._fetchone(
            "SELECT COUNT(*) FROM request WHERE rt_id = ?", (rt["id"],)
        )[0]
        assert total == 2  # kind=branch + kind=latest (una sola fila para tags)
    finally:
        client.close()
        cache.close()


def test_deploys_for_tags_una_sola_pagina_latest(tmp_path):
    calls = {"c": 0}

    def pipelines(request):
        calls["c"] += 1
        return httpx.Response(200, json={"next_page_token": None, "items": [
            {"id": f"p{i}", "number": i, "vcs": {"revision": "abc", "tag": f"v{i}"}}
            for i in range(1, 11)
        ]})

    def workflows(request):
        return httpx.Response(200, json={
            "next_page_token": None,
            "items": [{"id": "w", "name": "deploy", "status": "running", "created_at": "x"}],
        })

    def jobs(request):
        return httpx.Response(200, json={
            "next_page_token": None,
            "items": [{"id": "jd", "type": "build", "name": "deploy", "status": "running"}],
        })

    routes = {
        ("GET", "/api/v2/project/bb/o/r1/pipeline"): pipelines,
    }
    for i in (1, 2, 3):
        routes[("GET", f"/api/v2/pipeline/p{i}/workflow")] = workflows
    routes[("GET", "/api/v2/workflow/w/job")] = jobs
    cache = _make_cache(tmp_path)
    client = CircleCiClient("tok", vcs="bb", org="o", transport=_transport(routes), cache=cache)
    try:
        found = client.deploys_for_tags("r1", ["v1", "v2", "v3"], "abc")
        assert calls["c"] == 1  # 3 tags -> 1 sola página 'latest' consultada
        assert set(found) == {"v1", "v2", "v3"}
        assert all(v is not None and v.status == "running" for v in found.values())
    finally:
        client.close()
        cache.close()


def test_deploys_for_tags_descarta_revision_distinta_al_commit():
    """BBIT-48: un pipeline del tag con vcs.revision != commit se descarta en batch."""
    def pipelines(request):
        return httpx.Response(200, json={"next_page_token": None, "items": [
            {"id": "p2", "number": 12, "vcs": {"tag": "v1", "revision": "zzz"}},
            {"id": "p1", "number": 3, "vcs": {"tag": "v1", "revision": "abc"}},
        ]})

    def workflows(request):
        return httpx.Response(200, json={
            "next_page_token": None,
            "items": [{"id": "w", "name": "deploy", "status": "running", "created_at": "x"}],
        })

    def jobs(request):
        return httpx.Response(200, json={
            "next_page_token": None,
            "items": [{"id": "jd", "type": "build", "name": "deploy", "status": "running"}],
        })

    client = CircleCiClient("tok", vcs="bb", org="o", transport=_transport({
        ("GET", "/api/v2/project/bb/o/r1/pipeline"): pipelines,
        ("GET", "/api/v2/pipeline/p1/workflow"): workflows,
        ("GET", "/api/v2/workflow/w/job"): jobs,
    }))
    try:
        # El pipeline más reciente (p2, rev zzz) se descarta por provenance;
        # el deploy real es p1 (rev abc == commit).
        found = client.deploys_for_tags("r1", ["v1", "v2"], "abc")
    finally:
        client.close()
    assert found["v1"] is not None
    assert found["v1"].pipeline_number == 3
    assert found["v1"].job == "deploy"
    assert found["v2"] is None


def test_pipeline_404_devuelve_lista_vacia_y_se_cachea(tmp_path):
    calls = {"c": 0}

    def not_found(request):
        calls["c"] += 1
        return httpx.Response(404, json={"message": "No pipelines found"})

    cache = _make_cache(tmp_path)
    client = CircleCiClient("tok", vcs="bb", org="o", transport=_transport({
        ("GET", "/api/v2/project/bb/o/r1/pipeline"): not_found,
    }), cache=cache)
    try:
        assert client.pipelines("r1", branch="release") == []
        assert calls["c"] == 1
        assert client.pipelines("r1", branch="release") == []
        assert calls["c"] == 1  # el 404 quedó cacheado como lista vacía
    finally:
        client.close()
        cache.close()


def test_pipeline_500_no_se_degrada_a_lista_vacia():
    def internal_error(request):
        return httpx.Response(500, json={"message": "boom"})

    client = CircleCiClient("tok", vcs="bb", org="o", transport=_transport({
        ("GET", "/api/v2/project/bb/o/r1/pipeline"): internal_error,
    }))
    try:
        with pytest.raises(CircleCiError, match="CircleCI 500"):
            client.pipelines("r1", branch="release")
    finally:
        client.close()


def test_pipeline_404_no_se_cachea_sin_cache():
    calls = {"c": 0}

    def not_found(request):
        calls["c"] += 1
        return httpx.Response(404, json={"message": "No pipelines found"})

    client = CircleCiClient("tok", vcs="bb", org="o", transport=_transport({
        ("GET", "/api/v2/project/bb/o/r1/pipeline"): not_found,
    }), cache=None)
    try:
        assert client.pipelines("r1", branch="release") == []
        assert calls["c"] == 1
        assert client.pipelines("r1", branch="release") == []
        assert calls["c"] == 2  # sin cache se vuelve a consultar
    finally:
        client.close()


def test_workflows_and_jobs_cache_hit(tmp_path):
    calls = {"wf": 0, "jobs": 0}

    def workflows(request):
        calls["wf"] += 1
        return httpx.Response(200, json={"next_page_token": None, "items": [
            {"id": "wf1", "name": "deploy", "status": "success", "created_at": "x"},
        ]})

    def jobs(request):
        calls["jobs"] += 1
        return httpx.Response(200, json={"next_page_token": None, "items": [
            {"id": "j1", "type": "build", "number": 8, "name": "deploy"},
        ]})

    cache = _make_cache(tmp_path)
    client = CircleCiClient("tok", vcs="bb", org="o", transport=_transport({
        ("GET", "/api/v2/pipeline/p1/workflow"): workflows,
        ("GET", "/api/v2/workflow/wf1/job"): jobs,
    }), cache=cache)
    try:
        assert client.workflows("p1")[0]["name"] == "deploy"
        assert client.workflows("p1")[0]["name"] == "deploy"
        assert calls["wf"] == 1
        assert client.workflow_jobs("wf1")[0]["name"] == "deploy"
        assert client.workflow_jobs("wf1")[0]["name"] == "deploy"
        assert calls["jobs"] == 1
    finally:
        client.close()
        cache.close()