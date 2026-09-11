"""Cliente de la API REST de Bitbucket Cloud (api.bitbucket.org/2.0).

Autenticación vía Bearer token (API token de Atlassian).
Operaciones de solo lectura para enumeración de repos, resolución de ramas
y obtención de diffs.
"""

from __future__ import annotations

import httpx
import io
import re
import tarfile
import time
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable

from ..http import get_global_rate_limiter, MAX_RETRIES, RETRY_BASE_DELAY

logger = logging.getLogger(__name__)

API_BASE = "https://api.bitbucket.org/2.0"
MAX_WORKERS = 4  # Reducido de 8 a evitar rate limits (429)


class BitbucketError(Exception):
    """Error genérico de la API de Bitbucket."""


class BitbucketAuthError(BitbucketError):
    """Token inválido o sin permisos."""


@dataclass(frozen=True)
class WorkspaceInfo:
    uuid: str
    name: str
    slug: str
    is_private: bool


@dataclass(frozen=True)
class Repository:
    slug: str
    name: str
    workspace: str
    default_branch: str = "master"
    resolved_branch: str = ""


@dataclass(frozen=True)
class DiffFile:
    path: str
    status: str
    lines_added: int = 0
    lines_removed: int = 0
    added_lines: tuple[str, ...] = ()
    removed_lines: tuple[str, ...] = ()


@dataclass(frozen=True)
class DiffResult:
    repo: str
    from_ref: str
    to_ref: str
    files: list[DiffFile]


class BitbucketClient:
    def __init__(
        self,
        workspace: str,
        token: str,
        url: str = "",
        timeout: float = 20.0,
        transport: httpx.BaseTransport | None = None,
        recorder: Callable[[dict], None] | None = None,
        cache: "ReleaseCache | None" = None,
    ):
        if not workspace:
            raise ValueError("workspace es obligatorio")
        if not token:
            raise ValueError("token es obligatorio")
        self.workspace = workspace
        self.cache = cache
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

        hooks: dict[str, list[Callable]] = {}
        if recorder is not None:
            hooks = {
                "request": [self._make_request_hook(recorder)],
                "response": [self._make_response_hook(recorder)],
            }

        self._client = httpx.Client(
            base_url=API_BASE,
            headers=headers,
            timeout=timeout,
            transport=transport,
            event_hooks=hooks,
        )

    def __enter__(self) -> "BitbucketClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def _make_request_hook(self, recorder: Callable[[dict], None]):
        def hook(request: httpx.Request) -> None:
            request.extensions["_bbit_start"] = time.perf_counter()

        return hook

    def _make_response_hook(self, recorder: Callable[[dict], None]):
        def hook(response: httpx.Response) -> None:
            try:
                response.read()
                start = response.request.extensions.get("_bbit_start")
                duration_ms = (time.perf_counter() - start) * 1000 if start else 0.0
                url = response.request.url
                recorder(
                    {
                        "source": "bitbucket",
                        "method": response.request.method,
                        "url": str(url.path) + (f"?{url.query}" if url.query else ""),
                        "params": dict(url.params) if url.params else None,
                        "status": response.status_code,
                        "duration_ms": duration_ms,
                        "response": response.text,
                    }
                )
            except Exception:  # el hook nunca rompe el flujo del cliente
                logger.exception("service_call recorder fallo en bitbucket")

        return hook

    def close(self) -> None:
        self._client.close()

    def _request(self, method: str, path: str, params: dict | None = None):
        last_error: Exception | None = None
        _rate_limiter = get_global_rate_limiter()
        for attempt in range(1, MAX_RETRIES + 1):
            _rate_limiter.acquire()
            try:
                resp = self._client.request(method, path, params=params)
            finally:
                _rate_limiter.release()
            if resp.status_code in (401, 403):
                raise BitbucketAuthError(
                    f"Bitbucket {resp.status_code}: token inválido o sin permisos"
                )
            if resp.status_code == 404:
                return None
            if resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After")
                if retry_after:
                    delay = float(retry_after)
                else:
                    delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
                last_error = BitbucketError(
                    f"Bitbucket 429 en {path}: rate limit alcanzado"
                )
                if attempt < MAX_RETRIES:
                    logger.warning(
                        "Bitbucket 429 en %s (intento %d/%d): esperando %.1fs",
                        path, attempt, MAX_RETRIES, delay,
                    )
                    time.sleep(delay)
                    continue
                break
            if resp.status_code >= 400:
                raise BitbucketError(
                    f"Bitbucket {resp.status_code} en {path}: {resp.text[:300]}"
                )
            return resp.json()
        raise last_error  # type: ignore[misc]

    def session(self) -> tuple[WorkspaceInfo, str]:
        """Valida token y describe workspace."""
        data = self._request("GET", "/user")
        if not data:
            raise BitbucketAuthError("Token inválido: /user no respondió")
        identity = f"{data.get('display_name', '')} (@{data.get('username', '')})"
        try:
            ws_data = self._request("GET", f"/workspaces/{self.workspace}")
        except BitbucketError:
            ws_data = None
        if ws_data:
            info = WorkspaceInfo(
                uuid=(ws_data.get("uuid") or "").strip("{}"),
                name=ws_data.get("name", ""),
                slug=ws_data.get("slug", self.workspace),
                is_private=bool(ws_data.get("is_private", False)),
            )
        else:
            info = WorkspaceInfo(uuid="", name=self.workspace, slug=self.workspace, is_private=True)
        return info, identity

    def list_repos(
        self,
        filter_names: list[str] | None = None,
        prefixes: list[str] | None = None,
    ) -> list[Repository]:
        # CACHE L1: Si hay prefijos, intentar obtener desde caché primero
        if self.cache and prefixes:
            cached_repos = self.cache.get_repos_by_prefix(self.workspace, prefixes)
            if cached_repos is not None:
                repos = [
                    Repository(
                        slug=r["slug"],
                        name=r["name"],
                        workspace=r.get("workspace", self.workspace),
                        default_branch=r.get("default_branch", "master"),
                    )
                    for r in cached_repos
                ]
                return repos
        
        allowed = {n.lower() for n in (filter_names or [])}
        prefs = [p.lower() for p in (prefixes or [])]

        def _keep(slug: str) -> bool:
            s = slug.lower()
            if allowed and s not in allowed:
                return False
            if prefs and not any(s.startswith(p) for p in prefs):
                return False
            return True

        repos: list[Repository] = []
        url: str | None = f"/repositories/{self.workspace}"
        params: dict | None = {"pagelen": 100, "role": "member"}
        while url:
            payload = self._request("GET", url, params=params)
            if payload is None:
                break
            params = None
            for item in payload.get("values", []):
                slug = item.get("slug", "")
                if not _keep(slug):
                    continue
                ws = (item.get("workspace") or {}).get("slug") or self.workspace
                repos.append(
                    Repository(
                        slug=slug,
                        name=item.get("name", slug),
                        workspace=ws,
                        default_branch=(item.get("mainbranch") or {}).get("name", "master"),
                    )
                )
            url = (payload.get("next") or "").replace(API_BASE, "")
            if not url:
                break
        
        # CACHE L1: Guardar repos por prefijo si hay caché
        if self.cache and prefixes:
            repos_data = [
                {
                    "slug": r.slug,
                    "name": r.name,
                    "workspace": r.workspace,
                    "default_branch": r.default_branch,
                }
                for r in repos
            ]
            self.cache.set_repos_by_prefix(self.workspace, prefixes, repos_data)
        
        return repos

    def has_branch(self, slug: str, branch: str) -> bool:
        resp = self._request("GET", f"/repositories/{self.workspace}/{slug}/refs/branches/{branch}")
        return resp is not None

    def list_branches(self, slug: str, prefix: str = "") -> list[str]:
        """Nombres de ramas de un repo (paginado), opcionalmente limitado a un
        prefijo de nombre."""
        # CACHE L2: Obtener todas las branches cacheadas
        if self.cache:
            cached_branches = self.cache.get_repo_branches(self.workspace, slug)
            if cached_branches is not None:
                names = [b["name"] for b in cached_branches]
                # Filtrar por prefijo localmente
                if prefix:
                    p = prefix.lower()
                    names = [n for n in names if n.lower().startswith(p)]
                return names
        
        names: list[str] = []
        url: str | None = f"/repositories/{self.workspace}/{slug}/refs/branches"
        params: dict | None = {"pagelen": 100}
        p = prefix.lower()
        all_branches = []
        while url:
            payload = self._request("GET", url, params=params)
            if payload is None:
                break
            params = None
            for item in payload.get("values", []):
                name = item.get("name", "")
                all_branches.append(name)
                if p and not name.lower().startswith(p):
                    continue
                names.append(name)
            url = (payload.get("next") or "").replace(API_BASE, "")
            if not url:
                break
        
        # CACHE L2: Guardar todas las branches del repo
        if self.cache:
            branches_data = [{"name": b} for b in all_branches]
            self.cache.set_repo_branches(self.workspace, slug, branches_data)
        
        return names

    @staticmethod
    def _latest_branch(names: list[str]) -> str:
        """Elige la rama 'más reciente' entre los nombres: la de mayor sufijo
        `-V{n}` y, si no, la mayor alfabéticamente."""
        def _ver(n: str):
            m = re.search(r"-V(\d+)(?:\.(\d+))?$", n, re.IGNORECASE)
            if m:
                major = int(m.group(1))
                minor = int(m.group(2) or 0)
                return major * 1000 + minor
            return -1

        return max(names, key=lambda n: (_ver(n), n))

    def resolve_branch(self, slug: str, branch: str) -> str:
        """Rama efectiva: la exacta si existe, o la más reciente que empiece con
        el prefijo (p.ej. `release/REP-325073-V2` para `release/REP-325073`).
        Devuelve "" si no hay ninguna."""
        if self.has_branch(slug, branch):
            return branch
        names = self.list_branches(slug, prefix=branch)
        if not names:
            return ""
        return self._latest_branch(names)

    def default_branch(self, slug: str) -> str:
        data = self._request(
            "GET",
            f"/repositories/{self.workspace}/{slug}/refs/branches",
            params={"sort": "-target.date", "pagelen": 1},
        )
        if data and data.get("values"):
            return data["values"][0].get("name", "master")
        return "master"

    def repos_with_branch(
        self,
        branch: str,
        prefixes: list[str] | None = None,
        repos: list["Repository"] | None = None,
    ) -> list[Repository]:
        """Repos (filtrados por prefijo si se pasa) que contienen la rama.

        La rama se resuelve por prefijo: si no existe literal, cuenta como
        presente si existe `branch` seguido de variante (p.ej.
        `release/REP-325073-V2` para `release/REP-325073`).

        El chequeo de existencia es un request por repo; se corre en paralelo
        (ThreadPoolExecutor) para no serializar llamadas a la API.

        `repos` es opcional: si ya tenés la lista completa del workspace
        cacheada, pasala para evitar el `list_repos` paginado interno.
        """
        repos = repos if repos is not None else self.list_repos(prefixes=prefixes)
        if not repos:
            return []
        workers = min(MAX_WORKERS, len(repos) or 1)
        matched: list[Repository] = []
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = [(repo, ex.submit(self.resolve_branch, repo.slug, branch)) for repo in repos]
            for repo, fut in futures:
                try:
                    resolved = fut.result()
                except BitbucketError:
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

    def diff(self, slug: str, from_ref: str, to_ref: str) -> DiffResult:
        """Diff entre refs como texto unificado (Cloud)."""
        resp = self._client.request(
            "GET",
            f"/repositories/{self.workspace}/{slug}/diff/{to_ref}",
            params={"from": from_ref},
            headers={"Accept": "text/plain"},
        )
        if resp.status_code in (401, 403):
            raise BitbucketAuthError(
                f"Bitbucket {resp.status_code}: token inválido o sin permisos"
            )
        if resp.status_code == 404:
            return DiffResult(repo=slug, from_ref=from_ref, to_ref=to_ref, files=[])
        if resp.status_code >= 400:
            raise BitbucketError(
                f"Bitbucket {resp.status_code} en {slug}/diff: {resp.text[:300]}"
            )
        return DiffResult(
            repo=slug,
            from_ref=from_ref,
            to_ref=to_ref,
            files=self._parse_unified_diff(resp.text),
        )

    @staticmethod
    def _parse_unified_diff(text: str) -> list[DiffFile]:
        files: list[DiffFile] = []
        path = ""
        status = "modified"
        added = 0
        removed = 0
        added_lines: list[str] = []
        removed_lines: list[str] = []
        in_hunk = False
        for line in text.splitlines():
            if line.startswith("diff --git "):
                if path:
                    files.append(DiffFile(path, status, added, removed, tuple(added_lines), tuple(removed_lines)))
                path = line.split(" b/", 1)[-1].split(" a/", 1)[-1]
                status = "modified"
                added = 0
                removed = 0
                added_lines = []
                removed_lines = []
                in_hunk = False
            elif line.startswith("new file mode"):
                status = "added"
            elif line.startswith("deleted file mode") or line.startswith("old mode"):
                if line.startswith("deleted file mode"):
                    status = "deleted"
                in_hunk = False
            elif line.startswith("rename"):
                status = "renamed"
            elif line.startswith("@@") or line.startswith("---") or line.startswith("+++"):
                in_hunk = line.startswith("@@")
            elif in_hunk:
                if line.startswith("+") and not line.startswith("+++"):
                    added += 1
                    content = line[1:].strip()
                    if content:
                        added_lines.append(content)
                elif line.startswith("-") and not line.startswith("---"):
                    removed += 1
                    content = line[1:].strip()
                    if content:
                        removed_lines.append(content)
        if path:
            files.append(DiffFile(path, status, added, removed, tuple(added_lines), tuple(removed_lines)))
        return files

    def raw_file(self, slug: str, ref: str, path: str) -> str | None:
        """Obtiene el contenido raw de un archivo."""
        resp = self._client.request(
            "GET",
            f"/repositories/{self.workspace}/{slug}/src/{ref}/{path}",
            headers={"Accept": "text/plain"},
        )
        if resp.status_code >= 400:
            return None
        return resp.text

    def snapshot(self, slug: str, ref: str) -> dict[str, str] | None:
        """Descarga en una request el árbol de un ref: {path: contenido}.

        Usa el tarball de `bitbucket.org/{workspace}/{slug}/get/{ref}.tar.gz`
        (la API de archive no existe en Bitbucket Cloud). Reemplaza
        list_files + N raw_file por una sola descarga por (slug, ref).

        Devuelve None si el ref no existe o el contenido no es un tarball
        utilizable. Los paths salen relativos, sin el prefijo raíz
        `{workspace}-{slug}-{sha}/` del tarball.
        """
        url = f"https://bitbucket.org/{self.workspace}/{slug}/get/{ref}.tar.gz"
        _rate_limiter = get_global_rate_limiter()
        _rate_limiter.acquire()
        try:
            resp = self._client.request(
                "GET", url, headers={"Accept": "application/x-tar-gz"}
            )
        finally:
            _rate_limiter.release()
        if resp.status_code >= 400:
            logger.info("snapshot %s %s: status %s", slug, ref, resp.status_code)
            return None
        out: dict[str, str] = {}
        try:
            with tarfile.open(fileobj=io.BytesIO(resp.content), mode="r:gz") as tar:
                for member in tar.getmembers():
                    if not member.isfile():
                        continue
                    parts = member.name.split("/", 1)
                    path = parts[1] if len(parts) > 1 else parts[0]
                    if not path:
                        continue
                    handle = tar.extractfile(member)
                    if handle is None:
                        continue
                    out[path] = handle.read().decode("utf-8", errors="replace")
        except tarfile.TarError as exc:
            logger.warning("snapshot %s %s: tarball inválido (%s)", slug, ref, exc)
            return None
        return out

    def list_files(self, slug: str, ref: str, tree: str = "") -> list[str]:
        """Lista los paths de tipo archivo bajo un ref/árbol, recursivo.

        GET /src/{ref}/{tree} devuelve un nivel del árbol con paginación;
        se recorre hasta la base. Resultado ordenado y sin duplicados.

        El ref raíz (sin `tree`) requiere el trailing slash en la URL
        (`/src/{ref}/`): la API responde 404 sin él. Los subdirectorios
        (`tree` no vacío) funcionan igual con o sin.
        """
        files: set[str] = set()
        base = f"/repositories/{self.workspace}/{slug}/src/{ref}/{tree}".rstrip("/")
        if not tree:
            base += "/"
        url: str | None = base
        params: dict | None = {"pagelen": 100}
        while url:
            payload = self._request("GET", url, params=params)
            if payload is None:
                break
            params = None
            for item in payload.get("values", []):
                if not isinstance(item, dict):
                    continue
                name = item.get("type", "")
                if name.endswith("directory"):
                    files.update(self.list_files(slug, ref, item.get("path", "")))
                elif name.endswith("file"):
                    files.add(item.get("path", ""))
            url = (payload.get("next") or "").replace(API_BASE, "")
            if not url:
                break
        return sorted(files)

    def commit_for_branch(self, slug: str, branch: str, resolved: str = "") -> str:
        """Último commit de una rama (hash completo).

        `resolved` es la rama efectiva ya resuelta (p.ej. del barrido de
        `repos_with_branch`): si viene, se consulta directo y se evita
        re-resolver (ahorra 2-3 requests cuando la rama literal no existe y
        hay variante `-V2`). Sin `resolved`, cae en `resolve_branch`."""
        target = resolved or branch
        data = self._request(
            "GET",
            f"/repositories/{self.workspace}/{slug}/commits/{target}",
            params={"pagelen": 1},
        )
        if data and data.get("values"):
            return data["values"][0].get("hash", "")
        if resolved:
            return ""
        resolved = self.resolve_branch(slug, branch)
        if not resolved or resolved == branch:
            return ""
        data = self._request(
            "GET",
            f"/repositories/{self.workspace}/{slug}/commits/{resolved}",
            params={"pagelen": 1},
        )
        if not data or not data.get("values"):
            return ""
        return data["values"][0].get("hash", "")

    def has_commits_ahead(self, slug: str, branch: str, base: str) -> bool:
        """¿La rama tiene al menos un commit que base aún no tiene?

        GET /commits/{branch}?exclude={base} → commits de branch ausentes en
        base: es lo que determina si un PR branch → base se puede crear.
        Ante un error no crítico asume True para no bloquear la creación.
        """
        try:
            data = self._request(
                "GET",
                f"/repositories/{self.workspace}/{slug}/commits/{branch}",
                params={"exclude": base, "pagelen": 1},
            )
        except BitbucketAuthError:
            raise
        except BitbucketError:
            return True
        return bool(data and data.get("values"))

    def commits_behind(self, slug: str, branch: str, base: str) -> int:
        """Cantidad de commits en base que la rama no tiene (gap de sync).

        GET /commits/{base}?exclude={branch} → commits de base ausentes en branch.
        Pide `pagelen=100` y lee `size` del envelope (el total de la consulta)
        en un solo request; si el servidor no devuelve `size`, cuenta los
        valores ya recibidos y pagina el resto.
        """
        url_base = f"/repositories/{self.workspace}/{slug}/commits/{base}"
        try:
            data = self._request("GET", url_base, params={"exclude": branch, "pagelen": 100})
        except (BitbucketError, BitbucketAuthError):
            return 0
        size = (data or {}).get("size")
        if isinstance(size, int) and size >= 0:
            return size
        count = 0
        while data and data.get("values"):
            count += len(data["values"])
            url = (data.get("next") or "").replace(API_BASE, "")
            if not url:
                break
            data = self._request("GET", url)
        return count

    def tags_on_commit(self, slug: str, commit_hash: str) -> list[dict]:
        """Tags que apuntan a un commit específico."""
        if not commit_hash:
            return []
        tags: list[dict] = []
        url: str | None = f"/repositories/{self.workspace}/{slug}/refs/tags"
        params: dict | None = {"pagelen": 100}
        if len(commit_hash) >= 40:
            # Filtro server-side por target.hash: evita paginar TODAS las tags
            # del repo cuando ya tenemos el hash completo (el objeto tag referencia
            # un commit propio, distinto del hash del tag anotado).
            params["q"] = f'target.hash="{commit_hash}"'
        while url:
            payload = self._request("GET", url, params=params)
            if payload is None:
                break
            params = None
            for item in payload.get("values", []):
                target = item.get("target") or {}
                thash = target.get("hash", "")
                if thash and (thash == commit_hash
                              or thash.startswith(commit_hash)
                              or commit_hash.startswith(thash)):
                    tags.append({
                        "name": item.get("name", ""),
                        "date": item.get("date") or target.get("date") or "",
                    })
            url = (payload.get("next") or "").replace(API_BASE, "")
            if not url:
                break
        return tags

    def branch_url(self, slug: str, branch: str) -> str:
        return f"https://bitbucket.org/{self.workspace}/{slug}/branch/{branch}"

    def find_pr(self, slug: str, source: str, destination: str) -> dict | None:
        """Localiza un PR abierto source → destination.

        Bitbucket Cloud ignora los params source_branch/destination_branch en
        GET /pullrequests: devuelve los PRs recientes sin filtrar por rama.
        Se filtra con la query `q` para evitar falsos positivos y además se
        valida que el `state` del PR sea `OPEN` (nunca MERGED/DECLINED/...).
        """
        q = 'source.branch.name="{}" AND destination.branch.name="{}"'.format(
            source.replace('"', '\\"'), destination.replace('"', '\\"')
        )
        data = self._request(
            "GET",
            f"/repositories/{self.workspace}/{slug}/pullrequests",
            params={
                "state": "OPEN",
                "q": q,
                "pagelen": 5,
            },
        )
        for pr in (data or {}).get("values", []):
            if pr.get("state", "").upper() != "OPEN":
                continue
            links = pr.get("links", {}) or {}
            html = (links.get("html") or {}).get("href", "")
            return {
                "id": pr.get("id"),
                "title": pr.get("title", ""),
                "url": html,
                "state": pr.get("state", ""),
                "source_commit": (pr.get("source") or {}).get("commit", {}).get("hash", ""),
            }
        return None

    def create_pr(
        self,
        slug: str,
        source: str,
        destination: str,
        title: str | None = None,
        description: str = "",
    ) -> dict:
        """Crea un PR source → destination. Requiere scope pullrequest:write."""
        resp = self._client.request(
            "POST",
            f"/repositories/{self.workspace}/{slug}/pullrequests",
            json={
                "title": title or f"Release: {source} → {destination}",
                "description": description,
                "source": {"branch": {"name": source}},
                "destination": {"branch": {"name": destination}},
                "close_source_branch": False,
            },
        )
        if resp.status_code in (401, 403):
            raise BitbucketAuthError(
                f"Bitbucket {resp.status_code}: sin permisos para crear PR "
                "(requiere scope pullrequest:write)"
            )
        if resp.status_code >= 400:
            raise BitbucketError(
                f"Bitbucket {resp.status_code} al crear PR: {resp.text[:300]}"
            )
        payload = resp.json()
        links = payload.get("links", {}) or {}
        return {
            "id": payload.get("id"),
            "title": payload.get("title", ""),
            "url": (links.get("html") or {}).get("href", ""),
            "state": payload.get("state", ""),
        }

    def update_pr_title(self, slug: str, pullrequest_id, title: str) -> dict:
        """Actualiza el título de un PR existente. Requiere scope pullrequest:write."""
        if not title:
            raise BitbucketError("Título de PR vacío")
        resp = self._client.request(
            "PUT",
            f"/repositories/{self.workspace}/{slug}/pullrequests/{pullrequest_id}",
            json={"title": title},
        )
        if resp.status_code in (401, 403):
            raise BitbucketAuthError(
                f"Bitbucket {resp.status_code}: sin permisos para editar PR "
                "(requiere scope pullrequest:write)"
            )
        if resp.status_code >= 400:
            raise BitbucketError(
                f"Bitbucket {resp.status_code} al actualizar PR: {resp.text[:300]}"
            )
        payload = resp.json()
        links = payload.get("links", {}) or {}
        return {
            "id": payload.get("id"),
            "title": payload.get("title", ""),
            "url": (links.get("html") or {}).get("href", ""),
            "state": payload.get("state", ""),
        }

    def tag_exists(self, slug: str, name: str) -> bool:
        """¿Existe el tag en el repo? (404 → False)."""
        return self._request("GET", f"/repositories/{self.workspace}/{slug}/refs/tags/{name}") is not None

    def create_tag(self, slug: str, name: str, commit: str) -> dict:
        """Crea un tag apuntando a un commit. Requiere scope repository:write."""
        resp = self._client.request(
            "POST",
            f"/repositories/{self.workspace}/{slug}/refs/tags",
            json={"name": name, "target": {"hash": commit}},
        )
        if resp.status_code in (401, 403):
            raise BitbucketAuthError(
                f"Bitbucket {resp.status_code}: sin permisos para crear tag "
                "(requiere scope repository:write)"
            )
        if resp.status_code >= 400:
            raise BitbucketError(
                f"Bitbucket {resp.status_code} al crear tag: {resp.text[:300]}"
            )
        payload = resp.json()
        return {"name": payload.get("name", name), "target": (payload.get("target") or {}).get("hash", "")}

    def upsert_file(
        self,
        slug: str,
        branch: str,
        path: str,
        content: str,
        message: str,
    ) -> dict:
        """Crea o actualiza un archivo en una rama (POST /src, multipart).

        Requiere scope repository:write. Devuelve {hash, subject} del commit.
        """
        resp = self._client.request(
            "POST",
            f"/repositories/{self.workspace}/{slug}/src",
            data={"message": message, "branch": branch},
            files={path: (path.split("/")[-1], content.encode("utf-8"), "text/plain")},
        )
        if resp.status_code in (401, 403):
            raise BitbucketAuthError(
                f"Bitbucket {resp.status_code}: sin permisos para escribir en la rama "
                "(requiere scope repository:write)"
            )
        if resp.status_code >= 400:
            raise BitbucketError(
                f"Bitbucket {resp.status_code} al commitear {path}: {resp.text[:300]}"
            )
        if not resp.text.strip() or "json" not in (resp.headers.get("content-type") or ""):
            return {"hash": "", "subject": message}
        payload = resp.json()
        return {
            "hash": payload.get("hash", ""),
            "subject": payload.get("subject", message),
        }