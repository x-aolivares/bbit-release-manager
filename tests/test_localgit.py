"""Tests del motor local-git (BBIT-33).

Construye un repo remote 'bare' ficticio, clona localmente con el
LocalRepoClient y valida las lecturas (commit_for_branch, tags_on_commit,
diff, list_files, raw_file, snapshot, repos_with_branch) sin llamar a
la API de Bitbucket.
"""

from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

import pytest

from bbit_release.bitbucket.client import DiffResult, Repository
from bbit_release.localgit.client import LocalRepoClient


class _FakeConfig:
    """Mínimo stub que satisface las queries de credential y clones_dir."""

    def __init__(self, bitbucket_url: str = "https://bitbucket.org", bitbucket_username: str = "test",
                 bitbucket_token: str = "FAKE-TOKEN", git_clones_dir: str = ""):
        self.bitbucket_url = bitbucket_url
        self.bitbucket_username = bitbucket_username
        self.bitbucket_token = bitbucket_token
        self.git_clones_dir = git_clones_dir


class _FakeBBClient:
    """Duck-typed stub de BitbucketClient: solo workspace (requerido)."""

    def __init__(self, workspace: str = "testorg"):
        self.workspace = workspace

    def raw_file(self, slug, ref, path):
        return None


WORKSPACE = "testorg"
REPO = "sample"


@pytest.fixture()
def git_remote(tmp_path: Path) -> Path:
    """Bare remote con commits, dos ramas y un tag."""
    bare = tmp_path / "remote" / REPO
    bare.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)

    work = tmp_path / "work"
    work.mkdir()
    subprocess.run(["git", "init", str(work)], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=work, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=work, check=True, capture_output=True)

    # Commit en master
    (work / "app" / "config.yml").mkdir(parents=True, exist_ok=True)
    (work / "app" / "config.yml" / "db.json").write_text('{"db": "prod"}', encoding="utf-8")
    (work / "README.md").write_text("# sample\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=work, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init master"], cwd=work, check=True, capture_output=True)

    # Tag en master: uat-450
    subprocess.run(["git", "tag", "-a", "uat-450", "-m", "uat-450"], cwd=work, check=True, capture_output=True)

    master_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=work, capture_output=True, text=True, check=True
    ).stdout.strip()

    # Branch release/REP-123-V2
    subprocess.run(["git", "checkout", "-b", "release/REP-123-V2"], cwd=work, check=True, capture_output=True)
    (work / "app" / "config.yml" / "feature.json").write_text('{"feature": true}', encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=work, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "feat branch"], cwd=work, check=True, capture_output=True)
    subprocess.run(["git", "tag", "-a", "stgp-100", "-m", "stgp-100"], cwd=work, check=True, capture_output=True)

    release_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=work, capture_output=True, text=True, check=True
    ).stdout.strip()

    subprocess.run(["git", "checkout", "master"], cwd=work, check=True, capture_output=True)
    subprocess.run(["git", "push", "--all", str(bare)], cwd=work, check=True, capture_output=True)
    subprocess.run(["git", "push", "--tags", str(bare)], cwd=work, check=True, capture_output=True)

    return bare


def _client(tmp_path: Path, git_remote: Path, cfg: _FakeConfig | None = None) -> LocalRepoClient:
    clones = tmp_path / "clones"
    clones.mkdir()
    cfg = cfg or _FakeConfig(git_clones_dir=str(clones))
    return LocalRepoClient(
        _FakeBBClient(),
        str(clones),
        cfg=cfg,
        remote_url=lambda slug: str(tmp_path / "remote" / slug),
    )


# ---------------------------------------------------------------------------

class TestEnsureRepo:
    def test_clones_on_first_call(self, tmp_path, git_remote):
        c = _client(tmp_path, git_remote)
        assert c.available(REPO) is False
        ok = c.ensure_repo(REPO, force=True)
        assert ok
        assert c.available(REPO)

    def test_fetch_on_second_call(self, tmp_path, git_remote):
        c = _client(tmp_path, git_remote)
        c.ensure_repo(REPO, force=True)
        ok = c.ensure_repo(REPO, force=False)
        assert ok

    def test_returns_false_for_missing_slug(self, tmp_path):
        clones = tmp_path / "clones"; clones.mkdir()
        c = LocalRepoClient(_FakeBBClient(), str(clones), cfg=_FakeConfig(),
                            remote_url=lambda s: str(tmp_path / "nonexistent" / s))
        assert c.ensure_repo("missing") is False


class TestAvailable:
    def test_unknown_slug(self, tmp_path):
        c = _client(tmp_path, Path("/nonexistent"))
        assert c.available("x") is False

    def test_cloned_slug(self, tmp_path, git_remote):
        c = _client(tmp_path, git_remote)
        c.ensure_repo(REPO, force=True)
        assert c.available(REPO)


class TestCommitForBranch:
    def test_exact_branch(self, tmp_path, git_remote):
        c = _client(tmp_path, git_remote)
        c.ensure_repo(REPO, force=True)
        sha = c.commit_for_branch(REPO, "master")
        assert len(sha) == 40
        assert all(ch in "0123456789abcdef" for ch in sha)

    def test_variant_branch(self, tmp_path, git_remote):
        c = _client(tmp_path, git_remote)
        c.ensure_repo(REPO, force=True)
        sha = c.commit_for_branch(REPO, "release/REP-123")
        assert len(sha) == 40

    def test_nonexistent_branch(self, tmp_path, git_remote):
        c = _client(tmp_path, git_remote)
        c.ensure_repo(REPO, force=True)
        sha = c.commit_for_branch(REPO, "no-existe")
        assert sha == ""


class TestTagsOnCommit:
    def test_returns_tags(self, tmp_path, git_remote):
        c = _client(tmp_path, git_remote)
        c.ensure_repo(REPO, force=True)
        master_sha = c.commit_for_branch(REPO, "master")
        tags = c.tags_on_commit(REPO, master_sha)
        assert len(tags) == 1
        assert tags[0]["name"] == "uat-450"

    def test_release_branch_tag(self, tmp_path, git_remote):
        c = _client(tmp_path, git_remote)
        c.ensure_repo(REPO, force=True)
        sha = c.commit_for_branch(REPO, "release/REP-123-V2")
        tags = c.tags_on_commit(REPO, sha)
        assert tags[0]["name"] == "stgp-100"


class TestDiff:
    def test_diff_returns_result(self, tmp_path, git_remote):
        c = _client(tmp_path, git_remote)
        c.ensure_repo(REPO, force=True)
        d = c.diff(REPO, "master", "release/REP-123-V2")
        assert isinstance(d, DiffResult)
        assert len(d.files) >= 1
        paths = {f.path for f in d.files}
        assert "app/config.yml/feature.json" in paths


class TestListFiles:
    def test_returns_sorted_files(self, tmp_path, git_remote):
        c = _client(tmp_path, git_remote)
        c.ensure_repo(REPO, force=True)
        files = c.list_files(REPO, "master")
        assert isinstance(files, list)
        assert "README.md" in files


class TestRawFile:
    def test_returns_content(self, tmp_path, git_remote):
        c = _client(tmp_path, git_remote)
        c.ensure_repo(REPO, force=True)
        content = c.raw_file(REPO, "master", "README.md")
        assert content is not None
        assert "sample" in content

    def test_missing_file(self, tmp_path, git_remote):
        c = _client(tmp_path, git_remote)
        c.ensure_repo(REPO, force=True)
        assert c.raw_file(REPO, "master", "no-existe.txt") is None


class TestSnapshot:
    def test_returns_dict(self, tmp_path, git_remote):
        c = _client(tmp_path, git_remote)
        c.ensure_repo(REPO, force=True)
        snap = c.snapshot(REPO, "master")
        assert isinstance(snap, dict)
        assert any("README" in k for k in snap.keys())


class TestReposWithBranch:
    def test_includes_matching_repos(self, tmp_path, git_remote):
        c = _client(tmp_path, git_remote)
        c.ensure_repo(REPO, force=True)
        repos = [Repository(slug=REPO, name=REPO, workspace=WORKSPACE)]
        matched = c.repos_with_branch("release/REP-123", repos=repos)
        assert len(matched) == 1
        assert matched[0].resolved_branch == "release/REP-123-V2"

    def test_excludes_missing_branch(self, tmp_path, git_remote):
        c = _client(tmp_path, git_remote)
        c.ensure_repo(REPO, force=True)
        repos = [Repository(slug=REPO, name=REPO, workspace=WORKSPACE)]
        matched = c.repos_with_branch("release/NOEXISTE-999", repos=repos)
        assert len(matched) == 0

    def test_clones_on_demand_if_not_available(self, tmp_path, git_remote):
        """Cuando se llama repos_with_branch() y el repo no está clonado aún,
        debe clonarlo de forma sincrónica (on-demand) antes de buscar la rama.
        
        Esto evita el problema donde el user clickea "Obtener Repositorios"
        antes de que el background thread termine de clonar (BBIT-33 fix).
        """
        c = _client(tmp_path, git_remote)
        # NO llamamos ensure_repo: el repo aún no está clonado
        assert c.available(REPO) is False
        
        repos = [Repository(slug=REPO, name=REPO, workspace=WORKSPACE)]
        # repos_with_branch debe clonar el repo de forma sincrónica
        matched = c.repos_with_branch("release/REP-123", repos=repos)
        
        # Debe encontrar la rama (se clonó automáticamente)
        assert len(matched) == 1
        assert matched[0].resolved_branch == "release/REP-123-V2"
        # Y ahora el repo debe estar disponible
        assert c.available(REPO)


class TestFallback:
    """Cuando el repo no está clonado, delega al stub API sin explotar."""

    def test_commit_for_branch_uncloned(self, tmp_path):
        class _StubBB(_FakeBBClient):
            def commit_for_branch(self, slug, branch, resolved=""):
                return "fake-commit-hash"
            def resolve_branch(self, slug, branch):
                return "fake-branch"

        c = LocalRepoClient(_StubBB(), str(tmp_path / "clones"), cfg=_FakeConfig(),
                            remote_url=lambda s: str(tmp_path / "nonexistent" / s))
        sha = c.commit_for_branch("missing", "master")
        assert sha == "fake-commit-hash"

    def test_diff_uncloned(self, tmp_path):
        class _StubBB(_FakeBBClient):
            def diff(self, slug, from_ref, to_ref):
                return DiffResult(repo=slug, from_ref=from_ref, to_ref=to_ref, files=[])
            def workspace(self):
                return WORKSPACE

        c = LocalRepoClient(_StubBB(), str(tmp_path / "clones"), cfg=_FakeConfig(),
                            remote_url=lambda s: str(tmp_path / "nonexistent" / s))
        d = c.diff("missing", "master", "release")
        assert d.files == []