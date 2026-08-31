import httpx
import pytest

from src.circleci.client import CircleCiAuthError, CircleCiClient, CircleCiError


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

    client = CircleCiClient("tok", vcs="bb", org="o", transport=_transport({
        ("GET", "/api/v2/pipeline/p1/workflow"): workflows,
    }))
    pipeline = {"id": "p1", "number": 7}
    try:
        job = client.deploy_job_for_pipeline("r1", pipeline, "stgp")
        assert job.workflow == "deploy-stgp"
        assert "workflows/wf1" in job.url
        assert client.deploy_job_for_pipeline("r1", pipeline, "uat") is None
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

    client = CircleCiClient("tok", vcs="bb", org="o", transport=_transport({
        ("GET", "/api/v2/project/bb/o/r1/pipeline"): pipelines,
        ("GET", "/api/v2/pipeline/p1/workflow"): workflows,
        ("GET", "/api/v2/pipeline/p2/workflow"): workflows,
    }))
    try:
        job = client.deploy_for_tag("r1", "uat-7", "abc", "uat")
        assert job is not None and job.workflow == "deploy-uat"
        assert "workflows/wf1" in job.url
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

    client = CircleCiClient("tok", vcs="bb", org="o", transport=_transport({
        ("GET", "/api/v2/project/bb/o/r1/pipeline"): pipelines,
        ("GET", "/api/v2/pipeline/t1/workflow"): workflows,
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