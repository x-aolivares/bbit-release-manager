from concurrent.futures import ThreadPoolExecutor
import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from ...bitbucket import client as bb
from ...config import Config
from ...circleci.client import CircleCiClient, CircleCiError
from ...scan.params import extract_ssm_params
from ..session import create_session, get_session, destroy_session, active_session_id

router = APIRouter(prefix="/api", tags=["repos"])

MAX_WORKERS = 8
SSM_MARKERS = ("ssm:", "{{resolve:")


def _suggests_ssm(file) -> bool:
    """¿El archivo puede aportar un parámetro SSM nuevo? Solo si sus líneas
    añadidas mencionan un marker de SSM (evita raw_files inútiles)."""
    return any(("ssm:" in line) or ("{{resolve:" in line) for line in file.added_lines)


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


@router.delete("/session")
def destroy(delete_credentials: bool = False):
    sid = active_session_id()
    if sid:
        destroy_session(sid)
    deleted = False
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
def list_repos(origin: str = ""):
    sid = active_session_id()
    if not sid:
        return {"items": [], "configured": False, "error": "No hay sesión activa. Conectá desde la web."}
    data = get_session(sid)
    if not data:
        return {"items": [], "configured": False, "error": "Sesión inválida."}
    if origin:
        repos = data.client.repos_with_branch(origin)
    else:
        repos = data.client.list_repos()
    return {
        "items": [
            {"slug": r.slug, "name": r.name, "workspace": r.workspace, "default_branch": r.default_branch}
            for r in repos
        ],
        "configured": True,
        "error": None,
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
    behind = client.commits_behind(repo.slug, origin, destination)
    match_commit = commit
    if ci is not None and pr and pr.get("source_commit"):
        match_commit = client.commit_for_branch(repo.slug, origin) or commit
    tags = client.tags_on_commit(repo.slug, match_commit)

    ci_error = None
    tag_rows = []
    if ci is not None:
        try:
            tag_deploys = ci.deploys_for_tags(repo.slug, [t["name"] for t in tags])
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
        deploy_id = None
        for t in tags:
            m = _tag_match(env).match(t["name"])
            if m:
                found_tag = t["name"]
                deploy_id = int(m.group(1))
                break
        match_tag[env] = found_tag
        deploy = None
        if ci is not None and found_tag and deploy_id and match_commit:
            try:
                deploy = ci.deploy_for_tag(repo.slug, found_tag, deploy_id, match_commit, env)
            except CircleCiError as exc:
                ci_error = ci_error or str(exc)
        deploys[env] = _serialize_deploy(deploy)

    item = {
        "slug": repo.slug,
        "name": repo.name,
        "workspace": repo.workspace,
        "branch_url": client.branch_url(repo.slug, origin),
        "commit": commit,
        "behind": behind,
        "tags": tag_rows,
        "pr": _serialize_pr(pr),
        "deploys": deploys,
        "match_tag": match_tag,
    }
    return item, ci_error


@router.get("/scan")
def scan(origin: str, destination: str = "master", prefixes: str = ""):
    data = _require_session()
    cfg = Config()
    clean = [p.strip() for p in prefixes.split(",") if p.strip()] or cfg.deploy_prefixes

    ci = _circleci()
    ci_configured = ci is not None
    ci_error = None
    if ci is None and cfg.circleci_token == "":
        ci_error = "Sin CIRCLECI_TOKEN configurado."

    repos = data.client.repos_with_branch(origin)
    workers = min(MAX_WORKERS, len(repos) or 1)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [
            ex.submit(_repo_scan, data.client, ci, repo, origin, destination, clean)
            for repo in repos
        ]
        results = [f.result() for f in futures]

    items = [r[0] for r in results]
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

    return {
        "origin": origin,
        "destination": destination,
        "prefixes": clean,
        "ci_configured": ci_configured,
        "ci_error": ci_error,
        "stats": stats,
        "repos": items,
    }


@router.post("/pr")
def create_pr(repo: str, origin: str, destination: str = "master", title: str = ""):
    data = _require_session()
    try:
        pr = data.client.create_pr(repo, origin, destination, title=title or None)
    except (bb.BitbucketAuthError, bb.BitbucketError) as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    return {"ok": True, "pr": _serialize_pr(pr)}


def _pr_title(origin: str, destination: str, title: str) -> str:
    return (title or "").strip() or f"Release: {origin} → {destination}"


@router.post("/prs/create-missing")
def create_missing_prs(origin: str, destination: str = "master", title: str = ""):
    """Crea PRs para los repos que aún no tienen uno, con el mismo título."""
    data = _require_session()
    target = _pr_title(origin, destination, title)
    created: list[str] = []
    skipped: list[str] = []
    failed: list[dict] = []
    for repo in data.client.repos_with_branch(origin):
        slug = repo.slug
        try:
            pr = data.client.find_pr(slug, origin, destination)
            if pr:
                skipped.append(slug)
                continue
            data.client.create_pr(slug, origin, destination, title=target)
            created.append(slug)
        except (bb.BitbucketAuthError, bb.BitbucketError) as exc:
            failed.append({"repo": slug, "error": str(exc)})
    return {"ok": True, "title": target, "created": created, "skipped": skipped, "failed": failed}


@router.post("/prs/update-titles")
def update_pr_titles(origin: str, destination: str = "master", title: str = ""):
    """Actualiza el título de todos los PRs existentes al mismo valor."""
    data = _require_session()
    target = _pr_title(origin, destination, title)
    updated: list[str] = []
    skipped: list[str] = []
    failed: list[dict] = []
    for repo in data.client.repos_with_branch(origin):
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
def generate_tags(origin: str, prefixes: str = "", repo: str = "", destination: str = "master"):
    """Crea tags `{env}-{pipeline_id}` sobre el head de la rama origen,
    por ambiente elegido y reusando el mismo pipeline id por commit.

    Por defecto aplica a todos los repos con la rama; con `repo` solo ese.
    Idempotente: no sobrescribe tags ya existentes.
    """
    data = _require_session()
    cfg = Config()
    envs = [p.strip().lower() for p in prefixes.split(",") if p.strip()] or cfg.deploy_prefixes
    ci = _circleci()
    if ci is None:
        return JSONResponse(
            {"ok": False, "error": "Sin CIRCLECI_TOKEN no se puede resolver el pipeline del commit."},
            status_code=400,
        )

    def _repos():
        for r in data.client.repos_with_branch(origin):
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


@router.get("/diff")
def diff(origin: str, destination: str = "master"):
    data = _require_session()
    cfg = Config()
    prefixes = cfg.ssm_prefixes
    repos = data.client.repos_with_branch(origin)
    by_slug = {r.slug: r for r in repos}

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

    def _run_repo_diff(client, repo):
        """Etapa A: diff de texto por repo (en paralelo)."""
        return repo.slug, client.diff(repo.slug, destination, origin)

    # Etapa A: diffs y selección de archivos con indicio SSM.
    candidates: list[tuple[str, str, str, str]] = []
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(repos) or 1)) as ex:
        futures = [ex.submit(_run_repo_diff, data.client, r) for r in repos]
        for slug, d in (f.result() for f in futures):
            pending = [f for f in d.files if f.status != "deleted" and _suggests_ssm(f)]
            if not pending:
                continue
            origin_ref, dest_ref = _repo_refs(data.client, by_slug[slug])
            for f in pending:
                candidates.append((slug, f.path, origin_ref, dest_ref))

    # Etapa B: raws + extracción por archivo (en paralelo).
    def _file_params(client, cand):
        slug, path, origin_ref, dest_ref = cand
        raw_origin = client.raw_file(slug, origin_ref, path)
        if raw_origin is None:
            return []
        origin_params = set(extract_ssm_params([raw_origin], prefixes))
        raw_dest = client.raw_file(slug, dest_ref, path)
        dest_params = set(extract_ssm_params([raw_dest], prefixes)) if raw_dest else set()
        return [(p, a) for p, a in sorted(origin_params - dest_params)]

    merged: dict[tuple[str, str], set[str]] = {}
    if candidates:
        with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(candidates) or 1)) as ex:
            futures = [ex.submit(_file_params, data.client, c) for c in candidates]
            results = [f.result() for f in futures]
        for (slug, _path, _o, _d), new in zip(candidates, results):
            for p, a in new:
                merged.setdefault((p, a), set()).add(slug)

    repos_out = []
    for slug in [r.slug for r in repos]:
        new = sorted(
            (
                {"param": p, "arn": a}
                for (p, a), slugs in merged.items()
                if slug in slugs
            ),
            key=lambda x: x["param"],
        )
        repos_out.append({"repo": slug, "new_params": new})
    params = sorted(
        [
            {"param": p, "arn": a, "repos": sorted(slugs)}
            for (p, a), slugs in merged.items()
        ],
        key=lambda x: x["param"],
    )
    return {
        "origin": origin,
        "destination": destination,
        "prefixes": [p.rstrip("/") for p in prefixes],
        "repos": repos_out,
        "params": params,
    }