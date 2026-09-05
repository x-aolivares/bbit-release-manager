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
        assert request.url.params.get("branch") == "uat-7"
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
                {"id": "jobuuid", "type": "build", "number": 4, "name": "deploy-uat"},
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
        assert job.url == (
            "https://app.circleci.com/pipelines/bb/o/r1/12/details?useNewPipelines=true"
            "&job=jobuuid&workflowId=wf1&buildNumber=4&jobType=build"
        )
        assert client.deploy_for_tag("r1", "uat-7", "abc", "stgp") is None
        assert client.deploy_for_tag("r1", "uat-7", "nope", "uat") is None
    finally:
        client.close()


def test_deploys_for_tags():
    def pipelines(request):
        items = []
        if request.url.params.get("branch") == "v1":
            items = [{"id": "t1", "number": 3, "vcs": {"tag": "v1"}}]
        return httpx.Response(200, json={"next_page_token": None, "items": items})

    def workflows(request):
        return httpx.Response(200, json={
            "next_page_token": None,
            "items": [{"id": "w", "name": "deploy", "status": "running", "created_at": "x"}],
        })

    def jobs(request):
        return httpx.Response(200, json={"next_page_token": None, "items": []})

    client = CircleCiClient("tok", vcs="bb", org="o", transport=_transport({
        ("GET", "/api/v2/project/bb/o/r1/pipeline"): pipelines,
        ("GET", "/api/v2/pipeline/t1/workflow"): workflows,
        ("GET", "/api/v2/workflow/w/job"): jobs,
    }))
    try:
        found = client.deploys_for_tags("r1", ["v1", "v2"])
    finally:
        client.close()
    assert found["v1"].status == "running"
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