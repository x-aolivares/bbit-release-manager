from concurrent.futures import ThreadPoolExecutor
import logging
import re
import time

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from ...bitbucket import client as bb
from ...cache import get_cache
from ...config import Config
from ...circleci.client import CircleCiClient, CircleCiError
from ...circleci.configyml import ensure_tag_workflows
from ...scan.params import classify_ssm, extract_ssm_params
from ..session import (
    create_session,
    get_session,
    destroy_session,
    active_session_id,
    _recorder,
)

log = logging.getLogger("bbit.scan")

router = APIRouter(prefix="/api", tags=["repos"])

MAX_WORKERS = 4
_SUBMIT_DELAY = 0.1


def _project_prefixes(cfg, raw: str = "") -> list[str] | None:
    """Prefijos de proyecto desde query param; si no, del config.

    Vacío/ausente → None (no filtra: todos los repos del workspace).
    """
    vals = [p.strip().lower() for p in raw.split(",") if p.strip()]
    if vals:
        return vals
    default = getattr(cfg, "project_prefixes", None) or []
    return default or None


def _exclude_repos(cfg, raw: str = "") -> set[str]:
    """Slugs excluidos desde query param; si no, del config (blacklist).

    Vacío/ausente → set vacío (no excluye).
    """
    vals = {s.strip().lower() for s in raw.split(",") if s.strip()}
    if vals:
        return vals
    default = getattr(cfg, "exclude_repos", None) or []
    return {s.lower() for s in default}


def _apply_filters(repos, prefs: list[str] | None, blocked: set[str]) -> list:
    """Filtra repos por prefijos de slug y exclusiones de blacklist."""
    out = []
    for r in repos:
        slug = r.slug.lower()
        if slug in blocked:
            continue
        if prefs and not any(slug.startswith(p) for p in prefs):
            continue
        out.append(r)
    return out


def _all_repos_cached(client, prefs: list[str] | None, exclude: set[str]) -> list | None:
    """Todos los repos del workspace (lista completa), con caché SQLite.

    Key por prefijos/exclusión (sin origin → todo el workspace). Comparte la
    misma cache que /api/repos sin origin, así el barrido paginado de
    `list_repos` no se repite entre endpoints.

    Si el cliente no expone `list_repos` (stubs de test), devuelve None y el
    llamador cae en `repos_with_branch` sin base.
    """
    if not hasattr(client, "list_repos"):
        return None
    cache = get_cache()
    cached = cache.get_repos("", "all", prefs, exclude)
    if cached is not None:
        from types import SimpleNamespace
        return [SimpleNamespace(**r) for r in cached]
    repos = client.list_repos(prefixes=prefs)
    items = [{"slug": r.slug, "name": r.name, "workspace": r.workspace, "default_branch": r.default_branch} for r in repos]
    cache.set_repos("", "all", prefs, exclude, items)
    return repos


def _branch_repos_cached(client, origin: str, destination: str, prefs: list[str] | None, exclude: set[str]) -> list:
    """Repos con la rama, con caché SQLite (tabla branch_repos).

    En el primer llamado usa la lista completa cacheada como base para evitar
    re-barrer el workspace (el `list_repos` paginado). Si la lista completa
    tampoco está disponible/cacheada, cae en `repos_with_branch` que la obtiene.
    """
    cache = get_cache()
    cached = cache.get_branch_repos(origin, destination, prefs, exclude)
    if cached is not None:
        from types import SimpleNamespace
        return [SimpleNamespace(**{**r, "slug": r["repo_name"]}) for r in cached]
    base = _all_repos_cached(client, prefs, exclude)
    if base is not None:
        repos = client.repos_with_branch(origin, prefixes=prefs, repos=base)
    else:
        repos = client.repos_with_branch(origin, prefixes=prefs)
    items = [{"repo_name": r.slug, "name": r.name, "workspace": r.workspace, "default_branch": r.default_branch} for r in repos]
    cache.set_branch_repos(origin, destination, prefs, exclude, items)
    return repos


def _suggests_ssm(file, prefixes) -> bool:
    """¿El archivo puede aportar un parámetro SSM (añadido o quitado)? Evita
    raw_files inútiles en modo diff."""
    lines = list(file.added_lines) + list(file.removed_lines or ())
    return bool(extract_ssm_params(lines, prefixes))


NON_TEXT_EXT = {
    ".png", ".jpg", ".jpeg", ".gif", ".pdf", ".woff", ".woff2", ".ttf",
    ".eot", ".ico", ".zip", ".gz", ".tar", ".jar", ".class", ".pyc",
    ".lock", ".svg", ".map", ".bin", ".dat",
}


def _read_files_params(client, slug: str, ref: str, files: list[str], prefixes) -> set:
    """Parámetros SSM en los raw de un ref para archivos de tipo texto."""
    def _one(path: str):
        name = path.rsplit("/", 1)[-1]
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if ext and f".{ext}" in NON_TEXT_EXT:
            return set()
        raw = client.raw_file(slug, ref, path)
        if raw is None:
            return set()
        return set(extract_ssm_params([raw], prefixes))

    out: set = set()
    if not files:
        return out
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(files) or 1)) as ex:
        for result in ex.map(_one, files):
            out |= result
    return out


def _require_session():
    sid = active_session_id()
    if not sid:
        raise HTTPException(401, "No hay sesión activa. Conectá desde la web.")
    data = get_session(sid)
    if not data:
        raise HTTPException(401, "Sesión inválida.")
    return data


def _circleci() -> CircleCiClient | None:
    cfg = Config()
    if not cfg.circleci_token:
        return None
    return CircleCiClient(
        cfg.circleci_token,
        vcs=cfg.circleci_vcs,
        org=cfg.circleci_org or cfg.workspace,
        recorder=_recorder,
    )


@router.get("/session")
def session_status():
    sid = active_session_id()
    data = get_session(sid) if sid else None
    if data:
        return {
            "active": True,
            "identity": data.identity,
            "workspace": data.workspace,
            "repo_count": data.repo_count,
            "stored": bool(Config().bitbucket_token),
        }
    cfg = Config()
    if cfg.bitbucket_token and cfg.workspace:
        return {"active": False, "stored": True, "needs_tokens": False}
    return {"active": False, "stored": False, "needs_tokens": True}


@router.post("/session/reuse")
def session_reuse():
    if active_session_id():
        return session_status()
    cfg = Config()
    tok = cfg.bitbucket_token
    ws = cfg.workspace
    if not (tok and ws):
        return JSONResponse({"ok": False, "error": "No hay credenciales guardadas."}, status_code=400)
    try:
        data = create_session(ws, tok)
    except (bb.BitbucketAuthError, bb.BitbucketError):
        return JSONResponse(
            {"ok": False, "error": "Las credenciales guardadas vencieron o ya no son válidas. Generá de nuevo."},
            status_code=401,
        )
    return {
        "ok": True,
        "active": True,
        "identity": data.identity,
        "workspace": data.workspace,
        "repo_count": data.repo_count,
        "stored": True,
    }


@router.post("/session")
def api_session(body: dict):
    workspace = (body.get("workspace") or "").strip()
    token = (body.get("token") or "").strip()
    circleci_token = (body.get("circleci_token") or "").strip()
    if not workspace or not token:
        raise HTTPException(400, "workspace y token son obligatorios")

    cfg = Config()
    if circleci_token:
        ci = CircleCiClient(
            circleci_token,
            vcs=cfg.circleci_vcs or "bb",
            org=cfg.circleci_org or workspace,
            recorder=_recorder,
        )
        try:
            ci.me()
        except (CircleCiError, ValueError) as exc:
            return JSONResponse(
                {"ok": False, "error": f"Token de CircleCI inválido: {exc}"},
                status_code=401,
            )
        finally:
            ci.close()

    try:
        data = create_session(workspace, token)
    except (bb.BitbucketAuthError, bb.BitbucketError) as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=401)

    try:
        cfg.save_tokens(bitbucket_token=token, circleci_token=circleci_token, workspace=workspace)
        cfg.save_filters(
            project_prefixes=",".join(_project_prefixes(cfg, body.get("project_prefixes", "")) or []),
            exclude_repos=",".join(sorted(_exclude_repos(cfg, body.get("exclude_repos", "")))),
        )
    except OSError as exc:
        return JSONResponse(
            {"ok": False, "error": f"Sesión OK pero no se pudo guardar el token en env.base: {exc}"},
            status_code=500,
        )

    return {
        "ok": True,
        "session_id": data.session_id,
        "identity": data.identity,
        "workspace": data.workspace,
        "repo_count": data.repo_count,
        "stored": True,
    }


@router.delete("/cache")
def clear_cache(body: dict | None = None):
    """Vacía registros del cache.

    Retrocompatible: sin body limpia TODO (request/init_sesion/service_call).
    Con ``{"sessions": [{origin, destination, project_prefixes, exclude}, ...]}``
    elimina solo las sesiones indicadas (y sus requests). Devuelve el desglose.
    """
    sessions = (body or {}).get("sessions") or []
    if not sessions:
        try:
            counts = get_cache().clear_all()
        except Exception as exc:
            return JSONResponse(
                {"ok": False, "error": f"No se pudo limpiar el cache: {exc}"},
                status_code=500,
            )
        return {"ok": True, "cleared": counts}

    cache = get_cache()
    total: dict[str, int] = {"sessions": 0, "requests": 0}
    try:
        for s in sessions:
            origin = (s.get("origin") or "").strip()
            destination = (s.get("destination") or "master").strip()
            if not origin:
                continue
            before_s = cache._fetchone("SELECT COUNT(*) FROM init_sesion", ())[0]
            before_r = cache._fetchone("SELECT COUNT(*) FROM request", ())[0]
            cache.invalidate(
                origin, destination,
                {
                    "repositories": {
                        "excluded": sorted(s.get("exclude") or []),
                        "prefixes": sorted(s.get("project_prefixes") or []),
                    }
                },
            )
            total["sessions"] += before_s - cache._fetchone("SELECT COUNT(*) FROM init_sesion", ())[0]
            total["requests"] += before_r - cache._fetchone("SELECT COUNT(*) FROM request", ())[0]
    except Exception as exc:
        return JSONResponse(
            {"ok": False, "error": f"No se pudo limpiar las sesiones: {exc}"},
            status_code=500,
        )
    return {"ok": True, "cleared": total}


@router.delete("/session")
def destroy(delete_credentials: bool = False):
    sid = active_session_id()
    if sid:
        destroy_session(sid)
    deleted = False
    try:
        Config().clear_filters()
    except OSError as exc:
        return JSONResponse(
            {"ok": False, "error": f"No se pudieron limpiar los filtros: {exc}"},
            status_code=500,
        )
    if delete_credentials:
        try:
            Config().remove_credentials()
            deleted = True
        except OSError as exc:
            return JSONResponse(
                {"ok": False, "error": f"No se pudieron eliminar las credenciales: {exc}"},
                status_code=500,
            )
    return {"ok": True, "delete_credentials": deleted}


@router.get("/repos")
def list_repos(origin: str = "", project_prefixes: str = "", force: int = 0, exclude: str = ""):
    sid = active_session_id()
    if not sid:
        return {"items": [], "configured": False, "error": "No hay sesión activa. Conectá desde la web."}
    data = get_session(sid)
    if not data:
        return {"items": [], "configured": False, "error": "Sesión inválida."}
    prefs = _project_prefixes(Config(), project_prefixes)
    blocked = _exclude_repos(Config(), exclude)
    cache = get_cache()

    if not force:
        cached = cache.get_repos(origin, "all", prefs, blocked)
        if cached is not None:
            repos = [r for r in cached if r["slug"] not in blocked]
            return {
                "items": repos,
                "configured": True, "error": None, "cached": True,
            }

    if origin:
        base = _all_repos_cached(data.client, prefs, blocked)
        if base is not None:
            repos = data.client.repos_with_branch(origin, prefixes=prefs, repos=base)
        else:
            repos = data.client.repos_with_branch(origin, prefixes=prefs)
    else:
        repos = _all_repos_cached(data.client, prefs, blocked) or data.client.list_repos(prefixes=prefs)
    items = [
        {"slug": r.slug, "name": r.name, "workspace": r.workspace, "default_branch": r.default_branch}
        for r in repos
    ]
    cache.set_repos(origin, "all", prefs, blocked, items)
    repos = [r for r in repos if r.slug.lower() not in blocked]
    return {
        "items": [
            {"slug": r.slug, "name": r.name, "workspace": r.workspace, "default_branch": r.default_branch}
            for r in repos
        ],
        "configured": True,
        "error": None,
        "cached": False,
    }


def _serialize_pr(pr: dict | None) -> dict:
    if pr:
        return {"exists": True, "url": pr.get("url", ""), "title": pr.get("title", ""), "state": pr.get("state", "")}
    return {"exists": False}


def _serialize_deploy(deploy) -> dict | None:
    if deploy is None:
        return None
    return {
        "workflow": deploy.workflow,
        "status": deploy.status,
        "created_at": deploy.created_at,
        "url": deploy.url,
    }


def _tag_match(prefix: str) -> re.Pattern:
    return re.compile(rf"^{re.escape(prefix)}-(\d+)$", re.IGNORECASE)


def _repo_scan(client, ci, repo, origin, destination, clean):
    """Payload de un repo (etapa paralela de /scan).

    Reusa el head de la rama origen desde el PR cuando existe
    (source_commit), evitando GET /commits/{branch}.

    La columna de cada ambiente sale del tag `{env}-{pipeline_id}` del commit:
    el deploy es válido solo si el pipeline del tag tiene ese número, la
    revisión del commit y un workflow que mencione el ambiente.
    """
    try:
        pr = client.find_pr(repo.slug, origin, destination)
    except bb.BitbucketError:
        pr = None

    if pr and pr.get("source_commit"):
        commit = pr["source_commit"]
    else:
        commit = client.commit_for_branch(repo.slug, origin)
    if not pr:
        no_changes = not client.has_commits_ahead(repo.slug, origin, destination)
    else:
        no_changes = False
    behind = client.commits_behind(repo.slug, origin, destination)
    match_commit = commit
    if ci is not None and pr and pr.get("source_commit"):
        branch_head = client.commit_for_branch(repo.slug, origin)
        log.info(
            "scan: %s pr.source_commit=%s branch_head=%s match_commit=%s",
            repo.slug,
            commit[:12],
            (branch_head or "?")[:12],
            (branch_head or commit)[:12],
        )
        match_commit = branch_head or commit
    tags = client.tags_on_commit(repo.slug, match_commit)

    ci_error = None
    ci_project = None
    tag_rows = []
    if ci is not None:
        try:
            tag_deploys = ci.deploys_for_tags(repo.slug, [t["name"] for t in tags])
            ci_project = ci.project_id(repo.slug)
        except CircleCiError as exc:
            tag_deploys = {}
            ci_error = str(exc)
        for t in tags:
            tag_rows.append({"name": t["name"], "deploy": _serialize_deploy(tag_deploys.get(t["name"]))})
    else:
        tag_rows = [{"name": t["name"], "deploy": None} for t in tags]

    deploys: dict[str, dict | None] = {}
    match_tag: dict[str, str | None] = {}
    for prefix in clean:
        env = prefix.lower()
        found_tag = None
        for t in tags:
            if _tag_match(env).match(t["name"]):
                found_tag = t["name"]
                break
        match_tag[env] = found_tag
        deploy = None
        if ci is not None and found_tag and match_commit:
            log.info(
                "scan: %s env=%s tag=%s commit=%s -> buscando deploy_for_tag",
                repo.slug, env, found_tag, match_commit[:12],
            )
            try:
                deploy = ci.deploy_for_tag(repo.slug, found_tag, match_commit, env)
            except CircleCiError as exc:
                ci_error = ci_error or str(exc)
            log.info(
                "scan: %s env=%s deploy=%s",
                repo.slug, env,
                f"{deploy.status}" if deploy else "None",
            )
        elif ci is not None and not found_tag:
            log.info("scan: %s env=%s sin tag %s-en en commit %s", repo.slug, env, env, match_commit[:12])
        deploys[env] = _serialize_deploy(deploy)

    item = {
        "slug": repo.slug,
        "name": repo.name,
        "workspace": repo.workspace,
        "branch_url": client.branch_url(repo.slug, origin),
        "commit": commit,
        "behind": behind,
        "no_changes": no_changes,
        "tags": tag_rows,
        "pr": _serialize_pr(pr),
        "deploys": deploys,
        "match_tag": match_tag,
        "ci_project": ci_project,
        "ci_vcs": ci.vcs if ci else None,
    }
    return item, ci_error


@router.get("/scan")
def scan(origin: str, destination: str = "master", prefixes: str = "", project_prefixes: str = "", exclude: str = "", force: int = 0):
    data = _require_session()
    cfg = Config()
    clean = [p.strip() for p in prefixes.split(",") if p.strip()] or cfg.deploy_prefixes
    proj = _project_prefixes(cfg, project_prefixes)
    blocked = _exclude_repos(cfg, exclude)
    cache = get_cache()

    if force:
        cache.invalidate(origin, destination, {"repositories": {"excluded": sorted(blocked), "prefixes": sorted(proj or [])}})
    elif cache.get_scan(origin, destination, proj, blocked, clean) is not None:
        return cache.get_scan(origin, destination, proj, blocked, clean)

    ci = _circleci()
    ci_configured = ci is not None
    ci_error = None
    if ci is None and cfg.circleci_token == "":
        ci_error = "Sin CIRCLECI_TOKEN configurado."

    repos = _apply_filters(_branch_repos_cached(data.client, origin, destination, proj, blocked), proj, blocked)
    workers = min(MAX_WORKERS, len(repos) or 1)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = []
        for i, repo in enumerate(repos):
            futures.append(ex.submit(_repo_scan, data.client, ci, repo, origin, destination, clean))
            if i < len(repos) - 1:
                time.sleep(_SUBMIT_DELAY)
        results = [f.result() for f in futures]

    items = [r[0] for r in results]
    items.sort(key=lambda x: x["slug"])
    first_ci_error = next((r[1] for r in results if r[1]), None)
    if first_ci_error:
        ci_error = ci_error or first_ci_error

    with_pr = sum(1 for it in items if it["pr"].get("exists"))
    synced = sum(1 for it in items if it["behind"] == 0)
    prod = sum(1 for it in items if (it["deploys"].get("prod") or {}).get("status") == "success")
    stats = {
        "repos": len(items),
        "with_pr": with_pr,
        "synced": synced,
        "prod": prod,
    }

    result = {
        "origin": origin,
        "destination": destination,
        "prefixes": clean,
        "ci_configured": ci_configured,
        "ci_error": ci_error,
        "stats": stats,
        "repos": items,
    }
    cache.set_scan(origin, destination, proj, blocked, result, clean)
    return result


@router.post("/pr")
def create_pr(repo: str, origin: str, destination: str = "master", title: str = ""):
    data = _require_session()
    try:
        if not data.client.has_commits_ahead(repo, origin, destination):
            return JSONResponse(
                {"ok": False, "no_changes": True,
                 "error": f"No se puede crear el PR: no hay cambios entre {origin} y {destination}."},
                status_code=400,
            )
        pr = data.client.create_pr(repo, origin, destination, title=title or None)
    except (bb.BitbucketAuthError, bb.BitbucketError) as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    return {"ok": True, "pr": _serialize_pr(pr)}


def _pr_title(origin: str, destination: str, title: str) -> str:
    return (title or "").strip() or f"Release: {origin} → {destination}"


@router.post("/prs/create-missing")
def create_missing_prs(origin: str, destination: str = "master", title: str = "", project_prefixes: str = "", exclude: str = ""):
    """Crea PRs para los repos que aún no tienen uno, con el mismo título."""
    data = _require_session()
    cfg = Config()
    target = _pr_title(origin, destination, title)
    proj = _project_prefixes(cfg, project_prefixes)
    blocked = _exclude_repos(cfg, exclude)
    created: list[str] = []
    skipped: list[str] = []
    no_changes: list[str] = []
    failed: list[dict] = []
    for repo in _apply_filters(_branch_repos_cached(data.client, origin, destination, proj, blocked), proj, blocked):
        slug = repo.slug
        try:
            pr = data.client.find_pr(slug, origin, destination)
            if pr:
                skipped.append(slug)
                continue
            if not data.client.has_commits_ahead(slug, origin, destination):
                no_changes.append(slug)
                continue
            data.client.create_pr(slug, origin, destination, title=target)
            created.append(slug)
        except (bb.BitbucketAuthError, bb.BitbucketError) as exc:
            failed.append({"repo": slug, "error": str(exc)})
    return {
        "ok": True,
        "title": target,
        "created": created,
        "skipped": skipped,
        "no_changes": no_changes,
        "failed": failed,
    }


@router.post("/prs/update-titles")
def update_pr_titles(origin: str, destination: str = "master", title: str = "", project_prefixes: str = "", exclude: str = ""):
    """Actualiza el título de todos los PRs existentes al mismo valor."""
    data = _require_session()
    cfg = Config()
    target = _pr_title(origin, destination, title)
    proj = _project_prefixes(cfg, project_prefixes)
    blocked = _exclude_repos(cfg, exclude)
    updated: list[str] = []
    skipped: list[str] = []
    failed: list[dict] = []
    for repo in _apply_filters(_branch_repos_cached(data.client, origin, destination, proj, blocked), proj, blocked):
        slug = repo.slug
        try:
            pr = data.client.find_pr(slug, origin, destination)
            if not pr:
                skipped.append(slug)
                continue
            if (pr.get("title") or "") == target:
                skipped.append(slug)
                continue
            data.client.update_pr_title(slug, pr["id"], target)
            updated.append(slug)
        except (bb.BitbucketAuthError, bb.BitbucketError) as exc:
            failed.append({"repo": slug, "error": str(exc)})
    return {"ok": True, "title": target, "updated": updated, "skipped": skipped, "failed": failed}


@router.post("/tags")
def generate_tags(origin: str, prefixes: str = "", repo: str = "", destination: str = "master", project_prefixes: str = "", exclude: str = ""):
    """Crea tags `{env}-{pipeline_id}` sobre el head de la rama origen,
    por ambiente elegido y reusando el mismo pipeline id por commit.

    Por defecto aplica a todos los repos con la rama; con `repo` solo ese.
    Idempotente: no sobrescribe tags ya existentes.
    """
    data = _require_session()
    cfg = Config()
    envs = [p.strip().lower() for p in prefixes.split(",") if p.strip()] or cfg.deploy_prefixes
    proj = _project_prefixes(cfg, project_prefixes)
    blocked = _exclude_repos(cfg, exclude)
    ci = _circleci()
    if ci is None:
        return JSONResponse(
            {"ok": False, "error": "Sin CIRCLECI_TOKEN no se puede resolver el pipeline del commit."},
            status_code=400,
        )

    def _repos():
        for r in _apply_filters(_branch_repos_cached(data.client, origin, destination, proj, blocked), proj, blocked):
            if not repo or r.slug == repo:
                yield r

    candidates = list(_repos())
    if repo and not candidates:
        return JSONResponse(
            {"ok": False, "error": f"El repo '{repo}' no contiene la rama '{origin}'."},
            status_code=400,
        )

    items = []
    for r in candidates:
        slug = r.slug
        commit = data.client.commit_for_branch(slug, origin)
        try:
            pipeline_id = ci.pipeline_id_for_commit(slug, origin, commit)
        except CircleCiError as exc:
            items.append({"repo": slug, "commit": commit, "pipeline_id": None,
                          "created": [], "skipped": [], "errors": [str(exc)]})
            continue
        if not pipeline_id:
            items.append({"repo": slug, "commit": commit, "pipeline_id": None,
                          "created": [], "skipped": [],
                          "errors": [f"El commit {commit[:8]} no tiene pipeline en CircleCI"]})
            continue
        created, skipped, errors = [], [], []
        for env in envs:
            tag = f"{env}-{pipeline_id}"
            try:
                if data.client.tag_exists(slug, tag):
                    skipped.append(tag)
                    continue
                data.client.create_tag(slug, tag, commit)
                created.append(tag)
            except (bb.BitbucketAuthError, bb.BitbucketError) as exc:
                errors.append(f"{tag}: {exc}")
        items.append({"repo": slug, "commit": commit, "pipeline_id": pipeline_id,
                      "created": created, "skipped": skipped, "errors": errors})
    return {"ok": True, "origin": origin, "envs": envs, "items": items}


@router.post("/circleci-config")
def circleci_config(origin: str, prefixes: str = "", repo: str = "", project_prefixes: str = "", exclude: str = ""):
    """Genera/actualiza `.circleci/config.yml` en los repos con workflows de tag.

    Cada workflow `{env}-deploy-on-tag` corre solo cuando se crea un tag
    `{env}-{n}` y exige aprobación manual antes del job `deploy-{env}`.
    Escribe sobre la rama de origen (la del tag). No sobrescribe configs
    existentes: hace merge agregando solo jobs/workflows faltantes.
    """
    data = _require_session()
    cfg = Config()
    envs = [p.strip().lower() for p in prefixes.split(",") if p.strip()] or cfg.deploy_prefixes
    proj = _project_prefixes(cfg, project_prefixes)
    blocked = _exclude_repos(cfg, exclude)

    client = data.client
    try:
        repos = _apply_filters(_branch_repos_cached(client, origin, "", proj, blocked), proj, blocked)
    except bb.BitbucketError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    candidates = [r for r in repos if not repo or r.slug == repo]
    if repo and not candidates:
        return JSONResponse(
            {"ok": False, "error": f"El repo '{repo}' no contiene la rama '{origin}'."},
            status_code=400,
        )

    items = []
    for r in candidates:
        slug = r.slug
        try:
            head = client.commit_for_branch(slug, origin)
            existing = client.raw_file(slug, head, ".circleci/config.yml")
            content, changed, added = ensure_tag_workflows(existing, envs)
            if not changed:
                items.append({"repo": slug, "commit": "", "envs": [],
                              "created": False, "updated": False, "skipped": True, "errors": []})
                continue
            result = client.upsert_file(
                slug,
                origin,
                ".circleci/config.yml",
                content,
                f"feat: workflows de tag con aprobacion ({', '.join(added)})",
            )
            commit = result.get("hash") or client.commit_for_branch(slug, origin)
            items.append({"repo": slug, "commit": commit, "envs": added,
                          "created": not bool(existing), "updated": bool(existing),
                          "skipped": False, "errors": []})
        except (bb.BitbucketAuthError, bb.BitbucketError, ValueError) as exc:
            items.append({"repo": slug, "commit": "", "envs": [],
                          "created": False, "updated": False, "skipped": False,
                          "errors": [str(exc)]})
    return {"ok": True, "origin": origin, "envs": envs, "items": items}


@router.get("/diff")
def diff(origin: str, destination: str = "master", mode: str = "diff", project_prefixes: str = "", exclude: str = "", force: int = 0):
    """Parámetros SSM de la iniciativa origin → destination.

    `mode=diff` (default): analiza solo los archivos tocados por el diff.
    `mode=all`: recorre todos los archivos del repo en ambos refs (más lento).

    Clasifica cada parámetro en `nuevo` (no existe en ninguna rama destino) o
    `reutilizado` (ya productivo en destino de otro repo → revisar SSM), y
    lista los `removed` (solo en rama destino, no implica eliminarlos).

    El estado `reutilizado` se calcula contra el master de TODOS los repos del
    proyecto (aunque no traigan la rama origen), para que un repo totalmente
    nuevo que reutiliza parámetros ya productivos no los marque como `nuevo`.
    """
    data = _require_session()
    cfg = Config()
    prefixes = cfg.ssm_prefixes
    proj = _project_prefixes(cfg, project_prefixes)
    blocked = _exclude_repos(cfg, exclude)
    mode = mode if mode == "all" else "diff"
    cache = get_cache()

    if force:
        cache.invalidate(origin, destination, {"repositories": {"excluded": sorted(blocked), "prefixes": sorted(proj or [])}})
    elif cache.get_diff(origin, destination, proj, blocked, ssm_prefixes=prefixes) is not None:
        return cache.get_diff(origin, destination, proj, blocked, ssm_prefixes=prefixes)

    master_repos = _apply_filters(_branch_repos_cached(data.client, origin, destination, proj, blocked), proj, blocked)
    branch_repos = master_repos
    by_slug = {r.slug: r for r in branch_repos}

    def _repo_refs(client, repo):
        """Refs para leer raws: head de origen (del PR si existe) y de destino."""
        try:
            pr = client.find_pr(repo.slug, origin, destination)
        except bb.BitbucketError:
            pr = None
        origin_ref = ""
        if pr and pr.get("source_commit"):
            origin_ref = pr["source_commit"]
        if not origin_ref:
            origin_ref = client.commit_for_branch(repo.slug, origin) or origin
        dest_ref = client.commit_for_branch(repo.slug, destination) or destination
        return origin_ref, dest_ref

    def _resolve_master(client, repo) -> set:
        """Master params de un repo (cache o lectura completa)."""
        cached_params = cache.get_master(repo.slug, destination)
        if cached_params is not None:
            return cached_params
        dest_ref = client.commit_for_branch(repo.slug, destination) or destination
        params = _read_files_params(client, repo.slug, dest_ref,
                                    client.list_files(repo.slug, dest_ref), prefixes)
        if params:
            cache.set_master(repo.slug, destination, params)
        return params

    # release_by_repo[slug] = paths en release (origen) del repo
    # dest_by_repo[slug] = paths en master (destino) del repo (solo paths, sin ARN)
    release_by_repo: dict[str, set] = {}
    dest_by_repo: dict[str, set] = {}

    if mode == "all":
        def _scan_all(client, repo):
            slug = repo.slug
            origin_ref, dest_ref = _repo_refs(client, repo)
            origin_params = _read_files_params(client, slug, origin_ref,
                                               client.list_files(slug, origin_ref), prefixes)
            dest_params = _read_files_params(client, slug, dest_ref,
                                             client.list_files(slug, dest_ref), prefixes)
            if dest_params:
                cache.set_master(slug, destination, dest_params)
            return slug, origin_params, dest_params

        with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(master_repos) or 1)) as ex:
            futures = {ex.submit(_scan_all, data.client, r): r for r in master_repos}
            for fut, repo in futures.items():
                slug, origin_params, dest_params = fut.result()
                dest_by_repo[slug] = {p for p, _ in dest_params}
                if slug in by_slug:
                    release_by_repo[slug] = {p for p, _ in origin_params}
    else:
        def _run_repo_diff(client, repo):
            return repo.slug, client.diff(repo.slug, destination, origin)

        # Master params de TODOS los repos (cache o lectura completa).
        with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(master_repos) or 1)) as ex:
            futures = {ex.submit(_resolve_master, data.client, r): r.slug for r in master_repos}
            for fut, slug in futures.items():
                dest_by_repo[slug] = {p for p, _ in fut.result()}

        # Etapa A: diffs y selección de archivos con indicio SSM (solo branch repos).
        candidates: list[tuple[str, str, str, str]] = []
        with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(branch_repos) or 1)) as ex:
            futures = {ex.submit(_run_repo_diff, data.client, r): r for r in branch_repos}
            for fut, repo in futures.items():
                slug, d = fut.result()
                pending = [f for f in d.files if _suggests_ssm(f, prefixes)]
                if not pending:
                    continue
                origin_ref, dest_ref = _repo_refs(data.client, repo)
                for f in pending:
                    candidates.append((slug, f.path, origin_ref, dest_ref))

        # Etapa B: raws + extracción por archivo (en paralelo).
        def _file_params(client, cand):
            slug, path, origin_ref, dest_ref = cand
            raw_origin = client.raw_file(slug, origin_ref, path)
            raw_dest = client.raw_file(slug, dest_ref, path)
            origin_params = set(extract_ssm_params([raw_origin], prefixes)) if raw_origin else set()
            return origin_params

        if candidates:
            with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(candidates) or 1)) as ex:
                results = [ex.submit(_file_params, data.client, c).result() for c in candidates]
            for (slug, _path, _o, _d), origin_params in zip(candidates, results):
                release_by_repo.setdefault(slug, set()).update({p for p, _ in origin_params})

    # Master global (union de paths en master de los repos del alcance).
    global_dest: set[str] = set()
    for paths in dest_by_repo.values():
        global_dest |= paths

    # Clasificacion global por path: reutilizado si ya existe en master global, si no nuevo.
    tipo = classify_ssm(release_by_repo, global_dest)

    # Release global: un path que aparece en N repos se agrega en una sola entrada
    # con el set de repos y la cantidad (count). Los master-only no aparecen.
    merged: dict[str, set[str]] = {}
    for slug, paths in release_by_repo.items():
        for path in paths:
            merged.setdefault(path, set()).add(slug)
    params = [
        {
            "param": path,
            "arn": "",
            "tipo": tipo.get(path, "nuevo"),
            "qa_value": None,
            "repos": sorted(entry),
            "count": len(entry),
        }
        for path, entry in sorted(merged.items())
    ]

    # Info de debug por repo: remover = paths en master del repo que no estan en su release.
    removed_merged: dict[str, set[str]] = {}
    for slug, dest_paths in dest_by_repo.items():
        for path in dest_paths - release_by_repo.get(slug, set()):
            removed_merged.setdefault(path, set()).add(slug)
    removed = [
        {"param": path, "repos": sorted(slugs)}
        for path, slugs in sorted(removed_merged.items())
    ]

    repos_out = [
        {
            "repo": slug,
            "added": sorted(release_by_repo.get(slug, set())),
            "removed": sorted(dest_by_repo.get(slug, set()) - release_by_repo.get(slug, set())),
        }
        for slug in sorted(by_slug)
    ]
    result = {
        "origin": origin,
        "destination": destination,
        "prefixes": [p.rstrip("/") for p in prefixes],
        "mode": mode,
        "repos": repos_out,
        "params": params,
        "removed": removed,
    }
    cache.set_diff(origin, destination, proj, blocked, result, ssm_prefixes=prefixes)
    return result