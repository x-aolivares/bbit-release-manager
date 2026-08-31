"""Cliente de la API REST de Bitbucket Cloud (api.bitbucket.org/2.0).

Autenticación vía Bearer token (API token de Atlassian).
Operaciones de solo lectura para enumeración de repos, resolución de ramas
y obtención de diffs.
"""

from __future__ import annotations

import httpx
from dataclasses import dataclass

API_BASE = "https://api.bitbucket.org/2.0"


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


@dataclass(frozen=True)
class DiffFile:
    path: str
    status: str
    lines_added: int = 0
    lines_removed: int = 0
    added_lines: tuple[str, ...] = ()


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
    ):
        if not workspace:
            raise ValueError("workspace es obligatorio")
        if not token:
            raise ValueError("token es obligatorio")
        self.workspace = workspace
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        self._client = httpx.Client(
            base_url=API_BASE,
            headers=headers,
            timeout=timeout,
            transport=transport,
        )

    def __enter__(self) -> "BitbucketClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def _request(self, method: str, path: str, params: dict | None = None):
        resp = self._client.request(method, path, params=params)
        if resp.status_code in (401, 403):
            raise BitbucketAuthError(
                f"Bitbucket {resp.status_code}: token inválido o sin permisos"
            )
        if resp.status_code == 404:
            return None
        if resp.status_code >= 400:
            raise BitbucketError(
                f"Bitbucket {resp.status_code} en {path}: {resp.text[:300]}"
            )
        return resp.json()

    def session(self) -> tuple[WorkspaceInfo, str, list[Repository]]:
        """Valida token, describe workspace y lista repos."""
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
        repos = self.list_repos()
        return info, identity, repos

    def list_repos(self, filter_names: list[str] | None = None) -> list[Repository]:
        allowed = {n.lower() for n in (filter_names or [])}
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
                if allowed and slug.lower() not in allowed:
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
        return repos

    def has_branch(self, slug: str, branch: str) -> bool:
        resp = self._request("GET", f"/repositories/{self.workspace}/{slug}/refs/branches/{branch}")
        return resp is not None

    def default_branch(self, slug: str) -> str:
        data = self._request(
            "GET",
            f"/repositories/{self.workspace}/{slug}/refs/branches",
            params={"sort": "-target.date", "pagelen": 1},
        )
        if data and data.get("values"):
            return data["values"][0].get("name", "master")
        return "master"

    def repos_with_branch(self, branch: str) -> list[Repository]:
        repos = self.list_repos()
        result: list[Repository] = []
        for repo in repos:
            if self.has_branch(repo.slug, branch):
                result.append(repo)
        return result

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
        in_hunk = False
        for line in text.splitlines():
            if line.startswith("diff --git "):
                if path:
                    files.append(DiffFile(path, status, added, removed, tuple(added_lines)))
                path = line.split(" b/", 1)[-1].split(" a/", 1)[-1]
                status = "modified"
                added = 0
                removed = 0
                added_lines = []
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
        if path:
            files.append(DiffFile(path, status, added, removed, tuple(added_lines)))
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

    def commit_for_branch(self, slug: str, branch: str) -> str:
        """Último commit de una rama (hash completo)."""
        data = self._request(
            "GET",
            f"/repositories/{self.workspace}/{slug}/commits/{branch}",
            params={"pagelen": 1},
        )
        if not data or not data.get("values"):
            return ""
        return data["values"][0].get("hash", "")

    def commits_behind(self, slug: str, branch: str, base: str) -> int:
        """Cantidad de commits en base que la rama no tiene (gap de sync).

        GET /commits/{base}?exclude={branch} → commits de base ausentes en branch.
        """
        try:
            data = self._request(
                "GET",
                f"/repositories/{self.workspace}/{slug}/commits/{base}",
                params={"exclude": branch, "pagelen": 100},
            )
        except (BitbucketError, BitbucketAuthError):
            return 0
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
        Se filtra con la query `q` para evitar falsos positivos.
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