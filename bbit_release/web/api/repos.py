from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
import re
import time

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse

from ...bitbucket import client as bb
from ...cache import DEFAULT_AWS_REGION, get_cache
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

MAX_WORKERS = 8


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
    
    BBIT-33: El cloning ocurre dentro de repos_with_branch() (no aquí).
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
    
    items = [{
        "repo_name": r.slug, "name": r.name, "workspace": r.workspace,
        "default_branch": r.default_branch, "resolved_branch": getattr(r, "resolved_branch", ""),
    } for r in repos]
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


def _read_files_params(
    client, slug: str, ref: str, files: list[str], prefixes,
    source: dict[str, str] | None = None,
) -> set:
    """Parámetros SSM en los raw de un ref para archivos de tipo texto.

    Con `source` (snapshot {path: contenido}) lee de memoria en vez de
    raw_file por archivo: una sola descarga por (slug, ref)."""
    def _one(path: str):
        name = path.rsplit("/", 1)[-1]
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if ext and f".{ext}" in NON_TEXT_EXT:
            return set()
        raw = source.get(path) if source is not None else client.raw_file(slug, ref, path)
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


def _session_config(data) -> "Config":
    """Config del cliente de la sesión (multi-usuario-ready).

    Si la sesión trae `client_id`, usa `Config.for_client` para no pisar el
    marker global; si no (sesiones previas), cae en el marker (mono-usuario).
    """
    if getattr(data, "client_id", ""):
        return Config.for_client(data.client_id)
    return Config()


def _circleci() -> CircleCiClient | None:
    cfg = Config()
    if not cfg.circleci_token:
        return None
    return CircleCiClient(
        cfg.circleci_token,
        vcs=cfg.circleci_vcs,
        org=cfg.circleci_org or cfg.workspace,
        recorder=_recorder,
        cache=get_cache(),
    )


SERVICE_MAP = {
    "bitbucket": ("Bitbucket", "BITBUCKET_TOKEN"),
    "circleci": ("CircleCi", "CIRCLECI_TOKEN"),
    "aws": ("AWS", "AWS_PROFILE"),
}


def _bb_probe(workspace: str, token: str, url: str) -> tuple[bool, str]:
    from ...bitbucket.client import BitbucketAuthError, BitbucketClient, BitbucketError

    try:
        with BitbucketClient(workspace, token, url=url) as c:
            _, identity = c.session()
    except (BitbucketAuthError, BitbucketError) as exc:
        return False, str(exc)
    return True, identity


def _ci_probe(token: str, vcs: str, org: str) -> tuple[bool, str]:
    ci = CircleCiClient(token, vcs=vcs, org=org, recorder=_recorder)
    try:
        ci.me()
    except (CircleCiError, ValueError) as exc:
        return False, str(exc)
    finally:
        ci.close()
    return True, ""


def _aws_probe(profile: str, region: str, *, endpoint_url: str = "",
               access_key_id: str = "", secret_access_key: str = "",
               session_token: str = "") -> tuple[bool, str]:
    from ...aws.client import validate_credentials

    return validate_credentials(
        profile, region,
        endpoint_url=endpoint_url,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        session_token=session_token,
    )


def _validate_service(service: str, body: dict, cfg: Config) -> tuple[bool, str]:
    """Valida credenciales de un servicio contra su API. (ok, detalle/error)."""
    if service == "bitbucket":
        ws = (body.get("workspace") or "").strip()
        tok = (body.get("token") or "").strip()
        if not ws or not tok:
            return False, "workspace y token son obligatorios"
        url = (body.get("url") or "").strip() or cfg.bitbucket_url
        return _bb_probe(ws, tok, url)
    if service == "circleci":
        tok = (body.get("token") or "").strip()
        if not tok:
            return False, "token es obligatorio"
        vcs = (body.get("vcs") or "").strip() or "bb"
        org = (body.get("org") or "").strip() or (body.get("workspace") or "").strip() or cfg.workspace
        ok, err = _ci_probe(tok, vcs, org)
        return (ok, err if ok else f"Token de CircleCI inválido o sin acceso: {err}")
    if service == "aws":
        profile = (body.get("profile") or "").strip()
        region = (body.get("region") or "").strip() or DEFAULT_AWS_REGION
        localstack = bool(body.get("localstack"))
        access_key_id = (body.get("access_key_id") or "").strip()
        secret_access_key = (body.get("secret_access_key") or "").strip()
        if not profile and not access_key_id:
            return False, "profile o credenciales directas son obligatorias"
        return _aws_probe(
            profile, region,
            endpoint_url=(body.get("endpoint_url") or "").strip() if localstack else "",
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            session_token=(body.get("session_token") or "").strip(),
        )
    raise ValueError(f"Servicio desconocido: {service}")


def _env_for(cfg: Config, service: str, body: dict) -> dict:
    """Bloque env canónico de un servicio desde el body (para sa_details)."""
    from ...config import _compact_env

    if service == "bitbucket":
        return _compact_env({
            "BITBUCKET_URL": (body.get("url") or "").strip() or cfg.bitbucket_url,
            "BITBUCKET_WORKSPACE": (body.get("workspace") or "").strip(),
            "BITBUCKET_USERNAME": (body.get("username") or "").strip(),
            "BITBUCKET_TOKEN": (body.get("token") or "").strip(),
        })
    if service == "circleci":
        return _compact_env({
            "CIRCLECI_TOKEN": (body.get("token") or "").strip(),
            "CIRCLECI_VCS": (body.get("vcs") or "").strip() or "bb",
            "CIRCLECI_ORG": (body.get("org") or "").strip() or (body.get("workspace") or "").strip() or cfg.workspace,
        })
    if service == "aws":
        localstack = bool(body.get("localstack"))
        return _compact_env({
            "AWS_PROFILE": (body.get("profile") or "").strip(),
            "AWS_REGION": (body.get("region") or "").strip() or DEFAULT_AWS_REGION,
            "AWS_LOCALSTACK": "1" if localstack else "",
            "AWS_ENDPOINT_URL": (body.get("endpoint_url") or "").strip() if localstack else "",
            "AWS_ACCESS_KEY_ID": (body.get("access_key_id") or "").strip(),
            "AWS_SECRET_ACCESS_KEY": (body.get("secret_access_key") or "").strip(),
            "AWS_SESSION_TOKEN": (body.get("session_token") or "").strip(),
        })
    raise ValueError(f"Servicio desconocido: {service}")


@router.get("/client")
def client_status():
    """Cliente activo + estado de servicios + settings (para la UI).

    Incluye el bloque ``auth`` con los valores actuales de cada servicio
    (para precargar el formulario de configuración del frontend).
    """
    cfg = Config()
    return {
        "client": {"alias": cfg.client_alias or "local"},
        "services": cfg.service_states(),
        "auth": {
            "bitbucket": {
                "url": cfg.bitbucket_url,
                "workspace": cfg.workspace,
                "username": cfg.bitbucket_username,
                "token": cfg.bitbucket_token,
            },
            "circleci": {
                "token": cfg.circleci_token,
                "vcs": cfg.circleci_vcs,
                "org": cfg.circleci_org,
            },
            "aws": {
                "profile": cfg.aws_profile,
                "region": cfg.aws_region,
                # endpoint no es secreto; credenciales directas NO se exponen
                "endpoint_url": cfg.aws_endpoint_url,
                "localstack": cfg.aws_localstack,
            },
        },
        "settings": {
            "project_prefixes": cfg.project_prefixes,
            "exclude_repos": cfg.exclude_repos,
            "default_branch": cfg.default_branch,
            "ssm_prefixes": cfg.ssm_prefixes,
            "deploy_prefixes": cfg.deploy_prefixes,
            "ssm_environments": cfg.ssm_environments,
            "ssm_read_secrets": cfg.ssm_read_secrets,
        },
        "configured": cfg.is_configured,
    }


@router.post("/auth/{service}/validate")
def validate_service_auth(service: str, body: dict):
    """Valida credenciales de un servicio SIN guardarlas."""
    if service not in SERVICE_MAP:
        raise HTTPException(404, f"Servicio desconocido: {service}")
    try:
        ok, detail = _validate_service(service, body, Config())
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": ok, "service": service, "detail": detail}


@router.post("/auth/{service}")
def save_service_auth(service: str, body: dict):
    """Valida y guarda credenciales de un servicio para el cliente activo."""
    if service not in SERVICE_MAP:
        raise HTTPException(404, f"Servicio desconocido: {service}")
    cfg = Config()
    try:
        ok, detail = _validate_service(service, body, cfg)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if not ok:
        return JSONResponse({"ok": False, "error": detail}, status_code=401)
    env = _env_for(cfg, service, body)
    if not env:
        return JSONResponse({"ok": False, "error": "No llegan credenciales para guardar."}, status_code=400)
    provider = SERVICE_MAP[service][0]
    expires_at = body.get("expires_at")
    try:
        cfg.save_service(provider, env, float(expires_at) if expires_at else None)
    except OSError as exc:
        return JSONResponse(
            {"ok": False, "error": f"No se pudo guardar en la DB: {exc}"},
            status_code=500,
        )
    return {"ok": True, "service": service, "stored": True, "detail": detail}


@router.get("/session")
@router.post("/session/reset-git-clones")
def reset_git_clones():
    """Limpia git_clones_dir para resetear a default (~/.bbit/clones).
    
    Útil cuando la config está apuntando a una ruta inválida o de test.
    """
    cfg = Config()
    conn = get_cache().get_connection()
    if conn:
        settings = conn.get("settings", {})
        settings["git_clones_dir"] = ""  # Vacío = default
        conn["settings"] = settings
        get_cache().save_connection(conn)
    
    cfg.reload()
    return {
        "ok": True,
        "git_clones_dir": cfg.git_clones_dir,
        "message": "git_clones_dir reseteado a default"
    }


def session_status():
    cfg = Config()
    sid = active_session_id()
    data = get_session(sid) if sid else None
    states = cfg.service_states()
    git = {
        "enabled": bool(cfg.git_clones_dir),
        "clones_dir": cfg.git_clones_dir or "",
    }
    if data:
        return {
            "active": True,
            "identity": data.identity,
            "workspace": data.workspace,
            "repo_count": data.repo_count,
            "client_alias": cfg.client_alias or "local",
            "services": states,
            "stored": bool(cfg.bitbucket_token),
            "needs_tokens": False,
            "git": git,
        }
    configured = bool(cfg.bitbucket_token) and bool(cfg.workspace)
    return {
        "active": False,
        "client_alias": cfg.client_alias or "local",
        "services": states,
        "stored": configured,
        "needs_tokens": not configured,
        "git": git,
    }


@router.post("/session/reuse")
def session_reuse(body: dict | None = None):
    if active_session_id():
        return session_status()
    body = body or {}
    alias = (body.get("alias") or "").strip()
    cfg = Config()
    if alias and alias != cfg.client_alias:
        if get_cache().get_client_by_alias(alias) is None:
            return JSONResponse(
                {"ok": False, "error": "No hay un cliente con ese alias. Registrate con alias, workspace y token."},
                status_code=400,
            )
        cfg = cfg.set_client_alias(alias)
    tok = cfg.bitbucket_token
    ws = cfg.workspace
    if not (tok and ws):
        return JSONResponse(
            {"ok": False, "error": "No hay credenciales para este cliente. Conectate o ejecutá 'bbit login'."},
            status_code=400,
        )
    
    # Probar credenciales rápidamente SIN crear sesión completa
    # para evitar loops si son inválidas
    try:
        import httpx
        headers = {"Authorization": f"Bearer {tok}", "Accept": "application/json"}
        with httpx.Client(headers=headers, timeout=5.0) as client:
            resp = client.get("https://api.bitbucket.org/2.0/user")
            if resp.status_code == 401:
                return JSONResponse(
                    {"ok": False, "error": "Las credenciales guardadas vencieron o ya no son válidas. Generá de nuevo."},
                    status_code=401,
                )
            if resp.status_code >= 400:
                return JSONResponse(
                    {"ok": False, "error": f"Error al validar credenciales: {resp.status_code}"},
                    status_code=401,
                )
    except Exception as exc:
        return JSONResponse(
            {"ok": False, "error": f"No se pudo validar las credenciales: {str(exc)}"},
            status_code=500,
        )
    
    # Credenciales OK, crear sesión completa
    try:
        data = create_session(ws, tok, client_id=cfg.client_id)
    except (bb.BitbucketAuthError, bb.BitbucketError) as exc:
        return JSONResponse(
            {"ok": False, "error": f"Error al crear sesión: {str(exc)}"},
            status_code=401,
        )
    return {
        "ok": True,
        "active": True,
        "identity": data.identity,
        "workspace": data.workspace,
        "repo_count": data.repo_count,
        "client_alias": cfg.client_alias or "local",
        "services": cfg.service_states(),
        "stored": True,
    }


@router.post("/session")
def api_session(body: dict):
    workspace = (body.get("workspace") or "").strip()
    token = (body.get("token") or "").strip()
    circleci_token = (body.get("circleci_token") or "").strip()
    aws_profile = (body.get("aws_profile") or "").strip()
    aws_region = (body.get("aws_region") or "").strip()
    aws_localstack = bool(body.get("aws_localstack"))
    aws_endpoint_url = (body.get("aws_endpoint_url") or "").strip()
    aws_access_key_id = (body.get("aws_access_key_id") or "").strip()
    aws_secret_access_key = (body.get("aws_secret_access_key") or "").strip()
    aws_session_token = (body.get("aws_session_token") or "").strip()
    if not workspace or not token:
        raise HTTPException(400, "workspace y token son obligatorios")

    cfg = Config()
    if body.get("alias"):
        alias = (body.get("alias") or "").strip()
        seed = f"{alias}|{time.time()}|{token}"
        cfg = cfg.set_client_alias(alias, seed=seed)

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

    if aws_profile or aws_access_key_id:
        ok_a, detail_a = _aws_probe(
            aws_profile, aws_region or DEFAULT_AWS_REGION,
            endpoint_url=aws_endpoint_url if aws_localstack else "",
            access_key_id=aws_access_key_id,
            secret_access_key=aws_secret_access_key,
            session_token=aws_session_token,
        )
        if not ok_a:
            return JSONResponse(
                {"ok": False, "error": f"Credenciales AWS inválidas: {detail_a}"},
                status_code=401,
            )

    try:
        data = create_session(workspace, token, client_id=cfg.client_id)
    except (bb.BitbucketAuthError, bb.BitbucketError) as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=401)

    try:
        cfg.save_tokens(
            bitbucket_token=token,
            circleci_token=circleci_token,
            workspace=workspace,
            bitbucket_username=(body.get("username") or "").strip(),
            aws_profile=aws_profile,
            aws_region=aws_region,
            aws_localstack="1" if aws_localstack else "",
            aws_endpoint_url=aws_endpoint_url if aws_localstack else "",
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key,
            aws_session_token=aws_session_token,
        )
        cfg.save_filters(
            project_prefixes=",".join(_project_prefixes(cfg, body.get("project_prefixes", "")) or []),
            exclude_repos=",".join(sorted(_exclude_repos(cfg, body.get("exclude_repos", "")))),
        )
        if "git_clones_dir" in body:
            cfg.save_git_clones_dir((body.get("git_clones_dir") or "").strip())
    except OSError as exc:
        return JSONResponse(
            {"ok": False, "error": f"Sesión OK pero no se pudo guardar la conexión en la DB: {exc}"},
            status_code=500,
        )

    return {
        "ok": True,
        "session_id": data.session_id,
        "identity": data.identity,
        "workspace": data.workspace,
        "repo_count": data.repo_count,
        "client_alias": cfg.client_alias or "local",
        "services": cfg.service_states(),
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


@router.put("/session/git")
def session_git(body: dict):
    """Persiste la carpeta de clones del motor local-git (BBIT-33)."""
    clones_dir = (body.get("clones_dir") or "").strip()
    try:
        cfg = Config()
        cfg.save_git_clones_dir(clones_dir)
    except OSError as exc:
        return JSONResponse(
            {"ok": False, "error": f"No se pudo guardar la carpeta de clones: {exc}"},
            status_code=500,
        )
    return {"ok": True, "git": {"enabled": bool(clones_dir), "clones_dir": clones_dir}}


@router.post("/session/clone")
def session_clone(body: dict | None = None):
    """Clona (o hace fetch) de los repos del workspace en la carpeta de clones.

    Sin ``repos`` explícito usa la lista completa del workspace (con caché).
    Ejecuta en paralelo con la concurrencia acotada del motor git. Los slots que
    ya existen solo reciben un fetch incremental (no destructivo).
    """
    cfg = Config()
    clones_dir = cfg.git_clones_dir or ""
    if not clones_dir:
        raise HTTPException(
            400, "Configurá la carpeta de clones (git_clones_dir) antes de clonar."
        )
    data = _require_session()
    client = data.client

    from ..localgit.client import LocalRepoClient

    if not isinstance(client, LocalRepoClient):
        raise HTTPException(
            400,
            "El motor git no está activo en esta sesión. Conectate de nuevo tras "
            "guardar la carpeta de clones.",
        )

    body = body or {}
    prefs = _project_prefixes(cfg, body.get("project_prefixes", ""))
    blocked = _exclude_repos(cfg, body.get("exclude_repos", ""))

    slugs = [s.strip() for s in (body.get("repos") or "").split(",") if s.strip()]
    if not slugs:
        base = _all_repos_cached(client, prefs, blocked)
        if base is not None:
            slugs = [r.slug for r in _apply_filters(base, prefs, blocked)]
        else:
            slugs = [r.slug for r in _apply_filters(client.list_repos(prefixes=prefs), prefs, blocked)]
    if not slugs:
        return {"ok": True, "clones_dir": clones_dir, "repos": []}

    from concurrent.futures import ThreadPoolExecutor

    results = []
    with ThreadPoolExecutor(max_workers=min(8, len(slugs) or 1)) as ex:
        futures = {ex.submit(client.ensure_repo, s): s for s in slugs}
        for fut, slug in futures.items():
            try:
                ok = fut.result(timeout=660)
                results.append({"slug": slug, "ok": ok, "error": "" if ok else "clone/fetch falló"})
            except Exception as exc:
                results.append({"slug": slug, "ok": False, "error": str(exc)})
    results.sort(key=lambda r: r["slug"])
    return {"ok": True, "clones_dir": clones_dir, "repos": results}





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
        "job": deploy.job,
        "approval": deploy.approval,
    }


def _tag_match(prefix: str) -> re.Pattern:
    return re.compile(rf"^{re.escape(prefix)}-(\d+)$", re.IGNORECASE)


def _repo_scan(client, ci, repo, origin, destination, clean, ctx=None):
    """Payload de un repo (etapa paralela de /scan).

    Reusa el head de la rama origen desde el PR cuando existe
    (source_commit), evitando GET /commits/{branch}.

    La columna de cada ambiente sale del tag `{env}-{pipeline_id}` del commit:
    el deploy es válido solo si el pipeline del tag tiene ese número, la
    revisión del commit y un workflow que mencione el ambiente.
    
    IMPORTANTE (BBIT-33): Si la rama NO existe en el repo (commit vacío),
    retorna None para que se ignore en los resultados. Esto evita mostrar
    repos que no tienen la rama buscada.
    """
    resolved = getattr(repo, "resolved_branch", "") or ""
    try:
        pr = client.find_pr(repo.slug, origin, destination)
    except bb.BitbucketError:
        pr = None

    if pr and pr.get("source_commit"):
        commit = pr["source_commit"]
    else:
        commit = client.commit_for_branch(repo.slug, origin, resolved=resolved)
    
    # BBIT-33: Si la rama no existe (commit vacío), no incluir en resultados
    if not commit:
        log.debug("scan: %s rama %s no existe, ignorando", repo.slug, origin)
        return None, None
    
    if ctx is not None:
        ctx.setdefault(repo.slug, {})["pr"] = pr
        ctx.setdefault(repo.slug, {})["origin_ref"] = commit
    if not pr:
        no_changes = not client.has_commits_ahead(repo.slug, origin, destination)
    else:
        no_changes = False
    match_commit = commit
    if ci is not None and pr and pr.get("source_commit"):
        branch_head = client.commit_for_branch(repo.slug, origin, resolved=resolved)
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
    env_tasks: list[tuple[str, str]] = []
    for prefix in clean:
        env = prefix.lower()
        found_tag = None
        for t in tags:
            if _tag_match(env).match(t["name"]):
                found_tag = t["name"]
                break
        match_tag[env] = found_tag
        deploys[env] = None
        if ci is not None and not found_tag:
            log.info("scan: %s env=%s sin tag %s-en en commit %s", repo.slug, env, env, match_commit[:12])
        if ci is not None and found_tag and match_commit:
            env_tasks.append((env, found_tag))

    def _env_deploy(task):
        """Deploy por tag/env aislado: devuelve (env, payload, err) para
        mergear el resultado en el hilo principal sin tocar ci_error."""
        env, found_tag = task
        deploy = None
        err = None
        log.info(
            "scan: %s env=%s tag=%s commit=%s -> buscando deploy_for_tag",
            repo.slug, env, found_tag, match_commit[:12],
        )
        try:
            deploy = ci.deploy_for_tag(repo.slug, found_tag, match_commit, env)
        except CircleCiError as exc:
            err = str(exc)
        log.info("scan: %s env=%s deploy=%s", repo.slug, env, f"{deploy.status}" if deploy else "None")
        return env, _serialize_deploy(deploy), err

    if env_tasks:
        with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(env_tasks) or 1)) as ex:
            results = list(ex.map(_env_deploy, env_tasks))
        for env, deploy, err in results:
            deploys[env] = deploy
            if err:
                ci_error = ci_error or err

    item = {
        "slug": repo.slug,
        "name": repo.name,
        "workspace": repo.workspace,
        "branch_url": client.branch_url(repo.slug, origin),
        "commit": commit,
        "no_changes": no_changes,
        "tags": tag_rows,
        "pr": _serialize_pr(pr),
        "deploys": deploys,
        "match_tag": match_tag,
        "ci_project": ci_project,
        "ci_vcs": ci.vcs if ci else None,
    }
    return item, ci_error


def _failed_repo_item(repo, client, origin, exc):
    """Item de scan para un repo cuya consulta falló.

    Se muestra en la tabla como fallido (con error) en vez de re-lanzar y
    romper el flow completo; el usuario reintenta solo estos repos a demanda.
    """
    return {
        "slug": repo.slug,
        "name": repo.name,
        "workspace": repo.workspace,
        "branch_url": client.branch_url(repo.slug, origin),
        "commit": "",
        "no_changes": False,
        "error": str(exc),
        "tags": [],
        "pr": {"exists": False},
        "deploys": {},
        "match_tag": {},
        "ci_project": None,
        "ci_vcs": None,
    }


def _scan_repos(client, ci, repos, origin, destination, clean, ctx=None):
    """Ejecuta el scan paralelo sobre una lista de repos (ya resueltos/filtrados).

    Un repo cuya consulta falla se marca como fallido en la tabla y no rompe
    el scan de los demás. Retorna (items, ci_error, stats).
    
    BBIT-33: Si un repo no tiene la rama, se filtra silenciosamente.
    """
    ci_error = None
    workers = min(MAX_WORKERS, len(repos) or 1)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(_repo_scan, client, ci, repo, origin, destination, clean, ctx) for repo in repos]
        results = []
        for fut, repo in zip(futures, repos):
            try:
                item, err = fut.result()
                # BBIT-33: Filtrar repos que no tienen la rama
                if item is not None:
                    results.append((item, err))
                else:
                    log.debug("scan: %s ignorado (sin rama %s)", repo.slug, origin)
            except (bb.BitbucketAuthError, bb.BitbucketError) as exc:
                log.warning("scan %s falló: %s", repo.slug, exc)
                results.append((_failed_repo_item(repo, client, origin, exc), None))

    items = [r[0] for r in results]
    items.sort(key=lambda x: x["slug"])
    first_ci_error = next((r[1] for r in results if r[1]), None)
    if first_ci_error:
        ci_error = first_ci_error

    with_pr = sum(1 for it in items if it["pr"].get("exists"))
    prod = sum(1 for it in items if (it["deploys"].get("prod") or {}).get("status") == "success")
    stats = {
        "repos": len(items),
        "with_pr": with_pr,
        "prod": prod,
    }
    return items, ci_error, stats


def _stream_scan(client, ci, repos, origin, destination, clean, ctx=None):
    """Generador que emite (item, error) por cada repo completado en el scan paralelo.

    Los repos se emiten a medida que terminan (``as_completed``), no en orden
    de envío: si un repo es lento, los que terminan primero se entregan antes
    por el SSE y el frontend los pinta apenas llegan.
    
    BBIT-33: Si un repo no tiene la rama, _repo_scan retorna None,None y se
    filtra silenciosamente (no se emite evento).
    """
    workers = min(MAX_WORKERS, len(repos) or 1)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {
            ex.submit(_repo_scan, client, ci, repo, origin, destination, clean, ctx): repo
            for repo in repos
        }
        for fut in as_completed(futures):
            repo = futures[fut]
            try:
                item, err = fut.result()
                # BBIT-33: Filtrar repos que no tienen la rama
                if item is None:
                    log.debug("stream_scan: %s ignorado (sin rama %s)", repo.slug, origin)
                    continue
                yield item, err
            except (bb.BitbucketAuthError, bb.BitbucketError) as exc:
                log.warning("stream scan %s falló: %s", repo.slug, exc)
                yield (_failed_repo_item(repo, client, origin, exc), None)


@router.get("/repos-quick")
def repos_quick(
    project_prefixes: str = "",
    exclude: str = "",
):
    """Endpoint rápido: devuelve repos del workspace con filtros aplicados.
    
    SIN hacer scan/diff/SSM - SIN verificar branches.
    Solo metadata básica de repos. Permite al usuario escribir origen/destino
    y filtrar localmente mientras se cargan datos pesados (verificación de
    branches, scan, diff, SSM) en background.
    
    Típicamente: < 1 segundo de respuesta. CERO requests de verificación
    de rama por repo.
    """
    data = _require_session()
    cfg = Config()
    proj = _project_prefixes(cfg, project_prefixes)
    blocked = _exclude_repos(cfg, exclude)
    
    # Obtener lista completa de repos (cacheada después de login)
    # IMPORTANTE: NO llamar a repos_with_branch() aquí.
    # Solo list_repos() que devuelve todos los repos del workspace.
    all_repos = _all_repos_cached(data.client, proj, blocked) or data.client.list_repos(prefixes=proj)
    repos = _apply_filters(all_repos, proj, blocked)
    
    return {
        "repos": [
            {
                "slug": r.slug,
                "name": r.name,
                "workspace": r.workspace,
                "default_branch": r.default_branch,
            }
            for r in repos
        ],
        "count": len(repos),
    }


@router.get("/flow")
def flow(
    origin: str,
    destination: str = "master",
    prefixes: str = "",
    project_prefixes: str = "",
    exclude: str = "",
    mode: str = "diff",
    force: int = 0,
    repos: str = "",
):
    """Endpoint unificado que resuelve repos UNA VEZ y computa scan + diff.

    Resuelve la lista de repos con la rama origen una sola vez, ejecuta el
    scan (PR, commits, tags, deploys) y el diff (params SSM) en el mismo
    ciclo, y retorna ambos payloads en una sola respuesta. Elimina la
    duplicación de resolución de repos entre los anteriores /api/scan,
    /api/diff y /api/repos.

    El parámetro `repos` (slugs separados por coma) limita el ESCAN a esos
    repos (reintento de los fallidos); el diff se sigue computando con el
    set completo para no alterar la clasificación.
    """
    data = _require_session()
    cfg_scan = Config()
    cfg_diff = _session_config(data)
    deploy_prefixes = [p.strip() for p in prefixes.split(",") if p.strip()] or cfg_scan.deploy_prefixes
    ssm_prefixes = cfg_diff.ssm_prefixes
    proj = _project_prefixes(cfg_scan, project_prefixes)
    blocked = _exclude_repos(cfg_scan, exclude)
    mode = mode if mode == "all" else "diff"
    only_repos = {s.strip() for s in repos.split(",") if s.strip()}
    cache = get_cache()

    if force:
        cache.invalidate(origin, destination, {
            "repositories": {"excluded": sorted(blocked), "prefixes": sorted(proj or [])}
        })

    repos = _apply_filters(
        _branch_repos_cached(data.client, origin, destination, proj, blocked),
        proj, blocked,
    )

    # Reintento a demanda: solo se re-escanan los repos fallidos pedidos.
    scan_repos = repos if not only_repos else [r for r in repos if r.slug in only_repos]

    ci = _circleci()
    ci_configured = ci is not None
    ci_error = None
    if ci is None and cfg_scan.circleci_token == "":
        ci_error = "Sin CIRCLECI_TOKEN configurado."

    # Contexto compartido por este request: por repo, el PR ya resuelto y los
    # refs de origen/destino ya calculados, para no re-consultarlos en el diff.
    ctx: dict = {r.slug: {} for r in repos}

    # Refresh liviano: si solo se pide re-escanear repos fallidos y el flow
    # completo ya está cacheado, se reusa el diff y no se lanza master params.
    cached_flow = None
    if only_repos:
        cached_flow = cache.get_flow(origin, destination, proj, blocked, deploy_prefixes, ssm_prefixes, mode)

    dest_by_repo = None
    if cached_flow is None and mode == "diff":
        # Overlap de fases: el scan y la resolución de master params corren en
        # paralelo (comparten el rate limiter global de BBIT-30) y el diff
        # reusa el resultado en vez de recomputarlo.
        with ThreadPoolExecutor(max_workers=2) as ex:
            scan_fut = ex.submit(_scan_repos, data.client, ci, scan_repos, origin, destination, deploy_prefixes, ctx)
            master_fut = ex.submit(_resolve_masters, data.client, repos, destination, ssm_prefixes, cache, ctx)
            scan_items, scan_ci_error, stats = scan_fut.result()
            dest_by_repo = master_fut.result()
    else:
        # En modo "all" no hay overlap: el diff re-resuelve los master params
        # junto con los origin params en la misma fase.
        scan_items, scan_ci_error, stats = _scan_repos(data.client, ci, scan_repos, origin, destination, deploy_prefixes, ctx)
    if scan_ci_error:
        ci_error = ci_error or scan_ci_error

    projects = [
        {"slug": r.slug, "name": r.name, "workspace": r.workspace, "default_branch": r.default_branch}
        for r in repos
    ]

    if cached_flow is not None:
        diff_raw = cached_flow["diff"]
    else:
        diff_raw = _compute_diff(
            data.client, origin, destination, mode, proj, blocked, ssm_prefixes, cache, ctx,
            dest_by_repo=dest_by_repo,
        )

    diff_enriched = _enrich_diff_ssm(diff_raw, cfg_diff)

    result = {
        "origin": origin,
        "destination": destination,
        "projects": projects,
        "scan": {
            "prefixes": deploy_prefixes,
            "ci_configured": ci_configured,
            "ci_error": ci_error,
            "stats": stats,
            "repos": scan_items,
        },
        "diff": diff_raw,
    }
    if not only_repos:
        cache.set_flow(origin, destination, proj, blocked, deploy_prefixes, ssm_prefixes, mode, result)
    result["diff"] = _enrich_diff_ssm(result["diff"], cfg_diff)
    return result


@router.get("/flow/stream")
def flow_stream(
    origin: str,
    destination: str = "master",
    prefixes: str = "",
    project_prefixes: str = "",
    exclude: str = "",
    mode: str = "diff",
    force: int = 0,
    repos: str = "",
):
    """Endpoint SSE de streaming del scan — entrega cada repo a medida que se completa.

    Mismos parámetros que ``GET /api/flow``. Emite eventos SSE ``repo``
    (por cada repo escaneado), ``stats`` (al terminar), ``diff``, y
    ``done`` (sentinel de cierre). El frontend suscribe ``EventSource`` a
    este endpoint para render incremental.
    """
    import json as _json

    data = _require_session()
    cfg_scan = Config()
    cfg_diff = _session_config(data)
    deploy_prefixes = [p.strip() for p in prefixes.split(",") if p.strip()] or cfg_scan.deploy_prefixes
    ssm_prefixes = cfg_diff.ssm_prefixes
    proj = _project_prefixes(cfg_scan, project_prefixes)
    blocked = _exclude_repos(cfg_scan, exclude)
    mode = mode if mode == "all" else "diff"
    only_repos = {s.strip() for s in repos.split(",") if s.strip()}
    cache = get_cache()

    if force:
        cache.invalidate(origin, destination, {
            "repositories": {"excluded": sorted(blocked), "prefixes": sorted(proj or [])}
        })

    repos_list = _apply_filters(
        _branch_repos_cached(data.client, origin, destination, proj, blocked),
        proj, blocked,
    )
    scan_repos = repos_list if not only_repos else [r for r in repos_list if r.slug in only_repos]

    ci = _circleci()
    ci_configured = ci is not None
    ci_error = None
    if ci is None and cfg_scan.circleci_token == "":
        ci_error = "Sin CIRCLECI_TOKEN configurado."

    ctx: dict = {}

    def _event(event_type: str, payload) -> str:
        return f"retry: 5000\nevent: {event_type}\ndata: {_json.dumps(payload)}\n\n"

    def _generate():
        items = []
        scan_ci_error = None

        # Overlap de fases: master params corren en un hilo paralelo al scan
        # (mismo rate limiter global). El executor se cierra al terminar o al
        # cerrarse el generador (disconnect del cliente), sin bloquear el yield.
        with ThreadPoolExecutor(max_workers=1) as ex:
            ctx.update({r.slug: {} for r in repos_list})
            master_fut = None
            if mode == "diff":
                master_fut = ex.submit(_resolve_masters, data.client, repos_list, destination, ssm_prefixes, cache, ctx)

            for item, err in _stream_scan(data.client, ci, scan_repos, origin, destination, deploy_prefixes, ctx):
                items.append(item)
                if err:
                    scan_ci_error = scan_ci_error or err
                yield _event("repo", item)

            if scan_ci_error:
                ci_error_local = ci_error or scan_ci_error
                yield _event("error", {"message": ci_error_local})

            items.sort(key=lambda x: x["slug"])
            with_pr = sum(1 for it in items if it["pr"].get("exists"))
            prod = sum(1 for it in items if (it["deploys"].get("prod") or {}).get("status") == "success")
            stats = {
                "repos": len(items),
                "with_pr": with_pr,
                "prod": prod,
            }
            yield _event("stats", stats)

            dest_by_repo = master_fut.result() if master_fut is not None else None
            diff_raw = _compute_diff(
                data.client, origin, destination, mode, proj, blocked, ssm_prefixes, cache, ctx,
                dest_by_repo=dest_by_repo,
            )
            diff_enriched = _enrich_diff_ssm(diff_raw, cfg_diff)
            yield _event("diff", diff_enriched)

            yield _event("done", {})

    return StreamingResponse(_generate(), media_type="text/event-stream")


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
    proj = _project_prefixes(cfg, project_prefixes)
    blocked = _exclude_repos(cfg, exclude)
    ci = _circleci()
    if ci is None:
        return JSONResponse(
            {"ok": False, "error": "Sin CIRCLECI_TOKEN no se puede resolver el pipeline del commit."},
            status_code=400,
        )

    # When repo param is set, resolve ONLY that repo directly — skip
    # _branch_repos_cached entirely and use provided prefixes as-is.
    if repo:
        envs = [p.strip().lower() for p in prefixes.split(",") if p.strip()] or cfg.deploy_prefixes
        try:
            commit = data.client.commit_for_branch(repo, origin)
        except (bb.BitbucketAuthError, bb.BitbucketError) as exc:
            return JSONResponse(
                {"ok": False, "error": str(exc)},
                status_code=400,
            )
        # Verify the repo contains the branch by checking if commit_for_branch returned a value
        if not commit:
            return JSONResponse(
                {"ok": False, "error": f"El repo '{repo}' no contiene la rama '{origin}'."},
                status_code=400,
            )
        try:
            pipeline_id = ci.pipeline_id_for_commit(repo, origin, commit)
        except CircleCiError as exc:
            items = [{"repo": repo, "commit": commit, "pipeline_id": None,
                      "created": [], "skipped": [], "errors": [str(exc)]}]
            return {"ok": True, "origin": origin, "envs": envs, "items": items}
        if not pipeline_id:
            items = [{"repo": repo, "commit": commit, "pipeline_id": None,
                      "created": [], "skipped": [],
                      "errors": [f"El commit {commit[:8]} no tiene pipeline en CircleCI"]}]
            return {"ok": True, "origin": origin, "envs": envs, "items": items}
        created, skipped, errors = [], [], []
        for env in envs:
            tag = f"{env}-{pipeline_id}"
            try:
                if data.client.tag_exists(repo, tag):
                    skipped.append(tag)
                    continue
                data.client.create_tag(repo, tag, commit)
                created.append(tag)
            except (bb.BitbucketAuthError, bb.BitbucketError) as exc:
                errors.append(f"{tag}: {exc}")
        items = [{"repo": repo, "commit": commit, "pipeline_id": pipeline_id,
                  "created": created, "skipped": skipped, "errors": errors}]
        return {"ok": True, "origin": origin, "envs": envs, "items": items}

    # Batch mode (no repo param): global resolution + cfg fallback
    envs = [p.strip().lower() for p in prefixes.split(",") if p.strip()] or cfg.deploy_prefixes

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





def _repo_refs(client, repo, origin: str, destination: str, ctx: dict | None = None):
    """Refs para leer raws: head de origen (del PR si existe) y de destino.

    Reutiliza el PR y los refs que el scan ya resolvió en este mismo
    request (ctx), evitando repetir find_pr y commit_for_branch.
    """
    entry = (ctx or {}).setdefault(repo.slug, {})
    if "pr" not in entry:
        try:
            pr = client.find_pr(repo.slug, origin, destination)
        except bb.BitbucketError:
            pr = None
        entry["pr"] = pr
    pr = entry["pr"]
    origin_ref = entry.get("origin_ref") or ""
    if not origin_ref:
        if pr and pr.get("source_commit"):
            origin_ref = pr["source_commit"]
        if not origin_ref:
            origin_ref = _ref_for(client, repo.slug, origin)
        entry["origin_ref"] = origin_ref
    dest_ref = entry.get("dest_ref") or ""
    if not dest_ref:
        dest_ref = _ref_for(client, repo.slug, destination)
        entry["dest_ref"] = dest_ref
    return origin_ref, dest_ref


def _ref_for(client, slug: str, branch: str) -> str:
    """Commit de una rama; si falla, degrada al nombre de la rama (sin romper)."""
    try:
        return client.commit_for_branch(slug, branch) or branch
    except (bb.BitbucketAuthError, bb.BitbucketError) as exc:
        log.warning("commit_for_branch %s %s falló: %s", slug, branch, exc)
        return branch


def _snapshot_for(client, slug: str, ref: str, ctx: dict | None = None) -> dict[str, str] | None:
    """Snapshot {path: contenido} con cache por (slug, ref) en ctx.

    Si el cliente no ofrece snapshot (o el ref no existe) devuelve None
    y el flujo cae en list_files + raw_file."""
    entry = (ctx or {}).setdefault(slug, {})
    snaps = entry.setdefault("snapshots", {})
    if ref in snaps:
        return snaps[ref]
    if not hasattr(client, "snapshot"):
        return None
    try:
        snap = client.snapshot(slug, ref)
    except (bb.BitbucketAuthError, bb.BitbucketError) as exc:
        log.warning("snapshot %s %s falló: %s", slug, ref, exc)
        return None
    snaps[ref] = snap
    return snap


def _read_all_params(client, slug: str, ref: str, prefixes, ctx: dict | None = None) -> set:
    """Params SSM de todos los archivos de texto de un ref. Con snapshot
    es un solo tarball; sin él, list_files + raw_file por archivo."""
    snap = _snapshot_for(client, slug, ref, ctx)
    if snap is not None:
        return _read_files_params(client, slug, ref, list(snap), prefixes, source=snap)
    return _read_files_params(client, slug, ref, client.list_files(slug, ref), prefixes)


def _resolve_master(client, repo, destination: str, prefixes, cache, ctx: dict | None = None) -> set:
    """Master params de un repo (cache o lectura completa).

    Devuelve el set de tuplas (path, arn) tal como se guarda en disco."""
    cached_params = cache.get_master(repo.slug, destination)
    if cached_params is not None:
        return cached_params
    entry = (ctx or {}).setdefault(repo.slug, {})
    dest_ref = entry.get("dest_ref") or ""
    if not dest_ref:
        dest_ref = _ref_for(client, repo.slug, destination)
        entry["dest_ref"] = dest_ref
    try:
        params = _read_all_params(client, repo.slug, dest_ref, prefixes, ctx)
    except (bb.BitbucketAuthError, bb.BitbucketError) as exc:
        log.warning("master params %s falló: %s", repo.slug, exc)
        return set()
    if params:
        cache.set_master(repo.slug, destination, params)
    return params


def _resolve_masters(client, repos, destination: str, prefixes, cache, ctx: dict | None = None) -> dict[str, set]:
    """Master params de todos los repos en paralelo: {slug: {paths sin ARN}}.

    Se usa en el overlap scan+diff: esta fase corre en paralelo con el scan
    y el resultado se le pasa a ``_compute_diff`` para no repetirla."""
    dest_by_repo: dict[str, set] = {}
    if not repos:
        return dest_by_repo
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(repos) or 1)) as ex:
        futures = {ex.submit(_resolve_master, client, r, destination, prefixes, cache, ctx): r.slug for r in repos}
        for fut, slug in futures.items():
            dest_by_repo[slug] = {p for p, _ in fut.result()}
    return dest_by_repo


def _compute_diff(
    client,
    origin: str,
    destination: str,
    mode: str,
    proj: list[str] | None,
    blocked: set[str],
    prefixes: list[str],
    cache,
    ctx: dict | None = None,
    dest_by_repo: dict[str, set] | None = None,
) -> dict:
    """Computa el payload del diff (sin valores SSM; esos van por overlay).

    Si se pasa ``dest_by_repo`` (paths de master por slug, ya resueltos por
    el overlap de fases) se reutiliza en vez de recalcular los master params.
    """
    master_repos = _apply_filters(_branch_repos_cached(client, origin, destination, proj, blocked), proj, blocked)
    branch_repos = master_repos
    by_slug = {r.slug: r for r in branch_repos}

    # release_by_repo[slug] = paths en release (origen) del repo
    # dest_by_repo[slug] = paths en master (destino) del repo (solo paths, sin ARN)
    release_by_repo: dict[str, set] = {}
    if dest_by_repo is None:
        dest_by_repo = {}

    if mode == "all":
        def _scan_all(client, repo):
            slug = repo.slug
            try:
                origin_ref, dest_ref = _repo_refs(client, repo, origin, destination, ctx)
                origin_params = _read_all_params(client, slug, origin_ref, prefixes, ctx)
                dest_params = _read_all_params(client, slug, dest_ref, prefixes, ctx)
            except (bb.BitbucketAuthError, bb.BitbucketError) as exc:
                log.warning("scan all %s falló: %s", slug, exc)
                return slug, set(), set()
            if dest_params:
                cache.set_master(slug, destination, dest_params)
            return slug, origin_params, dest_params

        with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(master_repos) or 1)) as ex:
            futures = {ex.submit(_scan_all, client, r): r for r in master_repos}
            for fut, repo in futures.items():
                slug, origin_params, dest_params = fut.result()
                dest_by_repo[slug] = {p for p, _ in dest_params}
                if slug in by_slug:
                    release_by_repo[slug] = {p for p, _ in origin_params}
    else:
        def _run_repo_diff(client, repo):
            try:
                return repo.slug, client.diff(repo.slug, destination, origin)
            except (bb.BitbucketAuthError, bb.BitbucketError) as exc:
                log.warning("diff %s falló: %s", repo.slug, exc)
                return repo.slug, None

        # Master params de TODOS los repos (cache o lectura completa). Si
        # el overlap ya los resolvió (dest_by_repo precomputado), se reusan.
        if not dest_by_repo:
            dest_by_repo = _resolve_masters(client, master_repos, destination, prefixes, cache, ctx)

        # Etapa A: diffs y selección de archivos con indicio SSM (solo branch repos).
        candidates: list[tuple[str, str, str, str]] = []
        with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(branch_repos) or 1)) as ex:
            futures = {ex.submit(_run_repo_diff, client, r): r for r in branch_repos}
            for fut, repo in futures.items():
                slug, d = fut.result()
                if d is None:
                    continue
                pending = [f for f in d.files if _suggests_ssm(f, prefixes)]
                if not pending:
                    continue
                origin_ref, dest_ref = _repo_refs(client, repo, origin, destination, ctx)
                for f in pending:
                    candidates.append((slug, f.path, origin_ref, dest_ref))

        # Etapa B: raws + extracción por archivo (en paralelo).
        def _file_params(client, cand):
            slug, path, origin_ref, dest_ref = cand
            try:
                snap_o = _snapshot_for(client, slug, origin_ref, ctx)
                snap_d = _snapshot_for(client, slug, dest_ref, ctx)
                if snap_o is not None and snap_d is not None:
                    raw_origin = snap_o.get(path)
                    raw_dest = snap_d.get(path)
                else:
                    raw_origin = client.raw_file(slug, origin_ref, path)
                    raw_dest = client.raw_file(slug, dest_ref, path)
            except (bb.BitbucketAuthError, bb.BitbucketError) as exc:
                log.warning("raw %s %s falló: %s", slug, path, exc)
                return set()
            origin_params = set(extract_ssm_params([raw_origin], prefixes)) if raw_origin else set()
            return origin_params

        if candidates:
            with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(candidates) or 1)) as ex:
                results = [ex.submit(_file_params, client, c).result() for c in candidates]
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
    return {
        "origin": origin,
        "destination": destination,
        "prefixes": [p.rstrip("/") for p in prefixes],
        "mode": mode,
        "repos": repos_out,
        "params": params,
        "removed": removed,
    }


def _enrich_diff_ssm(result: dict, cfg: Config) -> dict:
    """Overlay de valores SSM por cliente sobre el diff cacheado.

    El payload cacheado es compartido entre sesiones: aca se resuelven
    los env_values desde la ssm-view (cache local, sin llamada AWS) y el
    type por ambiente. Los valores reales de SSM (qa_value) se piden por
    separado cuando el usuario clickea "Revisar SSM".
    Se devuelve una copia enriquecida (sin escribir valores a la cache del diff).
    """
    import copy

    from ...ssm.store import SsmStore

    out = copy.deepcopy(result)
    store = SsmStore(get_cache())
    for p in out.get("params", []):
        overlay = store.enrich_param(
            p.get("param") or p.get("name") or "",
            read_secrets=cfg.ssm_read_secrets,
        )
        if overlay["env_values"]:
            p["type"] = overlay["type"]
            p["env_values"] = overlay["env_values"]
    return out