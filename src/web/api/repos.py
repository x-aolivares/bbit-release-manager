from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from ...bitbucket import client as bb
from ...config import Config
from ...circleci.client import CircleCiClient, CircleCiError
from ...scan.params import extract_ssm_params
from ..session import create_session, get_session, destroy_session, active_session_id

router = APIRouter(prefix="/api", tags=["repos"])


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
    items = []
    for repo in repos:
        commit = data.client.commit_for_branch(repo.slug, origin)
        behind = data.client.commits_behind(repo.slug, origin, destination)
        tags = data.client.tags_on_commit(repo.slug, commit)
        try:
            pr = data.client.find_pr(repo.slug, origin, destination)
        except bb.BitbucketError:
            pr = None

        tag_rows = []
        if ci is not None:
            try:
                tag_deploys = ci.deploys_for_tags(repo.slug, [t["name"] for t in tags])
            except CircleCiError as exc:
                tag_deploys = {}
                ci_error = ci_error or str(exc)
            for t in tags:
                tag_rows.append({"name": t["name"], "deploy": _serialize_deploy(tag_deploys.get(t["name"]))})
        else:
            tag_rows = [{"name": t["name"], "deploy": None} for t in tags]

        deploys: dict[str, dict | None] = {}
        if ci is not None:
            try:
                found = ci.scheduled_deploys_for_commit(repo.slug, origin, commit, clean)
                deploys = {p: _serialize_deploy(d) for p, d in found.items()}
            except CircleCiError as exc:
                ci_error = ci_error or str(exc)

        items.append({
            "slug": repo.slug,
            "name": repo.name,
            "workspace": repo.workspace,
            "branch_url": data.client.branch_url(repo.slug, origin),
            "commit": commit,
            "behind": behind,
            "tags": tag_rows,
            "pr": _serialize_pr(pr),
            "deploys": deploys,
        })

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


@router.get("/diff")
def diff(origin: str, destination: str = "master"):
    data = _require_session()
    cfg = Config()
    prefixes = cfg.ssm_prefixes
    repos = data.client.repos_with_branch(origin)

    per_repo = []
    merged: dict[tuple[str, str], list[str]] = {}
    for repo in repos:
        d = data.client.diff(repo.slug, destination, origin)
        origin_ref = data.client.commit_for_branch(repo.slug, origin) or origin
        dest_ref = data.client.commit_for_branch(repo.slug, destination) or destination
        new_params: list[tuple[str, str]] = []
        for f in d.files:
            if f.status == "deleted":
                continue
            raw_origin = data.client.raw_file(repo.slug, origin_ref, f.path)
            if raw_origin is None:
                continue
            origin_params = set(extract_ssm_params([raw_origin], prefixes))
            raw_dest = data.client.raw_file(repo.slug, dest_ref, f.path)
            dest_params = set(extract_ssm_params([raw_dest], prefixes)) if raw_dest else set()
            for p, a in sorted(origin_params - dest_params):
                new_params.append((p, a))
        new_params = sorted(set(new_params), key=lambda t: t[0])
        per_repo.append({
            "repo": repo.slug,
            "new_params": [{"param": p, "arn": a} for p, a in new_params],
        })
        for p, a in new_params:
            merged.setdefault((p, a), []).append(repo.slug)

    params = [
        {"param": p, "arn": a, "repos": sorted(set(rs))}
        for (p, a), rs in merged.items()
    ]
    params.sort(key=lambda x: x["param"])
    return {
        "origin": origin,
        "destination": destination,
        "prefixes": [p.rstrip("/") for p in prefixes],
        "repos": per_repo,
        "params": params,
    }