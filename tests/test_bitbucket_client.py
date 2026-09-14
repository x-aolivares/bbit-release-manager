import httpx
import pytest

from bbit_release.bitbucket.client import (
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


def test_snapshot_extrae_archivos_sin_prefijo_raiz():
    import io
    import tarfile

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        def add(name: str, data: bytes, isdir: bool = False):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            if isdir:
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
            tar.addfile(info, io.BytesIO(data))

        add("ws-r1-abc123/", b"", isdir=True)
        add("ws-r1-abc123/common.yml", b"k: {{resolve:ssm:/config/app/key}}")
        add("ws-r1-abc123/config/app.json", b'{"env": "x"}')
    content = buf.getvalue()

    def archive(request):
        return httpx.Response(
            200, content=content, headers={"Content-Type": "application/x-tar-gz"}
        )

    client = BitbucketClient("ws", "tok", transport=_transport({
        ("GET", "/ws/r1/get/abc.tar.gz"): archive,
    }))
    try:
        snap = client.snapshot("r1", "abc")
    finally:
        client.close()
    assert snap == {
        "common.yml": "k: {{resolve:ssm:/config/app/key}}",
        "config/app.json": '{"env": "x"}',
    }


def test_snapshot_none_si_el_ref_no_existe():
    client = BitbucketClient("ws", "tok", transport=_transport({}))
    try:
        assert client.snapshot("r1", "nope") is None
    finally:
        client.close()


def test_snapshot_cache_hit_evita_redescarga(tmp_path):
    import io
    import tarfile

    from bbit_release.cache import ReleaseCache

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo("ws-r1-abc/")
        info.type = tarfile.DIRTYPE
        info.mode = 0o755
        tar.addfile(info, io.BytesIO(b""))
        data = b"a: {{resolve:ssm:/config/x}}"
        info = tarfile.TarInfo("ws-r1-abc/app.yml")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    content = buf.getvalue()
    calls = {"c": 0}

    def archive(request):
        calls["c"] += 1
        return httpx.Response(
            200, content=content, headers={"Content-Type": "application/x-tar-gz"}
        )

    cache = ReleaseCache(tmp_path / "cache.db")
    client = BitbucketClient("ws", "tok", transport=_transport({
        ("GET", "/ws/r1/get/abc.tar.gz"): archive,
    }), cache=cache)
    try:
        snap1 = client.snapshot("r1", "abc")
        snap2 = client.snapshot("r1", "abc")
        assert calls["c"] == 1  # el 2do pega en SQLite, no re-descarga
        assert snap1 == snap2 == {"app.yml": "a: {{resolve:ssm:/config/x}}"}
    finally:
        client.close()
        cache.close()


def test_snapshot_force_redescarga(tmp_path):
    import io
    import tarfile

    from bbit_release.cache import ReleaseCache

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo("ws-r1-abc/")
        info.type = tarfile.DIRTYPE
        info.mode = 0o755
        tar.addfile(info, io.BytesIO(b""))
        data = b"x"
        info = tarfile.TarInfo("ws-r1-abc/f.txt")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    content = buf.getvalue()
    calls = {"c": 0}

    def archive(request):
        calls["c"] += 1
        return httpx.Response(
            200, content=content, headers={"Content-Type": "application/x-tar-gz"}
        )

    cache = ReleaseCache(tmp_path / "cache.db")
    client = BitbucketClient("ws", "tok", transport=_transport({
        ("GET", "/ws/r1/get/abc.tar.gz"): archive,
    }), cache=cache)
    try:
        client.snapshot("r1", "abc")
        client.snapshot("r1", "abc", force=True)
        assert calls["c"] == 2  # force saltea la caché y re-descarga
    finally:
        client.close()
        cache.close()


def test_session_ok():
    def user(request):
        return httpx.Response(200, json={"username": "jane", "display_name": "Jane"})

    def workspace(request):
        return httpx.Response(200, json={"slug": "ws", "is_private": True, "name": "WS"})

    client = BitbucketClient("ws", "tok", transport=_transport({
        ("GET", "/2.0/user"): user,
        ("GET", "/2.0/workspaces/ws"): workspace,
    }))
    try:
        info, identity = client.session()
    finally:
        client.close()
    assert identity == "Jane (@jane)"
    assert info.slug == "ws"


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
    assert found[0].resolved_branch == "release"


def test_list_repos_two_prefixes_one_workspace_scan(tmp_path):
    """BBIT-46: prefijos distintos comparten el índice filter-free sin re-barrer."""
    from bbit_release.cache import ReleaseCache

    hits = {"repos": 0}

    def repos_list(request):
        hits["repos"] += 1
        return httpx.Response(200, json={"values": [
            {"slug": "svc-accounting-api", "name": "Accounting API", "workspace": {"slug": "ws"}},
            {"slug": "svc-billing", "name": "Billing", "workspace": {"slug": "ws"}},
        ]})

    routes = {("GET", "/2.0/repositories/ws"): repos_list}
    cache = ReleaseCache(db_path=tmp_path / "index.db")
    client = BitbucketClient("ws", "tok", cache=cache, transport=_transport(routes))
    try:
        first = client.list_repos(prefixes=["svc-accounting-"])
        assert [r.slug for r in first] == ["svc-accounting-api"]
        assert hits["repos"] == 1

        second = client.list_repos(prefixes=["svc-"])
        assert [r.slug for r in second] == ["svc-accounting-api", "svc-billing"]
        assert hits["repos"] == 1  # una sola búsqueda del workspace
    finally:
        client.close()
        cache.close()


def test_repos_with_branch_delta_cache(tmp_path):
    """Con cache, solo los repos nuevos se resuelven contra la API en re-consultas."""
    from bbit_release.cache import ReleaseCache

    hits = {"branch": 0}

    def repos_list(request):
        return httpx.Response(200, json={"values": [
            {"slug": "a", "name": "A", "workspace": {"slug": "ws"}},
            {"slug": "b", "name": "B", "workspace": {"slug": "ws"}},
        ]})

    def repos_list_with_c(request):
        return httpx.Response(200, json={"values": [
            {"slug": "a", "name": "A", "workspace": {"slug": "ws"}},
            {"slug": "b", "name": "B", "workspace": {"slug": "ws"}},
            {"slug": "c", "name": "C", "workspace": {"slug": "ws"}},
        ]})

    def branch_a(request):
        hits["branch"] += 1
        return httpx.Response(200, json={"name": "release"})

    def branch_b(request):
        hits["branch"] += 1
        return httpx.Response(404, json={"message": "not found"})

    def branch_c(request):
        hits["branch"] += 1
        return httpx.Response(200, json={"name": "release"})

    routes = {
        ("GET", "/2.0/repositories/ws"): repos_list,
        ("GET", "/2.0/repositories/ws/a/refs/branches/release"): branch_a,
        ("GET", "/2.0/repositories/ws/b/refs/branches/release"): branch_b,
    }

    cache = ReleaseCache(db_path=tmp_path / "delta.db")
    client = BitbucketClient("ws", "tok", cache=cache, transport=_transport(routes))
    try:
        first = client.repos_with_branch("release")
        assert [r.slug for r in first] == ["a"]
        assert hits["branch"] == 2

        second = client.repos_with_branch("release")
        # BBIT-46: el universo (list_repos) queda cacheado filter-free; el repo
        # nuevo "c" no aparece hasta invalidar el índice o que expire su TTL, y
        # a/b NO se re-resuelven (delta de branch_refs intacto).
        assert [r.slug for r in second] == ["a"]
        assert hits["branch"] == 2

        routes[("GET", "/2.0/repositories/ws/c/refs/branches/release")] = branch_c
        # Nuevo repo: invalidar SOLO el índice de repos (no el delta de ramas).
        cache.invalidate("ws", "all", {})
        routes[("GET", "/2.0/repositories/ws")] = repos_list_with_c

        third = client.repos_with_branch("release")
        assert [r.slug for r in third] == ["a", "c"]
        assert hits["branch"] == 3
    finally:
        client.close()


def test_resolve_branch_prefix_matches_latest():
    def branches_list(request):
        return httpx.Response(200, json={"values": [
            {"name": "release/REP-325073-V1"},
            {"name": "release/REP-325073-V2"},
            {"name": "release/REP-999999-X"},
        ]})

    client = BitbucketClient("ws", "tok", transport=_transport({
        ("GET", "/2.0/repositories/ws/r1/refs/branches"): branches_list,
    }))
    try:
        resolved = client.resolve_branch("r1", "release/REP-325073")
    finally:
        client.close()
    assert resolved == "release/REP-325073-V2"


def test_resolve_branch_exact_wins():
    def exact(request):
        return httpx.Response(200, json={"name": "main"})

    def branches_list(request):
        return httpx.Response(200, json={"values": [{"name": "main-v1"}]})

    client = BitbucketClient("ws", "tok", transport=_transport({
        ("GET", "/2.0/repositories/ws/r1/refs/branches/main"): exact,
        ("GET", "/2.0/repositories/ws/r1/refs/branches"): branches_list,
    }))
    try:
        resolved = client.resolve_branch("r1", "main")
    finally:
        client.close()
    assert resolved == "main"


def test_resolve_branch_none():
    client = BitbucketClient("ws", "tok", transport=_transport({}))
    try:
        resolved = client.resolve_branch("r1", "missing")
    finally:
        client.close()
    assert resolved == ""


def test_latest_branch_version_suffix():
    from bbit_release.bitbucket.client import BitbucketClient as B
    names = ["release/REP/a-V1", "release/REP/a-V2", "release/REP/a-V10", "release/REP/a"]
    assert B._latest_branch(names) == "release/REP/a-V10"


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


def test_rate_limit_429_no_retry(monkeypatch):
    """Con MAX_RETRIES=3 un 429 con Retry-After se reintenta MAX_RETRIES veces
    (backoff respetando el header) y luego falla con BitbucketError."""
    monkeypatch.setattr("bbit_release.bitbucket.client.MAX_RETRIES", 3)
    monkeypatch.setattr("bbit_release.bitbucket.client.time.sleep", lambda _s: None)
    calls = 0

    def notif(request):
        nonlocal calls
        calls += 1
        return httpx.Response(429, headers={"Retry-After": "0.5"}, json={"message": "rate limit"})

    client = BitbucketClient("ws", "tok", transport=_transport({
        ("GET", "/2.0/repositories/ws/r1/refs/tags"): notif,
    }))
    with pytest.raises(BitbucketError, match="rate limit alcanzado"):
        try:
            client.tags_on_commit("r1", "abc")
        finally:
            client.close()
    assert calls == 3


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


def test_commit_for_branch_with_resolved_skips_resolve():
    def commits(request):
        return httpx.Response(200, json={"values": [{"hash": "abc123"}]})

    client = BitbucketClient("ws", "tok", transport=_transport({
        ("GET", "/2.0/repositories/ws/r1/commits/release/REP-1-V2"): commits,
    }))
    try:
        h = client.commit_for_branch("r1", "release/REP-1", resolved="release/REP-1-V2")
    finally:
        client.close()
    assert h == "abc123"


def test_commit_for_branch_with_resolved_missing_returns_empty():
    client = BitbucketClient("ws", "tok", transport=_transport({}))
    try:
        h = client.commit_for_branch("r1", "release/REP-1", resolved="release/REP-1-V2")
    finally:
        client.close()
    assert h == ""


def test_commits_behind_uses_size_single_request():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={"values": [], "size": 5})

    client = BitbucketClient("ws", "tok", transport=_transport({
        ("GET", "/2.0/repositories/ws/r1/commits/master"): handler,
    }))
    try:
        behind = client.commits_behind("r1", "release", "master")
    finally:
        client.close()
    assert behind == 5
    assert len(calls) == 1


def test_commits_behind_fallback_paginates_without_size():
    hits = {"n": 0}

    def handler(request):
        hits["n"] += 1
        if hits["n"] == 1:
            return httpx.Response(200, json={
                "values": [{"hash": f"c{i}"} for i in range(3)],
                "size": None,
                "next": "https://api.bitbucket.org/2.0/repositories/ws/r1/commits/master?exclude=release&pagelen=100&page=2",
            })
        return httpx.Response(200, json={"values": [{"hash": "c3"}]})

    client = BitbucketClient("ws", "tok", transport=_transport({
        ("GET", "/2.0/repositories/ws/r1/commits/master"): handler,
    }))
    try:
        behind = client.commits_behind("r1", "release", "master")
    finally:
        client.close()
    assert behind == 4
    assert hits["n"] == 2


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


def test_find_pr_ignores_non_open():
    payload = {
        "values": [
            {
                "id": 5,
                "title": "Merged",
                "state": "MERGED",
                "links": {"html": {"href": "http://pr/5"}},
                "source": {"commit": {"hash": "old"}},
            },
            {
                "id": 6,
                "title": "Open",
                "state": "OPEN",
                "links": {"html": {"href": "http://pr/6"}},
                "source": {"commit": {"hash": "new1"}},
            },
        ]
    }
    client = BitbucketClient("ws", "tok", transport=_transport({
        ("GET", "/2.0/repositories/ws/r1/pullrequests"): lambda r: httpx.Response(200, json=payload),
    }))
    try:
        pr = client.find_pr("r1", "release", "master")
    finally:
        client.close()
    assert pr is not None
    assert pr["id"] == 6
    assert pr["state"] == "OPEN"
    assert pr["source_commit"] == "new1"


def test_find_pr_none_when_only_merged():
    payload = {
        "values": [{
            "id": 5,
            "title": "Merged",
            "state": "MERGED",
            "links": {"html": {"href": "http://pr/5"}},
            "source": {"commit": {"hash": "old"}},
        }]
    }
    client = BitbucketClient("ws", "tok", transport=_transport({
        ("GET", "/2.0/repositories/ws/r1/pullrequests"): lambda r: httpx.Response(200, json=payload),
    }))
    try:
        pr = client.find_pr("r1", "release", "master")
    finally:
        client.close()
    assert pr is None


def test_has_commits_ahead_true():
    client = BitbucketClient("ws", "tok", transport=_transport({
        ("GET", "/2.0/repositories/ws/r1/commits/release"): lambda r: httpx.Response(
            200, json={"values": [{"hash": "abc"}]}
        ),
    }))
    try:
        assert client.has_commits_ahead("r1", "release", "master") is True
    finally:
        client.close()


def test_has_commits_ahead_false():
    client = BitbucketClient("ws", "tok", transport=_transport({
        ("GET", "/2.0/repositories/ws/r1/commits/release"): lambda r: httpx.Response(200, json={"values": []}),
    }))
    try:
        assert client.has_commits_ahead("r1", "release", "master") is False
    finally:
        client.close()


def test_has_commits_ahead_ignores_api_error():
    client = BitbucketClient("ws", "tok", transport=_transport({
        ("GET", "/2.0/repositories/ws/r1/commits/release"): lambda r: httpx.Response(500, json={}),
    }))
    try:
        assert client.has_commits_ahead("r1", "release", "master") is True
    finally:
        client.close()


def test_list_files_recursive_paginated():
    """El ref raíz usa trailing slash y se recorren subdirectorios."""
    def handler(request):
        q = request.url.params
        if q.get("page") == "2":
            return httpx.Response(200, json={"values": [{"path": "b.txt", "type": "file"}]})
        if request.url.path == "/2.0/repositories/ws/r1/src/abc/":
            return httpx.Response(200, json={
                "values": [
                    {"path": "z.txt", "type": "file"},
                    {"path": "config", "type": "commit_directory"},
                ],
                "next": "https://api.bitbucket.org/2.0/repositories/ws/r1/src/abc?page=2",
            })
        if request.url.path == "/2.0/repositories/ws/r1/src/abc/config":
            return httpx.Response(200, json={
                "values": [{"path": "config/nested.yaml", "type": "commit_file"}],
            })
        return httpx.Response(404, json={})

    client = BitbucketClient("ws", "tok", transport=httpx.MockTransport(handler))
    try:
        files = client.list_files("r1", "abc")
    finally:
        client.close()
    assert files == ["b.txt", "config/nested.yaml", "z.txt"]


def test_list_files_empty_on_404():
    client = BitbucketClient("ws", "tok", transport=_transport({}))
    try:
        assert client.list_files("r1", "abc") == []
    finally:
        client.close()


def test_list_files_branch_name_with_slash():
    """Un ref con '/' (ej. release/REP-325073) no rompe URL ni recursión."""
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/2.0/repositories/ws/r1/src/release/REP-325073/config":
            payload = {"values": [{"type": "file", "path": "config/nested.yaml"}]}
        elif request.url.path == "/2.0/repositories/ws/r1/src/release/REP-325073/":
            payload = {"values": [
                {"type": "commit_directory", "path": "config"},
                {"type": "commit_file", "path": "app.yaml"},
            ]}
        else:
            return httpx.Response(404, json={})
        return httpx.Response(200, json=payload)

    client = BitbucketClient("ws", "tok", transport=httpx.MockTransport(handler))
    try:
        files = client.list_files("r1", "release/REP-325073")
    finally:
        client.close()
    assert files == ["app.yaml", "config/nested.yaml"]
    assert calls == [
        "/2.0/repositories/ws/r1/src/release/REP-325073/",
        "/2.0/repositories/ws/r1/src/release/REP-325073/config",
    ]


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


def test_upsert_file():
    def post(request):
        assert request.url.path == "/2.0/repositories/ws/r1/src"
        body = request.read()
        assert b"name=\"message\"" in body and b"feat: workflow" in body
        assert b"name=\"branch\"" in body and b"master" in body
        assert b"name=\".circleci/config.yml\"" in body
        assert b"; filename=\"config.yml\"" in body
        assert b"uat-deploy-on-tag" in body
        return httpx.Response(201, json={"hash": "abc123", "subject": "feat"})

    client = BitbucketClient("ws", "tok", transport=_transport({
        ("POST", "/2.0/repositories/ws/r1/src"): post,
    }))
    try:
        out = client.upsert_file("r1", "master", ".circleci/config.yml",
                                 "uat-deploy-on-tag:\n  jobs: []", "feat: workflow")
    finally:
        client.close()
    assert out["hash"] == "abc123"


def test_upsert_file_requires_write_scope():
    client = BitbucketClient("ws", "tok", transport=_transport({
        ("POST", "/2.0/repositories/ws/r1/src"): lambda r: httpx.Response(403, json={"message": "no"}),
    }))
    try:
        with pytest.raises(BitbucketAuthError):
            client.upsert_file("r1", "master", ".circleci/config.yml", "x: 1", "msg")
    finally:
        client.close()


# -- service_call hook ------------------------------------------------------

def test_response_hook_captures_raw():
    recorded: list[dict] = []

    def branch(request):
        return httpx.Response(200, json={"name": "main", "target": {"hash": "abc"}})

    client = BitbucketClient(
        "ws", "tok",
        transport=_transport({("GET", "/2.0/repositories/ws/r1/refs/branches/main"): branch}),
        recorder=recorded.append,
    )
    try:
        assert client.has_branch("r1", "main") is True
    finally:
        client.close()

    assert len(recorded) == 1
    entry = recorded[0]
    assert entry["source"] == "bitbucket"
    assert entry["method"] == "GET"
    assert entry["url"] == "/2.0/repositories/ws/r1/refs/branches/main"
    assert entry["status"] == 200
    assert "target" in entry["response"]
    assert entry["duration_ms"] >= 0


def test_response_hook_captures_error_status():
    recorded: list[dict] = []

    client = BitbucketClient("ws", "tok", transport=_transport({
        ("GET", "/2.0/repositories/ws/r1/refs/branches/nope"): lambda r: httpx.Response(
            404, json={"error": {"message": "missing"}}
        ),
    }), recorder=recorded.append)
    try:
        client.has_branch("r1", "nope")
    finally:
        client.close()

    assert len(recorded) == 1
    assert recorded[0]["status"] == 404
    assert "missing" in recorded[0]["response"]


def test_recorder_failure_does_not_break_client():
    def boom(_entry):
        raise RuntimeError("recorder exploded")

    def branch(request):
        return httpx.Response(200, json={"name": "main"})

    client = BitbucketClient("ws", "tok", transport=_transport({
        ("GET", "/2.0/repositories/ws/r1/refs/branches/main"): branch,
    }), recorder=boom)
    try:
        assert client.has_branch("r1", "main") is True
    finally:
        client.close()


def test_response_hook_streaming_body():
    """El hook debe leer el body streamed (caso httpx real), no asumirlos leídos."""

    class Chunks(httpx._types.SyncByteStream):
        def __init__(self, chunks):
            self._chunks = iter(chunks)

        def __iter__(self):
            return self._chunks

        def close(self):
            pass

    def stream_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=Chunks([b'{"name": "main"}']))

    recorded: list[dict] = []
    client = BitbucketClient(
        "ws", "tok",
        transport=httpx.MockTransport(stream_handler),
        recorder=recorded.append,
    )
    try:
        assert client.has_branch("r1", "main") is True
    finally:
        client.close()

    assert len(recorded) == 1
    assert recorded[0]["status"] == 200
    assert recorded[0]["response"] == '{"name": "main"}'


def test_no_recorder_returns_same_behavior():
    def branch(request):
        return httpx.Response(200, json={"name": "main"})

    client = BitbucketClient("ws", "tok", transport=_transport({
        ("GET", "/2.0/repositories/ws/r1/refs/branches/main"): branch,
    }))
    try:
        assert client.has_branch("r1", "main") is True
    finally:
        client.close()
