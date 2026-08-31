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


def test_scheduled_deploys_for_commit():
    def pipelines(request):
        assert request.url.params.get("branch") == "release"
        return httpx.Response(200, json={
            "next_page_token": None,
            "items": [
                {"id": "p1", "number": 7,
                 "trigger": {"type": "schedule"},
                 "vcs": {"revision": "abc", "branch": "release"}},
                {"id": "p2", "number": 8,
                 "trigger": {"type": "webhook"},
                 "vcs": {"revision": "abc", "branch": "release"}},
            ],
        })

    def workflows(request):
        assert request.url.path == "/api/v2/pipeline/p1/workflow"
        return httpx.Response(200, json={
            "next_page_token": None,
            "items": [
                {"id": "wf1", "name": "deploy-stgp", "status": "success", "created_at": "2026-01-01"}
            ],
        })

    client = CircleCiClient("tok", vcs="bb", org="o", transport=_transport({
        ("GET", "/api/v2/project/bb/o/r1/pipeline"): pipelines,
        ("GET", "/api/v2/pipeline/p1/workflow"): workflows,
    }))
    try:
        found = client.scheduled_deploys_for_commit("r1", "release", "abc", ["uat", "stgp", "prod"])
    finally:
        client.close()
    assert found["stgp"] is not None
    assert found["stgp"].workflow == "deploy-stgp"
    assert "workflows/wf1" in found["stgp"].url
    assert found["uat"] is None


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