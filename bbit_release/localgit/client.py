"""Adapter local-git (BBIT-33): reemplaza las lecturas pesadas de Bitbucket
(repos/ramas/tags/diff/params) por comandos git sobre clones locales, dejando
las operaciones de escritura/PR en el BitbucketClient original.

Nunca toca el working tree: todas las lecturas se resuelven contra la object
database ushando refs `origin/<rama>` (git show / ls-tree / archive / tag),
por lo que las operaciones son no destructivas por diseño.

Si un repo no está clonado (o el git falla), las operaciones caen al API por
repo, de modo que el flujo nunca se rompe si el área de clones es parcial.
"""

from __future__ import annotations

import io
import logging
import os
import re
import subprocess
import tarfile
import threading
import time
from pathlib import Path
from typing import Callable

from ..bitbucket.client import (
    BitbucketClient,
    DiffResult,
    Repository,
)
from ..config import Config

log = logging.getLogger(__name__)

GIT_BIN = os.environ.get("BBIT_GIT_BIN", "git")

# Git es local, pero clonar N repos en paralelo satura disco/red: acotamos
# los subprocesos simultáneos a un tope conservador.
GIT_MAX_CONCURRENT = 4
GIT_CLONE_TIMEOUT = 600.0
GIT_FETCH_TIMEOUT = 300.0
GIT_READ_TIMEOUT = 60.0

# Refresco mínimo entre fetch de un mismo repo (evita fetch en cada request).
FETCH_MIN_AGE = 120.0

FULL_SHA = re.compile(r"^[0-9a-f]{40}$")


class LocalRepoClient:
    """Adaptador duck-typed sobre ``BitbucketClient`` con lecturas vía git local.

    Expone la misma interfaz que el flow usa (repos_with_branch,
    commit_for_branch, tags_on_commit, diff, list_files, raw_file, snapshot)
    y delega al cliente original lo que no se puede deducir localmente
    (find_pr, create_pr, has_commits_ahead, branch_url, ...) mediante
    ``__getattr__``.
    """

    def __init__(
        self,
        bitbucket_client: BitbucketClient,
        clones_dir: str,
        cfg: Config | None = None,
        remote_url: Callable[[str], str] | None = None,
    ):
        self._bb = bitbucket_client
        self._clones_dir = Path(clones_dir).expanduser()
        self._cfg = cfg or Config()
        # Para tests: permite apuntar los clones a un remote arbitrario por slug.
        self._remote_url = remote_url
        self._clones_dir.mkdir(parents=True, exist_ok=True)
        self._repo_locks: dict[str, threading.Lock] = {}
        self._repo_locks_guard = threading.Lock()
        self._git_sem = threading.BoundedSemaphore(GIT_MAX_CONCURRENT)

    # -- delegación -----------------------------------------------------------

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._bb, name)

    def close(self) -> None:
        self._bb.close()

    def __enter__(self) -> "LocalRepoClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- helpers git ----------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return True

    def repo_path(self, slug: str) -> Path:
        return self._clones_dir / slug

    def available(self, slug: str) -> bool:
        return (self.repo_path(slug) / ".git").exists() or (self.repo_path(slug) / ".git").is_dir()

    def _git_env(self) -> dict:
        """Env para git: nunca pide credenciales en TTY y, si hay usuario+token
        guardados, los inyecta vía credential.helper (fuera del argv, para no
        exponer el secret en la lista de procesos)."""
        env = dict(os.environ)
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GIT_ASKPASS"] = "true"
        username = (self._cfg.bitbucket_username or "").strip()
        token = (self._cfg.bitbucket_token or "").strip()
        if username and token:
            helper = (
                '!f() { printf "username=%s\\npassword=%s\\n" '
                '"$BBIT_GIT_USER" "$BBIT_GIT_TOKEN"; }; f'
            )
            env["BBIT_GIT_USER"] = username
            env["BBIT_GIT_TOKEN"] = token
            env["GIT_CONFIG_COUNT"] = "1"
            env["GIT_CONFIG_KEY_0"] = "credential.helper"
            env["GIT_CONFIG_VALUE_0"] = helper
        return env

    def _remote(self, slug: str) -> str:
        if self._remote_url is not None:
            return self._remote_url(slug)
        url = self._cfg.bitbucket_url or "https://bitbucket.org"
        return f"{url.rstrip('/')}/{self._bb.workspace}/{slug}.git"

    def _run(self, slug: str, args: list[str], timeout: float = GIT_READ_TIMEOUT) -> subprocess.CompletedProcess:
        with self._git_sem:
            try:
                return subprocess.run(
                    [GIT_BIN, *args],
                    cwd=self.repo_path(slug),
                    env=self._git_env(),
                    capture_output=True,
                    timeout=timeout,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                log.warning("git %s %s: %s", slug, " ".join(args[:2]), exc)
                raise

    def _repo_lock(self, slug: str) -> threading.Lock:
        with self._repo_locks_guard:
            lock = self._repo_locks.get(slug)
            if lock is None:
                lock = self._repo_locks[slug] = threading.Lock()
            return lock

    def _last_fetch_age(self, slug: str) -> float:
        try:
            return max(0.0, os.path.getmtime(self.repo_path(slug) / ".git" / "FETCH_HEAD"))
        except OSError:
            return 0.0  # muy reciente -> no fetch

    def ensure_repo(self, slug: str, *, force: bool = False) -> bool:
        """Clona si falta o hace fetch incremental. Devuelve True si queda usable."""
        with self._repo_lock(slug):
            path = self.repo_path(slug)
            try:
                if not self.available(slug):
                    with self._git_sem:
                        proc = subprocess.run(
                            [GIT_BIN, "clone", "--no-checkout", str(self._remote(slug)), str(path)],
                            env=self._git_env(),
                            capture_output=True,
                            timeout=GIT_CLONE_TIMEOUT,
                            check=False,
                        )
                    if proc.returncode != 0:
                        log.warning("clone %s falló: %s", slug, proc.stderr.decode("utf-8", "replace")[-400:])
                        return False
                    return path.exists()
                need_fetch = force or not (path / ".git" / "FETCH_HEAD").exists() or \
                    (time.time() - self._last_fetch_age(slug)) > FETCH_MIN_AGE
                if need_fetch:
                    proc = self._run(slug, ["fetch", "--prune", "origin"], timeout=GIT_FETCH_TIMEOUT)
                    if proc.returncode == 0:
                        return True
                    # Fetch fallido: si ya hay refs, seguimos con lo que hay.
                    log.warning("fetch %s falló: %s", slug, proc.stderr.decode("utf-8", "replace")[-300:])
                return path.exists()
            except (OSError, subprocess.TimeoutExpired) as exc:
                log.warning("ensure_repo %s falló: %s", slug, exc)
                return False

    @staticmethod
    def _refs(branch_or_sha: str) -> list[str]:
        """Refs candidatas para comandos git: raw si es sha completo, si no
        la rama remota de tracking `origin/<rama>`."""
        if FULL_SHA.match(branch_or_sha) or branch_or_sha.endswith("^{}"):
            return [branch_or_sha]
        if "/" in branch_or_sha:
            return [f"origin/{branch_or_sha}", branch_or_sha]
        return [f"origin/{branch_or_sha}"]

    def _resolve_local(self, slug: str, branch: str) -> str:
        """Rama efectiva local: literal o variante `-V{n}` más reciente.

        Lee solo refs locales (refs/remotes/origin), sin red."""
        proc = self._run(slug, ["for-each-ref", "--format=%(refname:short)", f"refs/remotes/origin/{branch}*"])
        lines = (proc.stdout or b"").decode("utf-8", "replace").splitlines()
        names = [ln[len("origin/"):] for ln in lines if ln.startswith("origin/")]
        if not names:
            return ""
        exact = [n for n in names if n == branch]
        if exact:
            return branch
        candidates = [n for n in names if n.startswith(branch)]
        if not candidates:
            return ""
        return self._latest_branch(candidates)

    @staticmethod
    def _latest_branch(names: list[str]) -> str:
        def _ver(n: str):
            m = re.search(r"-V(\d+)(?:\.(\d+))?$", n, re.IGNORECASE)
            if m:
                return int(m.group(1)) * 1000 + int(m.group(2) or 0)
            return -1

        return max(names, key=lambda n: (_ver(n), n))

    def _revparse(self, slug: str, ref: str) -> str:
        try:
            proc = self._run(slug, ["rev-parse", "--verify", "--quiet", ref])
        except subprocess.TimeoutExpired:
            return ""
        if proc.returncode != 0:
            return ""
        return proc.stdout.decode("utf-8", "replace").strip()

    # -- interfaz del flow ----------------------------------------------------

    def repos_with_branch(
        self,
        branch: str,
        prefixes: list[str] | None = None,
        repos: list[Repository] | None = None,
    ) -> list[Repository]:
        """Repos que contienen la rama (clonados → git; el resto → API por repo)."""
        from concurrent.futures import ThreadPoolExecutor

        repos = repos if repos is not None else self._bb.list_repos(prefixes=prefixes)
        if not repos:
            return []
        workers = min(8, len(repos) or 1)
        matched: list[Repository] = []
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = [(repo, ex.submit(self._resolve_for_branch, repo.slug, branch)) for repo in repos]
            for repo, fut in futures:
                try:
                    resolved = fut.result()
                except Exception:
                    continue
                if resolved:
                    matched.append(Repository(
                        slug=repo.slug,
                        name=repo.name,
                        workspace=repo.workspace,
                        default_branch=getattr(repo, "default_branch", "master"),
                        resolved_branch=resolved,
                    ))
        return matched

    def _resolve_for_branch(self, slug: str, branch: str) -> str:
        """Resuelve si la rama existe: local (git) o API fallback."""
        # Si no hay clone, intentar API directamente
        if not self.available(slug):
            try:
                return self._bb.resolve_branch(slug, branch)
            except Exception:
                return ""
        
        # Si hay clone, asegurar que esté actualizado (clone o fetch)
        try:
            if not self.ensure_repo(slug):
                # Si ensure_repo falla, fallback a API
                try:
                    return self._bb.resolve_branch(slug, branch)
                except Exception:
                    return ""
            
            # Ahora intentar leer localmente
            return self._resolve_local(slug, branch)
        except (OSError, subprocess.TimeoutExpired):
            # Git falló, intentar API
            try:
                return self._bb.resolve_branch(slug, branch)
            except Exception:
                return ""

    def commit_for_branch(self, slug: str, branch: str, resolved: str = "") -> str:
        """Último commit de la rama (hash completo)."""
        if not self.available(slug):
            return self._bb.commit_for_branch(slug, branch, resolved=resolved)
        try:
            if not self.ensure_repo(slug):
                return self._bb.commit_for_branch(slug, branch, resolved=resolved)
            target = resolved or self._resolve_local(slug, branch)
            if not target:
                return ""
            return self._revparse(slug, f"origin/{target}") or ""
        except (OSError, subprocess.TimeoutExpired) as exc:
            log.warning("commit_for_branch %s %s: %s", slug, branch, exc)
            return self._bb.commit_for_branch(slug, branch, resolved=resolved)

    def tags_on_commit(self, slug: str, commit_hash: str) -> list[dict]:
        """Tags que apuntan al commit (exacto, como target.hash de la API)."""
        if not commit_hash:
            return []
        if not self.available(slug):
            return self._bb.tags_on_commit(slug, commit_hash)
        try:
            self.ensure_repo(slug)
            proc = self._run(slug, ["tag", "--points-at", commit_hash, "--format=%(refname:short)%09%(creatordate:iso8601)"])
            if proc.returncode != 0:
                return self._bb.tags_on_commit(slug, commit_hash)
            tags: list[dict] = []
            for line in proc.stdout.decode("utf-8", "replace").splitlines():
                name, _, date = line.partition("\t")
                if name:
                    tags.append({"name": name, "date": date or ""})
            return tags
        except (OSError, subprocess.TimeoutExpired) as exc:
            log.warning("tags_on_commit %s %s: %s", slug, commit_hash[:12], exc)
            return self._bb.tags_on_commit(slug, commit_hash)

    def diff(self, slug: str, from_ref: str, to_ref: str) -> DiffResult:
        """Diff entre refs (destino...origen, three-dot) como texto unificado."""
        if not self.available(slug):
            return self._bb.diff(slug, from_ref, to_ref)
        try:
            self.ensure_repo(slug)
            base = self._refs(from_ref)[0]
            target = self._refs(to_ref)[0]
            proc = self._run(slug, ["diff", "--no-color", "--no-ext-diff", f"{base}...{target}"])
            if proc.returncode != 0:
                return self._bb.diff(slug, from_ref, to_ref)
            text = proc.stdout.decode("utf-8", "replace")
            return DiffResult(
                repo=slug,
                from_ref=from_ref,
                to_ref=to_ref,
                files=BitbucketClient._parse_unified_diff(text),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            log.warning("diff %s %s...%s: %s", slug, from_ref, to_ref, exc)
            return self._bb.diff(slug, from_ref, to_ref)

    def list_files(self, slug: str, ref: str, tree: str = "") -> list[str]:
        """Paths (archivo) recursivos de un ref/árbol."""
        if not self.available(slug):
            return self._bb.list_files(slug, ref, tree=tree)
        try:
            self.ensure_repo(slug)
            args = ["ls-tree", "-r", "--name-only", self._refs(ref)[0]]
            if tree:
                args += ["--", tree]
            proc = self._run(slug, args)
            if proc.returncode != 0:
                return self._bb.list_files(slug, ref, tree=tree)
            return sorted(p for p in proc.stdout.decode("utf-8", "replace").splitlines() if p)
        except (OSError, subprocess.TimeoutExpired) as exc:
            log.warning("list_files %s %s: %s", slug, ref, exc)
            return self._bb.list_files(slug, ref, tree=tree)

    def raw_file(self, slug: str, ref: str, path: str) -> str | None:
        """Contenido de un archivo en el ref (git show ref:path)."""
        if not self.available(slug):
            return self._bb.raw_file(slug, ref, path)
        try:
            self.ensure_repo(slug)
            proc = self._run(slug, ["show", "--no-textconv", f"{self._refs(ref)[0]}:{path}"])
            if proc.returncode != 0:
                return self._bb.raw_file(slug, ref, path)
            return proc.stdout.decode("utf-8", "replace")
        except (OSError, subprocess.TimeoutExpired) as exc:
            log.warning("raw_file %s %s %s: %s", slug, ref, path, exc)
            return self._bb.raw_file(slug, ref, path)

    def snapshot(self, slug: str, ref: str) -> dict[str, str] | None:
        """Árbol del ref como {path: contenido} (git archive, un solo proceso)."""
        if not self.available(slug):
            return self._bb.snapshot(slug, ref)
        try:
            self.ensure_repo(slug)
            proc = self._run(slug, ["archive", "--format=tar", self._refs(ref)[0]])
            if proc.returncode != 0:
                return self._bb.snapshot(slug, ref)
            out: dict[str, str] = {}
            with tarfile.open(fileobj=io.BytesIO(proc.stdout), mode="r:") as tar:
                for member in tar.getmembers():
                    if not member.isfile():
                        continue
                    handle = tar.extractfile(member)
                    if handle is None:
                        continue
                    out[member.name] = handle.read().decode("utf-8", errors="replace")
            return out or None
        except (tarfile.TarError, OSError, subprocess.TimeoutExpired) as exc:
            log.warning("snapshot %s %s: %s", slug, ref, exc)
            return self._bb.snapshot(slug, ref)