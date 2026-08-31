"""Cliente de la API v2 de CircleCI.

Busca pipelines (deploys programados) por commit y por prefijos de tag.
Requiere un token de usuario (Circle-Token) y el project slug de la forma
{vcs}/{org}/{repo} (p. ej. bb/my_org_web_dev/bbit-test-01).
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

API_BASE = "https://circleci.com/api/v2"
WEB_BASE = "https://app.circleci.com/pipelines"


class CircleCiError(Exception):
    """Error genérico de la API de CircleCI."""


class CircleCiAuthError(CircleCiError):
    """Token inválido o sin permisos."""


@dataclass(frozen=True)
class DeployJob:
    workflow: str
    pipeline_id: str
    pipeline_number: int
    status: str
    created_at: str
    url: str


class CircleCiClient:
    def __init__(
        self,
        token: str,
        vcs: str = "bb",
        org: str = "",
        timeout: float = 15.0,
        transport: httpx.BaseTransport | None = None,
    ):
        if not token:
            raise ValueError("CIRCLECI_TOKEN es obligatorio")
        if not org:
            raise ValueError("org (CIRCLECI_ORG) es obligatorio")
        self.vcs = vcs
        self.org = org
        self._client = httpx.Client(
            base_url=API_BASE,
            headers={"Circle-Token": token, "Accept": "application/json"},
            timeout=timeout,
            transport=transport,
        )

    def __enter__(self) -> "CircleCiClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def project_slug(self, repo: str) -> str:
        return f"{self.vcs}/{self.org}/{repo}"

    def me(self) -> dict:
        return self._request("GET", "/me")

    def _request(self, method: str, path: str, params: dict | None = None):
        resp = self._client.request(method, path, params=params)
        if resp.status_code in (401, 403):
            raise CircleCiAuthError(
                f"CircleCI {resp.status_code}: token inválido o sin permisos"
            )
        if resp.status_code >= 400:
            raise CircleCiError(
                f"CircleCI {resp.status_code} en {path}: {resp.text[:300]}"
            )
        return resp.json()

    def _paginate(self, path: str, params: dict) -> list[dict]:
        items: list[dict] = []
        token: str | None = None
        while True:
            page_params = dict(params)
            if token:
                page_params["page-token"] = token
            payload = self._request("GET", path, params=page_params)
            items.extend(payload.get("items", []))
            token = payload.get("next_page_token")
            if not token:
                break
        return items

    def pipelines(self, repo: str, branch: str | None = None, tag: str | None = None) -> list[dict]:
        """Pipelines del proyecto, opcionalmente filtrados por rama o tag."""
        params: dict = {"limit": 100}
        if branch:
            params["branch"] = branch
        elif tag:
            params["branch"] = tag
        items = self._paginate(f"/project/{self.project_slug(repo)}/pipeline", params)
        if tag:
            items = [p for p in items if (p.get("vcs") or {}).get("tag") == tag]
        return items

    def workflows(self, pipeline_id: str) -> list[dict]:
        return self._paginate(f"/pipeline/{pipeline_id}/workflow", {})

    def _deploy_from_workflow(self, repo: str, pipeline: dict, workflow: dict) -> DeployJob:
        slug = self.project_slug(repo)
        number = (pipeline.get("number") or 0)
        wf_id = workflow.get("id", "")
        return DeployJob(
            workflow=workflow.get("name", ""),
            pipeline_id=pipeline.get("id", ""),
            pipeline_number=number,
            status=workflow.get("status", ""),
            created_at=workflow.get("created_at", ""),
            url=f"{WEB_BASE}/{slug}/{number}/workflows/{wf_id}",
        )

    def scheduled_deploys_for_commit(
        self,
        repo: str,
        branch: str,
        commit: str,
        prefixes: list[str],
    ) -> dict[str, DeployJob | None]:
        """Mapea cada prefijo a su deploy programado para el commit dado.

        Busca pipelines de trigger 'schedule' cuyo vcs.revision coincide con el
        commit y cuyo workflow matchea el prefijo (substring case-insensitive).
        """
        result: dict[str, DeployJob | None] = {p: None for p in prefixes}
        if not commit:
            return result
        pipelines = self.pipelines(repo, branch=branch)
        matched: dict[str, DeployJob] = {}
        for pipeline in pipelines:
            trigger = (pipeline.get("trigger") or {}).get("type", "")
            revision = (pipeline.get("vcs") or {}).get("revision", "")
            if trigger != "schedule" or revision != commit:
                continue
            for workflow in self.workflows(pipeline.get("id", "")):
                name = workflow.get("name", "")
                for prefix in prefixes:
                    if prefix and prefix.lower() in name.lower():
                        existing = matched.get(prefix)
                        if existing is None:
                            matched[prefix] = self._deploy_from_workflow(repo, pipeline, workflow)
        for prefix, deploy in matched.items():
            result[prefix] = deploy
        return result

    def deploys_for_tags(self, repo: str, tags: list[str]) -> dict[str, DeployJob | None]:
        """Para cada tag, el último pipeline corrido sobre ese tag."""
        result: dict[str, DeployJob | None] = {t: None for t in tags}
        if not tags:
            return result
        for tag in tags:
            pipelines = self.pipelines(repo, tag=tag)
            if not pipelines:
                continue
            pipeline = pipelines[0]
            for workflow in self.workflows(pipeline.get("id", "")):
                if workflow.get("name", ""):
                    result[tag] = self._deploy_from_workflow(repo, pipeline, workflow)
                    break
        return result