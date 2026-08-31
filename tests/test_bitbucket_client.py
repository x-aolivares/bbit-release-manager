import httpx
import pytest

from src.bitbucket.client import (
    BitbucketAuthError,
    BitbucketClient,
    BitbucketError,
    DiffFile,
)


def _transport(routes: dict) -> httpx.BaseTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        key = (request.method, request.url.path)
        if key in routes:
            return routes[key](request)
        return httpx.Response(404, json={"message": "not found"})

    return httpx.MockTransport(handler)


def test_session_ok():
    def user(request):
        return httpx.Response(200, json={"username": "jane", "display_name": "Jane"})

    def workspace(request):
        return httpx.Response(200, json={"slug": "ws", "is_private": True, "name": "WS"})

    def repos(request):
        return httpx.Response(200, json={"values": [{"slug": "a", "name": "A", "workspace": {"slug": "ws"}}]})

    client = BitbucketClient("ws", "tok", transport=_transport({
        ("GET", "/2.0/user"): user,
        ("GET", "/2.0/workspaces/ws"): workspace,
        ("GET", "/2.0/repositories/ws"): repos,
    }))
    try:
        info, identity, found = client.session()
    finally:
        client.close()
    assert identity == "Jane (@jane)"
    assert info.slug == "ws"
    assert [r.slug for r in found] == ["a"]


def test_has_branch_true():
    def branch(request):
        return httpx.Response(200, json={"name": "main"})

    client = BitbucketClient("ws", "tok", transport=_transport({
        ("GET", "/2.0/repositories/ws/r1/refs/branches/main"): branch,
    }))
    try:
        assert client.has_branch("r1", "main") is True
    finally:
        client.close()


def test_has_branch_false():
    client = BitbucketClient("ws", "tok", transport=_transport({}))
    try:
        assert client.has_branch("r1", "nonexistent") is False
    finally:
        client.close()


def test_repos_with_branch():
    def repos(request):
        return httpx.Response(200, json={"values": [
            {"slug": "a", "name": "A", "workspace": {"slug": "ws"}},
            {"slug": "b", "name": "B", "workspace": {"slug": "ws"}},
        ]})

    def branch_a(request):
        return httpx.Response(200, json={"name": "release"})

    client = BitbucketClient("ws", "tok", transport=_transport({
        ("GET", "/2.0/repositories/ws"): repos,
        ("GET", "/2.0/repositories/ws/a/refs/branches/release"): branch_a,
    }))
    try:
        found = client.repos_with_branch("release")
    finally:
        client.close()
    assert [r.slug for r in found] == ["a"]


def test_diff():
    diff_text = (
        "diff --git a/file.txt b/file.txt\n"
        "index 111..222 100644\n"
        "--- a/file.txt\n"
        "+++ b/file.txt\n"
        "@@ -1,3 +1,5 @@\n"
        " keep\n"
        "-old\n"
        "+new1\n"
        "+new2\n"
        "+new3\n"
        "done\n"
    )
    client = BitbucketClient("ws", "tok", transport=_transport({
        ("GET", "/2.0/repositories/ws/r1/diff/release"): lambda r: httpx.Response(200, text=diff_text),
    }))
    try:
        d = client.diff("r1", "master", "release")
    finally:
        client.close()
    assert len(d.files) == 1
    assert d.files[0].path == "file.txt"
    assert d.files[0].lines_added == 3
    assert d.files[0].lines_removed == 1


def test_auth_error():
    def user(request):
        return httpx.Response(401, json={"type": "error"})

    client = BitbucketClient("ws", "bad", transport=_transport({
        ("GET", "/2.0/user"): user,
    }))
    with pytest.raises(BitbucketAuthError):
        try:
            client.session()
        finally:
            client.close()


@pytest.mark.parametrize("ws,tok", [("", "tok"), ("ws", "")])
def test_requires_workspace_and_token(ws, tok):
    with pytest.raises(ValueError):
        BitbucketClient(ws, tok)

def test_commit_for_branch():
    def commits(request):
        return httpx.Response(200, json={"values": [{"hash": "abc123"}]})

    client = BitbucketClient("ws", "tok", transport=_transport({
        ("GET", "/2.0/repositories/ws/r1/commits/release"): commits,
    }))
    try:
        h = client.commit_for_branch("r1", "release")
    finally:
        client.close()
    assert h == "abc123"


def test_tags_on_commit():
    payload = {
        "values": [
            {"name": "v1", "target": {"hash": "abc", "date": "2026-01-01"}},
            {"name": "v2", "target": {"hash": "other"}},
        ]
    }
    client = BitbucketClient("ws", "tok", transport=_transport({
        ("GET", "/2.0/repositories/ws/r1/refs/tags"): lambda r: httpx.Response(200, json=payload),
    }))
    try:
        tags = client.tags_on_commit("r1", "abc")
    finally:
        client.close()
    assert [t["name"] for t in tags] == ["v1"]


def test_tags_on_commit_prefix_full_hash():
    payload = {
        "values": [
            {"name": "uat-3", "target": {"hash": "e38579ed0ace", "date": "2026-01-01"}},
            {"name": "v1", "target": {"hash": "other"}},
        ]
    }
    client = BitbucketClient("ws", "tok", transport=_transport({
        ("GET", "/2.0/repositories/ws/r1/refs/tags"): lambda r: httpx.Response(200, json=payload),
    }))
    try:
        tags = client.tags_on_commit("r1", "e38579ed0aceff370704b5272d1d339f6b2dde7d")
    finally:
        client.close()
    assert [t["name"] for t in tags] == ["uat-3"]


def test_find_pr_none():
    client = BitbucketClient("ws", "tok", transport=_transport({
        ("GET", "/2.0/repositories/ws/r1/pullrequests"): lambda r: httpx.Response(200, json={"values": []}),
    }))
    try:
        pr = client.find_pr("r1", "release", "master")
    finally:
        client.close()
    assert pr is None


def test_find_pr_filters_by_both_branches():
    seen = {}

    def handler(request):
        seen["q"] = request.url.params.get("q")
        return httpx.Response(200, json={"values": []})

    client = BitbucketClient("ws", "tok", transport=_transport({
        ("GET", "/2.0/repositories/ws/r1/pullrequests"): handler,
    }))
    try:
        assert client.find_pr("r1", "circleci-project-setup", "release/REP-325073") is None
    finally:
        client.close()
    assert seen["q"] == (
        'source.branch.name="circleci-project-setup" '
        'AND destination.branch.name="release/REP-325073"'
    )


def test_find_pr_returns_source_commit():
    payload = {
        "values": [{
            "id": 7,
            "title": "T",
            "state": "OPEN",
            "links": {"html": {"href": "http://pr/7"}},
            "source": {"commit": {"hash": "abcDEF"}},
        }]
    }
    client = BitbucketClient("ws", "tok", transport=_transport({
        ("GET", "/2.0/repositories/ws/r1/pullrequests"): lambda r: httpx.Response(200, json=payload),
    }))
    try:
        pr = client.find_pr("r1", "release", "master")
    finally:
        client.close()
    assert pr["source_commit"] == "abcDEF"
    assert pr["title"] == "T"


def test_create_pr():
    def post(request):
        assert "/pullrequests" in request.url.path
        body = request.read()
        assert b'"release"' in body and b'"master"' in body
        assert b"Titulo comun" in body
        return httpx.Response(201, json={"id": 42, "title": "T", "state": "OPEN", "links": {"html": {"href": "http://pr"}}})

    client = BitbucketClient("ws", "tok", transport=_transport({
        ("POST", "/2.0/repositories/ws/r1/pullrequests"): post,
    }))
    try:
        pr = client.create_pr("r1", "release", "master", title="Titulo comun")
    finally:
        client.close()
    assert pr["id"] == 42
    assert pr["url"] == "http://pr"


def test_update_pr_title():
    def put(request):
        assert request.url.path == "/2.0/repositories/ws/r1/pullrequests/9"
        assert request.method == "PUT"
        body = request.read()
        assert b"Nuevo titulo" in body
        return httpx.Response(200, json={"id": 9, "title": "Nuevo titulo", "state": "OPEN", "links": {"html": {"href": "http://pr2"}}})

    client = BitbucketClient("ws", "tok", transport=_transport({
        ("PUT", "/2.0/repositories/ws/r1/pullrequests/9"): put,
    }))
    try:
        pr = client.update_pr_title("r1", 9, "Nuevo titulo")
    finally:
        client.close()
    assert pr["title"] == "Nuevo titulo"
    assert pr["url"] == "http://pr2"


def test_create_pr_requires_write_scope():
    client = BitbucketClient("ws", "tok", transport=_transport({
        ("POST", "/2.0/repositories/ws/r1/pullrequests"): lambda r: httpx.Response(403, json={"message": "no"}),
    }))
    try:
        with pytest.raises(BitbucketAuthError):
            client.create_pr("r1", "release", "master")
    finally:
        client.close()


def test_tag_exists():
    client = BitbucketClient("ws", "tok", transport=_transport({
        ("GET", "/2.0/repositories/ws/r1/refs/tags/uat-7"): lambda r: httpx.Response(200, json={"name": "uat-7"}),
    }))
    try:
        assert client.tag_exists("r1", "uat-7") is True
        assert client.tag_exists("r1", "stgp-9") is False
    finally:
        client.close()


def test_create_tag():
    def post(request):
        assert request.url.path == "/2.0/repositories/ws/r1/refs/tags"
        body = request.read()
        assert b'"name":"uat-7"' in body and b'"hash":"abc"' in body
        return httpx.Response(201, json={"name": "uat-7", "target": {"hash": "abc"}})

    client = BitbucketClient("ws", "tok", transport=_transport({
        ("POST", "/2.0/repositories/ws/r1/refs/tags"): post,
    }))
    try:
        tag = client.create_tag("r1", "uat-7", "abc")
    finally:
        client.close()
    assert tag == {"name": "uat-7", "target": "abc"}


def test_create_tag_requires_write_scope():
    client = BitbucketClient("ws", "tok", transport=_transport({
        ("POST", "/2.0/repositories/ws/r1/refs/tags"): lambda r: httpx.Response(403, json={"message": "no"}),
    }))
    try:
        with pytest.raises(BitbucketAuthError):
            client.create_tag("r1", "uat-7", "abc")
    finally:
        client.close()
