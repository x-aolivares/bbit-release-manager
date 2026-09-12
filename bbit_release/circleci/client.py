"""Cliente de la API v2 de CircleCI.

Busca pipelines (deploys programados) por commit y por prefijos de tag.
Requiere un token de usuario (Circle-Token) y el project slug de la forma
{vcs}/{org}/{repo} (p. ej. bb/my_org_web_dev/bbit-test-01).
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

import httpx

from ..http import get_global_rate_limiter, MAX_RETRIES, RETRY_BASE_DELAY

if TYPE_CHECKING:
    from ..cache import ReleaseCache

log = logging.getLogger("bbit.circleci")

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
    job: str = ""
    approval: str = ""


class CircleCiClient:
    def __init__(
        self,
        token: str,
        vcs: str = "bb",
        org: str = "",
        timeout: float = 15.0,
        transport: httpx.BaseTransport | None = None,
        recorder: Callable[[dict], None] | None = None,
        cache: ReleaseCache | None = None,
    ):
        if not token:
            raise ValueError("CIRCLECI_TOKEN es obligatorio")
        if not org:
            raise ValueError("org (CIRCLECI_ORG) es obligatorio")
        self.vcs = vcs
        self.org = org
        self._cache = cache
        self._pipeline_lock = threading.Lock()

        hooks: dict[str, list[Callable]] = {}
        if recorder is not None:
            hooks = {
                "request": [self._make_request_hook(recorder)],
                "response": [self._make_response_hook(recorder)],
            }

        self._client = httpx.Client(
            base_url=API_BASE,
            headers={"Circle-Token": token, "Accept": "application/json"},
            timeout=timeout,
            transport=transport,
            event_hooks=hooks,
        )

    def __enter__(self) -> "CircleCiClient":
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
                        "source": "circleci",
                        "method": response.request.method,
                        "url": str(url.path) + (f"?{url.query}" if url.query else ""),
                        "params": dict(url.params) if url.params else None,
                        "status": response.status_code,
                        "duration_ms": duration_ms,
                        "response": response.text,
                    }
                )
            except Exception:  # el hook nunca rompe el flujo del cliente
                log.exception("service_call recorder fallo en circleci")

        return hook

    def project_slug(self, repo: str) -> str:
        return f"{self.vcs}/{self.org}/{repo}"

    def project_id(self, repo: str) -> str | None:
        """UUID del proyecto en CircleCI (se usa en el link web filtrado por tag)."""
        slug = self.project_slug(repo)
        if self._cache is not None:
            cached = self._cache.get_circleci_project(slug)
            if cached is not None:
                return cached.get("id") or None
        payload = self._request("GET", f"/project/{slug}")
        if self._cache is not None:
            self._cache.set_circleci_project(slug, payload)
        return payload.get("id") or None

    def me(self) -> dict:
        return self._request("GET", "/me")

    def _request(self, method: str, path: str, params: dict | None = None):
        last_error: CircleCiError | None = None
        _rate_limiter = get_global_rate_limiter()
        for attempt in range(1, MAX_RETRIES + 1):
            _rate_limiter.acquire()
            try:
                resp = self._client.request(method, path, params=params)
            finally:
                _rate_limiter.release()
            if resp.status_code in (401, 403):
                raise CircleCiAuthError(
                    f"CircleCI {resp.status_code}: token inválido o sin permisos"
                )
            if resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After")
                if retry_after:
                    delay = float(retry_after)
                else:
                    delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
                last_error = CircleCiError(
                    f"CircleCI 429 en {path}: rate limit alcanzado"
                )
                if attempt < MAX_RETRIES:
                    log.warning(
                        "CircleCI 429 en %s (intento %d/%d): esperando %.1fs",
                        path, attempt, MAX_RETRIES, delay,
                    )
                    time.sleep(delay)
                    continue
                break
            if resp.status_code >= 400:
                raise CircleCiError(
                    f"CircleCI {resp.status_code} en {path}: {resp.text[:300]}"
                )
            return resp.json()
        raise last_error  # type: ignore[misc]

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

    def _paginate_capped(self, path: str, params: dict, max_pages: int = 1) -> list[dict]:
        """Como ``_paginate`` pero se detiene tras ``max_pages`` páginas.

        CircleCI devuelve ``next_page_token`` salvo que sea la última página
        real, sin relación con ``limit``: pedir ``limit=1`` o ``limit=100``
        solo cambia el tamaño de CADA página, no cuántas páginas se piden.
        Sin este corte, ``pipelines(tag=...)`` termina paginando el histórico
        completo del proyecto (100+ requests) igual que antes de BBIT-33
        Phase 10, sólo que en páginas más chicas.
        """
        items: list[dict] = []
        token: str | None = None
        pages = 0
        while True:
            page_params = dict(params)
            if token:
                page_params["page-token"] = token
            payload = self._request("GET", path, params=page_params)
            items.extend(payload.get("items", []))
            pages += 1
            token = payload.get("next_page_token")
            if not token or pages >= max_pages:
                break
        return items

    def pipelines(self, repo: str, branch: str | None = None, tag: str | None = None) -> list[dict]:
        """Pipelines del proyecto, opcionalmente filtrados por rama o tag.

        BBIT-33 Phase 10 (fix): ``limit`` en la query de CircleCI solo
        controla el tamaño de CADA página — no cuántas páginas se piden.
        El corte real de "no traer el histórico completo" es ``max_pages``
        vía ``_paginate_capped``: una sola página (``limit`` pipelines) y
        listo, sin seguir ``next_page_token``.
        """
        params: dict = {"limit": 20}
        key = ""
        kind = ""
        if branch:
            params["branch"] = branch
            key, kind = branch, "branch"
        elif tag:
            key, kind = tag, "tag"
        
        if self._cache is not None and key:
            cached = self._cache.get_circleci_pipelines(self.project_slug(repo), key, kind)
            if cached is not None:
                return cached
        
        if tag:
            # Para tags: una sola página de las pipelines más recientes del
            # proyecto (no hay filtro server-side por tag), filtrada
            # client-side. Si el tag buscado no está en esa página reciente
            # no se encuentra — trade-off aceptado a cambio de no paginar
            # el histórico completo del proyecto por cada tag.
            project_pipelines = self._paginate_capped(
                f"/project/{self.project_slug(repo)}/pipeline", {"limit": 20}, max_pages=1,
            )
            items = [p for p in project_pipelines
                     if (p.get("vcs") or {}).get("tag") == tag]
        else:
            # Para branches: CircleCI filtra server-side por branch, así que
            # una sola página alcanza (la última pipeline de esa rama).
            items = self._paginate_capped(
                f"/project/{self.project_slug(repo)}/pipeline", params, max_pages=1,
            )
        
        if self._cache is not None and key:
            self._cache.set_circleci_pipelines(self.project_slug(repo), key, kind, items)
        return items

    def workflows(self, pipeline_id: str) -> list[dict]:
        if self._cache is not None:
            cached = self._cache.get_circleci_workflows(pipeline_id)
            if cached is not None:
                return cached
        items = self._paginate(f"/pipeline/{pipeline_id}/workflow", {})
        if self._cache is not None:
            self._cache.set_circleci_workflows(pipeline_id, items)
        return items

    def workflow_jobs(self, workflow_id: str) -> list[dict]:
        """Jobs de un workflow (para el deep-link al job del deploy)."""
        if self._cache is not None:
            cached = self._cache.get_circleci_workflow_jobs(workflow_id)
            if cached is not None:
                return cached
        items = self._paginate(f"/workflow/{workflow_id}/job", {})
        if self._cache is not None:
            self._cache.set_circleci_workflow_jobs(workflow_id, items)
        return items

    def _pick_deploy_job(self, jobs: list[dict], prefix: str) -> dict | None:
        """Job de deploy real: el que contenga el env (prefix) o el último no-approval."""
        deployables = [j for j in jobs if j.get("type") != "approval"]
        if not deployables:
            return None
        if prefix:
            for job in deployables:
                if prefix.lower() in (job.get("name") or "").lower():
                    return job
        return deployables[-1]

    @staticmethod
    def _deploy_status(approval: dict | None, deploy_job: dict | None) -> str:
        """Estado del deploy mirando el job real (y el gate de aprobación)."""
        if approval:
            astatus = approval.get("status") or ""
            if astatus == "canceled":
                return "canceled"
            if astatus not in ("success",):
                return "on_hold"
        return (deploy_job or {}).get("status") or ""

    def _deploy_from_workflow(self, repo: str, pipeline: dict, workflow: dict, prefix: str = "") -> DeployJob:
        slug = self.project_slug(repo)
        number = (pipeline.get("number") or 0)
        wf_id = workflow.get("id", "")
        url = f"{WEB_BASE}/{slug}/{number}/workflows/{wf_id}"
        try:
            jobs = self.workflow_jobs(wf_id)
        except CircleCiError:
            jobs = []
        approval = next((j for j in jobs if j.get("type") == "approval"), None)
        deploy_job = self._pick_deploy_job(jobs, prefix)
        status = self._deploy_status(approval, deploy_job)
        # Deep-link: al gate de aprobación mientras esté pendiente; si ya se
        # aprobó (o no hay gate), al job de deploy real.
        link = None
        if approval and approval.get("id") and approval.get("status") != "success":
            link = approval
        if link is None:
            link = deploy_job
        if link and link.get("id"):
            job_type = link.get("type") or "build"
            build_number = link.get("number") or link.get("job_number") or ""
            url = (
                f"{WEB_BASE}/{slug}/{number}/details?useNewPipelines=true"
                f"&job={link['id']}&workflowId={wf_id}"
                f"&buildNumber={build_number}&jobType={job_type}"
            )
        return DeployJob(
            workflow=workflow.get("name", ""),
            pipeline_id=pipeline.get("id", ""),
            pipeline_number=number,
            status=status,
            created_at=workflow.get("created_at", ""),
            url=url,
            job=deploy_job.get("name", "") if deploy_job else "",
            approval=approval.get("status", "") if approval else "",
        )

    def pipeline_id_for_commit(self, repo: str, branch: str, commit: str) -> int | None:
        """Número de pipeline más reciente que vcs.revision == commit en la rama."""
        if not commit:
            return None
        best = None
        for pipeline in self.pipelines(repo, branch=branch):
            revision = (pipeline.get("vcs") or {}).get("revision", "")
            if revision != commit:
                continue
            number = pipeline.get("number") or 0
            if best is None or number > best:
                best = number
        return best

    def deploy_for_tag(
        self,
        repo: str,
        tag: str,
        commit: str,
        prefix: str,
    ) -> DeployJob | None:
        """Deploy del tag: pipeline corrido sobre ese tag con revision == commit."""
        if not tag or not commit:
            log.warning("deploy_for_tag: %s tag=%r commit=%r -> tag o commit vacío", repo, tag, commit)
            return None
        pipelines = self.pipelines(repo, tag=tag)
        if not pipelines:
            log.warning("deploy_for_tag: %s tag=%s commit=%s -> sin pipelines", repo, tag, commit)
        else:
            log.info(
                "deploy_for_tag: %s tag=%s commit=%s -> %d pipelines, buscando rev match",
                repo, tag, commit[:12], len(pipelines),
            )
        for pipeline in pipelines:
            rev = (pipeline.get("vcs") or {}).get("revision", "")
            log.info(
                "deploy_for_tag: %s pipeline#%s vcs.revision=%s vs commit=%s match=%s",
                repo, pipeline.get("number"), rev[:12], commit[:12], rev == commit,
            )
            if rev != commit:
                log.info(
                    "deploy_for_tag: %s tag=%s descarta pipeline vcs.revision=%s (commit=%s)",
                    repo, tag, rev[:12], commit[:12],
                )
                continue
            job = self.deploy_job_for_pipeline(repo, pipeline, prefix)
            log.info(
                "deploy_for_tag: %s tag=%s commit=%s prefix=%s -> workflow=%r status=%r",
                repo, tag, commit[:12], prefix,
                job.workflow if job else None,
                job.status if job else None,
            )
            return job
        log.warning(
            "deploy_for_tag: %s tag=%s commit=%s -> %d pipelines revisados, ninguno con rev match",
            repo, tag, commit[:12], len(pipelines),
        )
        return None

    def deploy_job_for_pipeline(self, repo: str, pipeline: dict, prefix: str) -> DeployJob | None:
        """Primer workflow del pipeline cuyo nombre contiene el prefijo."""
        workflows = self.workflows(pipeline.get("id", ""))
        for workflow in workflows:
            name = workflow.get("name", "")
            if prefix and prefix.lower() in name.lower():
                return self._deploy_from_workflow(repo, pipeline, workflow, prefix)
        log.warning(
            "deploy_job_for_pipeline: %s pipeline=%s prefix=%s sin workflow que contenga %r (workflows=%s)",
            repo, pipeline.get("id"), prefix, prefix,
            [w.get("name") for w in workflows],
        )
        return None

    def deploys_for_envs(
        self,
        repo: str,
        tags: list[str],
        commit: str,
        prefixes: list[str],
    ) -> dict[str, dict]:
        """Deploys por tag (default) y por env en UNA pasada de fetch por tag.

        BBIT-34: reemplaza el par ``deploys_for_tags`` + loop por-env
        ``deploy_for_tag`` para los scans. Cada tag resuelve pipelines,
        workflows y jobs UNA sola vez; el resultado combina:
        - ``tags``: {tag: DeployJob | None}  -> primer pipeline con workflow
           nombrado (mismo criterio que ``deploys_for_tags``).
        - ``envs_by_tag``: {tag: {prefix: DeployJob | None}} -> pipeline con
           vcs.revision == commit cuyo workflow contiene el prefijo (mismo
           criterio que ``deploy_for_tag`` por env).
        """
        result: dict[str, dict] = {"tags": {t: None for t in tags}, "envs_by_tag": {}}
        if not tags:
            return result

        def _resolve(tag: str) -> tuple[str, DeployJob | None, dict[str, DeployJob | None]]:
            pipelines = self.pipelines(repo, tag=tag)
            default: DeployJob | None = None
            if pipelines:
                for workflow in self.workflows(pipelines[0].get("id", "")):
                    if workflow.get("name", ""):
                        default = self._deploy_from_workflow(repo, pipelines[0], workflow)
                        break
            envs: dict[str, DeployJob | None] = {}
            for pipeline in pipelines:
                rev = (pipeline.get("vcs") or {}).get("revision", "")
                if rev != commit:
                    continue
                for workflow in self.workflows(pipeline.get("id", "")):
                    name = (workflow.get("name") or "").lower()
                    match = next((p for p in prefixes if p.lower() in name), None)
                    if match is not None and match not in envs:
                        envs[match] = self._deploy_from_workflow(repo, pipeline, workflow, match)
                break
            return tag, default, envs

        with ThreadPoolExecutor(max_workers=min(len(tags), 4)) as ex:
            resolved = list(ex.map(_resolve, tags))
        for tag, default, envs in resolved:
            result["tags"][tag] = default
            result["envs_by_tag"][tag] = envs
        return result

    def deploys_for_tags(self, repo: str, tags: list[str]) -> dict[str, DeployJob | None]:
        """Para cada tag, el último pipeline corrido sobre ese tag.

        Los pipelines de tags distintos se resuelven en paralelo (acotado a
        pocos workers); ``ex.map`` preserva el orden de ``tags`` en el
        resultado. El lock de ``_project_pipelines`` evita paginar dos veces
        el mismo proyecto en caché fría.
        """
        result: dict[str, DeployJob | None] = {t: None for t in tags}
        if not tags:
            return result

        def _resolve(tag: str) -> DeployJob | None:
            pipelines = self.pipelines(repo, tag=tag)
            if not pipelines:
                return None
            pipeline = pipelines[0]
            for workflow in self.workflows(pipeline.get("id", "")):
                if workflow.get("name", ""):
                    return self._deploy_from_workflow(repo, pipeline, workflow)
            return None

        with ThreadPoolExecutor(max_workers=min(len(tags), 4)) as ex:
            resolved = list(ex.map(_resolve, tags))
        for tag, deploy in zip(tags, resolved):
            if deploy is not None:
                result[tag] = deploy
        return result