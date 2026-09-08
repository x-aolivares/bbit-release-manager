from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

from .cache import DEFAULT_AWS_REGION, DEFAULT_CLIENT_ALIAS, get_cache

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_CLIENT_MARKER = _PROJECT_ROOT / "data" / "client_id"
_DEFAULT_CLIENT_MARKER = _CLIENT_MARKER


def _marker_path() -> Path:
    """Ruta del marker del cliente activo (sobrescribible por env en tests)."""
    override = os.environ.get("BBIT_CLIENT_MARKER")
    if override:
        return Path(override)
    return _CLIENT_MARKER

# Variables que determinan si un servicio tiene credencial almacenada.
_SECRET_KEYS = {
    "Bitbucket": ("BITBUCKET_TOKEN",),
    "CircleCi": ("CIRCLECI_TOKEN",),
    "AWS": ("AWS_PROFILE", "AWS_ACCESS_KEY_ID"),
}

# Claves canónicas de env vars por proveedor (mismas que antes en env.*).
_ENV_KEYS = {
    "Bitbucket": {"url": "BITBUCKET_URL", "workspace": "BITBUCKET_WORKSPACE", "username": "BITBUCKET_USERNAME", "token": "BITBUCKET_TOKEN"},
    "CircleCi": {"token": "CIRCLECI_TOKEN", "vcs": "CIRCLECI_VCS", "org": "CIRCLECI_ORG"},
    "AWS": {
        "profile": "AWS_PROFILE",
        "region": "AWS_REGION",
        "localstack": "AWS_LOCALSTACK",
        "endpoint": "AWS_ENDPOINT_URL",
        "access_key": "AWS_ACCESS_KEY_ID",
        "secret_key": "AWS_SECRET_ACCESS_KEY",
        "session_token": "AWS_SESSION_TOKEN",
    },
}


def _normalize_environments(ssm_environments) -> list[dict]:
    """Normaliza ambientes a lista de filas ``{name, region, localstack, endpoint_url}``.

    - dict ``{env: region}`` (str) → filas con solo región (flags vacíos).
    - dict ``{env: {"region":..., "localstack":..., "endpoint_url":...}}`` → filas.
    - lista de dicts → tal cual (con trims).
    """
    def _text(value: str | None) -> str:
        return "" if value is None else str(value).strip()

    def _flag(value) -> bool:
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)

    out: list[dict] = []
    if isinstance(ssm_environments, dict):
        for name, value in ssm_environments.items():
            name = (name or "").strip()
            if not name:
                continue
            if isinstance(value, dict):
                out.append(
                    {
                        "name": name,
                        "region": _text(value.get("region")),
                        "localstack": _flag(value.get("localstack")),
                        "endpoint_url": _text(value.get("endpoint_url")),
                    }
                )
            else:
                out.append(
                    {
                        "name": name,
                        "region": _text(value),
                        "localstack": False,
                        "endpoint_url": "",
                    }
                )
    else:
        for raw in ssm_environments or []:
            if not isinstance(raw, dict):
                continue
            name = (raw.get("name") or "").strip()
            if not name:
                continue
            out.append(
                {
                    "name": name,
                    "region": _text(raw.get("region")),
                    "localstack": _flag(raw.get("localstack")),
                    "endpoint_url": _text(raw.get("endpoint_url")),
                }
            )
    return out


def win_to_posix(path: str) -> str:
    """Convert a Windows path to Git Bash style for display (C:\\x -> /c/x)."""
    p = path.replace("\\", "/")
    if sys.platform == "win32" and re.match(r"^[A-Za-z]:/", p):
        p = f"/{p[0].lower()}{p[2:]}"
    return p


def posix_to_win(path: str) -> str:
    """Normalize a Git Bash style path (/c/x) to a real Windows path (C:\\x)."""
    m = re.match(r"^/([a-zA-Z])/(.*)$", path)
    if m:
        return f"{m.group(1).upper()}:{os.sep}{m.group(2).replace('/', os.sep)}".rstrip(os.sep)
    return path


def _default_settings() -> dict:
    """Settings por defecto (se guardan en la fila config/connection)."""
    return {
        "repos": [],
        "project_prefixes": [],
        "exclude_repos": [],
        "default_branch": "master",
        "ssm_prefixes": ["/config", "/common"],
        "deploy_prefixes": ["uat", "stgp", "prod"],
        "ssm_environments": {},       # {ambiente: región AWS} para la ssm-view
        "ssm_read_secrets": False,    # permite ver valores de secretos en la ssm-view
    }


def _split_commas(raw: str) -> list[str]:
    return [p.strip() for p in raw.split(",") if p.strip()]


def _is_production_db() -> bool:
    """True si la cache apunta al data/cache.db real (no una DB de test)."""
    return get_cache().db_path == _PROJECT_ROOT / "data" / "cache.db"


def _read_client_id() -> str | None:
    override = os.environ.get("BBIT_CLIENT_ID")
    if override:
        return override
    try:
        return _marker_path().read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _write_client_id(c_id: str) -> None:
    try:
        path = _marker_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(c_id, encoding="utf-8")
    except OSError:
        pass


def _resolve_client_id() -> str:
    """Cliente actual: env override > marker local > cliente default."""
    cache = get_cache()
    cid = _read_client_id()
    if cid and cache.get_client(cid):
        return cid
    client = cache.get_or_create_client(DEFAULT_CLIENT_ALIAS)
    _write_client_id(client["id"])
    return client["id"]


_legacy_migration_done = False
_connection_migration_done = False


def _migrate_legacy_env_base(path: Path | None = None) -> bool:
    """One-shot: importa ``config/env.base`` histórico a las tablas nuevas
    (client + service_authentication + settings) y elimina el archivo.

    Solo corre sobre la base por defecto (data/cache.db); con una DB temporal
    (tests) no toca archivos reales. Idempotente.
    """
    global _legacy_migration_done
    if _legacy_migration_done:
        return False
    _legacy_migration_done = True

    legacy = path or (_PROJECT_ROOT / "config" / "env.base")
    if not legacy.exists():
        return False

    from dotenv import dotenv_values

    values = dotenv_values(legacy) or {}
    cache = get_cache()
    client = cache.get_or_create_client(DEFAULT_CLIENT_ALIAS)

    bb_env = _compact_env({
        "BITBUCKET_URL": (values.get("BITBUCKET_URL") or "").strip(),
        "BITBUCKET_WORKSPACE": (values.get("BITBUCKET_WORKSPACE") or "").strip(),
        "BITBUCKET_USERNAME": values.get("BITBUCKET_USERNAME") or "",
        "BITBUCKET_TOKEN": values.get("BITBUCKET_TOKEN") or "",
    })
    if bb_env:
        cache.save_authentication(client["id"], "Bitbucket", bb_env)

    ci_env = _compact_env({
        "CIRCLECI_TOKEN": values.get("CIRCLECI_TOKEN") or "",
        "CIRCLECI_VCS": (values.get("CIRCLECI_VCS") or "").strip() or "bb",
        "CIRCLECI_ORG": (values.get("CIRCLECI_ORG") or "").strip(),
    })
    if ci_env:
        cache.save_authentication(client["id"], "CircleCi", ci_env)

    settings = _default_settings()
    if values.get("BITBUCKET_REPOS"):
        settings["repos"] = _split_commas(values.get("BITBUCKET_REPOS") or "")
    if values.get("BITBUCKET_PROJECT_PREFIXES"):
        settings["project_prefixes"] = _split_commas(values.get("BITBUCKET_PROJECT_PREFIXES") or "")
    if values.get("BITBUCKET_EXCLUDE_REPOS"):
        settings["exclude_repos"] = _split_commas(values.get("BITBUCKET_EXCLUDE_REPOS") or "")
    if values.get("BITBUCKET_DEFAULT_BRANCH"):
        settings["default_branch"] = (values.get("BITBUCKET_DEFAULT_BRANCH") or "").strip()
    if values.get("SSM_PREFIXES"):
        settings["ssm_prefixes"] = _split_commas(values.get("SSM_PREFIXES") or "")
    if values.get("DEPLOY_PREFIXES"):
        settings["deploy_prefixes"] = _split_commas(values.get("DEPLOY_PREFIXES") or "")
    current = cache.get_connection() or {"bypass_cache": False, "settings": {}}
    current_settings = dict(current.get("settings") or {})
    current_settings.update(settings)
    cache.save_connection({"bypass_cache": bool(current.get("bypass_cache", False)), "settings": current_settings})

    if not _read_client_id():
        _write_client_id(client["id"])
    try:
        legacy.unlink()
    except OSError:
        pass
    return True


def _migrate_connection_credentials() -> bool:
    """One-shot: divide la fila ``config/connection`` que quedó con credenciales
    (BBIT-3) en ``service_authentication`` por proveedor; la fila conserva solo
    settings. Idempotente.
    """
    global _connection_migration_done
    if _connection_migration_done:
        return False
    _connection_migration_done = True

    cache = get_cache()
    conn = cache.get_connection()
    if not conn:
        return False
    creds = conn.get("credentials") or {}
    bb = creds.get("bitbucket") or {}
    ci = creds.get("circle") or {}
    aws = creds.get("aws") or {}
    if not (bb or ci or aws):
        return False

    client = cache.get_or_create_client(DEFAULT_CLIENT_ALIAS)

    bb_env = _compact_env({
        "BITBUCKET_URL": bb.get("url") or "",
        "BITBUCKET_WORKSPACE": bb.get("workspace") or "",
        "BITBUCKET_USERNAME": bb.get("username") or "",
        "BITBUCKET_TOKEN": bb.get("token") or "",
    })
    if bb_env:
        cache.save_authentication(client["id"], "Bitbucket", bb_env)

    ci_env = _compact_env({
        "CIRCLECI_TOKEN": ci.get("token") or "",
        "CIRCLECI_VCS": ci.get("vcs") or "",
        "CIRCLECI_ORG": ci.get("org") or "",
    })
    if ci_env:
        cache.save_authentication(client["id"], "CircleCi", ci_env)

    aws_env = _compact_env({
        "AWS_PROFILE": (aws.get("profile") if isinstance(aws, dict) else "") or "",
        "AWS_REGION": (aws.get("region") if isinstance(aws, dict) else "") or "",
    })
    if aws_env:
        cache.save_authentication(client["id"], "AWS", aws_env)

    cache.save_connection({
        "bypass_cache": bool(conn.get("bypass_cache", False)),
        "settings": (conn.get("settings") or {}).copy(),
    })
    if not _read_client_id():
        _write_client_id(client["id"])
    return True


def _compact_env(env: dict) -> dict:
    return {k: v for k, v in env.items() if v is not None and str(v) != ""}


class Config:
    """Configuración y credenciales por cliente desde SQLite.

    - Credenciales por (cliente, proveedor) en ``service_authentication``
      (env vars en ``sa_details.env``), con ``expires_at`` opcional.
    - Settings (filtros, prefijos, rama base) en la fila ``config/connection``
      de ``init_sesion``.
    - Cliente actual resuelto por marker local (``data/client_id``) o env
      ``BBIT_CLIENT_ID``.
    """

    def __init__(self):
        if _is_production_db():
            _migrate_legacy_env_base()
        _migrate_connection_credentials()
        self.reload()

    @classmethod
    def reset(cls):
        """Limpia flags de migración y marker local (para tests)."""
        global _legacy_migration_done, _connection_migration_done
        _legacy_migration_done = False
        _connection_migration_done = False
        try:
            _marker_path().unlink()
        except OSError:
            pass

    @classmethod
    def for_client(cls, c_id: str) -> "Config":
        """Config de un cliente específico (multi-usuario web).

        Resuelve credenciales/settings para el cliente provisto sin tocar el
        marker global ``data/client_id`` (modo CLI / mono-usuario intacto).
        La instancia queda fijada a ese cliente.
        """
        obj = cls.__new__(cls)
        obj._reload_for(c_id)
        return obj

    def reload(self) -> "Config":
        self._reload_for(_resolve_client_id())
        return self

    def _reload_for(self, c_id: str) -> None:
        self._c_id = c_id
        cache = get_cache()

        settings = _default_settings()
        conn = cache.get_connection()
        if conn and isinstance(conn.get("settings"), dict):
            settings = {**settings, **conn["settings"]}
        self._settings = settings

        self._auth = {}
        for key, provider in (("bitbucket", "Bitbucket"), ("circleci", "CircleCi"), ("aws", "AWS")):
            auth = cache.get_authentication(self._c_id, provider) or {}
            self._auth[key] = auth
        self._bb = self._auth["bitbucket"].get("env", {})
        self._ci = self._auth["circleci"].get("env", {})
        self._aws = self._auth["aws"].get("env", {})
        return self

    # -- cliente ----------------------------------------------------------------

    @property
    def client_id(self) -> str:
        return self._c_id

    @property
    def client_alias(self) -> str:
        client = get_cache().get_client(self._c_id)
        return client["alias"] if client else ""

    def set_client_alias(self, alias: str, seed: str = "") -> "Config":
        """Cambia/crea el cliente activo por alias y lo vuelve el default local.

        ``seed`` se usa solo en la creación del cliente (uuid5 determinístico);
        los clientes existentes conservan su id. Sin seed, uuid4 (fallback).
        """
        client = get_cache().get_or_create_client(alias or DEFAULT_CLIENT_ALIAS, seed=seed)
        _write_client_id(client["id"])
        return self.reload()

    # -- credenciales -----------------------------------------------------------

    @property
    def bitbucket_url(self) -> str:
        return (self._bb.get("BITBUCKET_URL") or "https://bitbucket.org").rstrip("/")

    @property
    def bitbucket_token(self) -> str:
        return self._bb.get("BITBUCKET_TOKEN") or ""

    @property
    def bitbucket_username(self) -> str:
        return self._bb.get("BITBUCKET_USERNAME") or ""

    @property
    def workspace(self) -> str:
        return (self._bb.get("BITBUCKET_WORKSPACE") or "").strip()

    @property
    def circleci_token(self) -> str:
        return self._ci.get("CIRCLECI_TOKEN") or ""

    @property
    def circleci_vcs(self) -> str:
        return (self._ci.get("CIRCLECI_VCS") or "bb").strip()

    @property
    def circleci_org(self) -> str:
        return self._ci.get("CIRCLECI_ORG") or self.workspace

    @property
    def aws_profile(self) -> str:
        return self._aws.get("AWS_PROFILE") or ""

    @property
    def aws_region(self) -> str:
        """Región efectiva: valor persistido o default alineado con yappy-cli-manager.

        El front ya no expone AWS_REGION; se usa ``DEFAULT_AWS_REGION`` para
        levantar la sesión SSO sin un valor global intervenido.
        """
        return (self._aws.get("AWS_REGION") or "").strip() or DEFAULT_AWS_REGION

    @property
    def aws_environments(self) -> list[dict]:
        """Filas completas de ambientes (name, region, localstack, endpoint_url)."""
        return get_cache().list_aws_environments()

    @property
    def aws_endpoint_url(self) -> str:
        return (self._aws.get("AWS_ENDPOINT_URL") or "").strip()

    @property
    def aws_localstack(self) -> bool:
        """El endpoint custom solo se aplica cuando apuntás a LocalStack/Docker."""
        return str(self._aws.get("AWS_LOCALSTACK") or "").lower() in ("1", "true", "yes", "on")

    @property
    def aws_access_key_id(self) -> str:
        return (self._aws.get("AWS_ACCESS_KEY_ID") or "").strip()

    @property
    def aws_secret_access_key(self) -> str:
        return (self._aws.get("AWS_SECRET_ACCESS_KEY") or "").strip()

    @property
    def aws_session_token(self) -> str:
        return (self._aws.get("AWS_SESSION_TOKEN") or "").strip()

    @property
    def aws_env(self) -> dict:
        """Bloque env completo de AWS (profile/region/SSM_DECRYPT...)."""
        return dict(self._aws)

    def stored_services(self) -> dict:
        """Credenciales no vacías por proveedor: {provider: {env, expires_at}}."""
        out = {}
        for key, provider_name in (("bitbucket", "Bitbucket"), ("circleci", "CircleCi"), ("aws", "AWS")):
            env = self._auth[key].get("env", {})
            secret_keys = _SECRET_KEYS[provider_name]
            if any(env.get(k) for k in secret_keys):
                out[provider_name] = self._auth[key]
        return out

    def service_states(self) -> dict:
        """Estado por proveedor para la UI (stored/expires/warning)."""
        out = {}
        for key, provider_name in (("bitbucket", "Bitbucket"), ("circleci", "CircleCi"), ("aws", "AWS")):
            env = self._auth[key].get("env", {})
            exp = self._auth[key].get("expires_at")
            has = any(env.get(k) for k in _SECRET_KEYS[provider_name])
            warning = None
            if exp:
                if exp < time.time():
                    warning = "expired"
                elif exp - time.time() < 7 * 86400:
                    warning = "expiring"
            out[key] = {"stored": bool(has), "expires_at": exp, "warning": warning}
        return out

    # -- settings ----------------------------------------------------------------

    @property
    def repos(self) -> list[str]:
        return list(self._settings.get("repos") or [])

    @property
    def project_prefixes(self) -> list[str]:
        return [p.strip().lower() for p in (self._settings.get("project_prefixes") or [])]

    @property
    def exclude_repos(self) -> list[str]:
        return [r.strip().lower() for r in (self._settings.get("exclude_repos") or [])]

    @property
    def default_branch(self) -> str:
        return self._settings.get("default_branch") or "master"

    @property
    def ssm_prefixes(self) -> list[str]:
        return [p.strip().rstrip("/") for p in (self._settings.get("ssm_prefixes") or [])]

    @property
    def ssm_environments(self) -> dict:
        """Mapa ambiente → región AWS usado por el query de update de la ssm-view.

        Los ambientes viven en la tabla informativa ``aws_environment``
        (sembrados qa/dev en cada boot + agregados por el usuario). Se preserva
        el fallback a ``settings`` por retrocompatibilidad.
        """
        table = get_cache().aws_environments_map()
        if table:
            merged = dict(table)
            merged.update(self._settings.get("ssm_environments") or {})
            return merged
        return dict(self._settings.get("ssm_environments") or {})

    @property
    def ssm_read_secrets(self) -> bool:
        """Habilita mostrar valores de secretos en la ssm-view (default: false)."""
        return bool(self._settings.get("ssm_read_secrets", False))

    @property
    def deploy_prefixes(self) -> list[str]:
        return list(self._settings.get("deploy_prefixes") or [])

    @property
    def frontend_root(self) -> Path:
        return _PROJECT_ROOT / "frontend"

    @property
    def is_configured(self) -> bool:
        return bool(self.bitbucket_url and self.workspace and self.bitbucket_token)

    # -- persistencia -----------------------------------------------------------

    def _connection_details(self) -> dict:
        return {"bypass_cache": False, "settings": dict(self._settings)}

    def save_service(self, provider_name: str, env: dict, expires_at: float | None = None) -> int:
        """Persiste el bloque env de un proveedor para el cliente actual."""
        sid = get_cache().save_authentication(self._c_id, provider_name, env, expires_at)
        self.reload()
        return sid

    def save_tokens(
        self,
        *,
        bitbucket_token: str = "",
        workspace: str = "",
        bitbucket_username: str = "",
        bitbucket_url: str = "",
        circleci_token: str = "",
        circleci_vcs: str = "",
        circleci_org: str = "",
        aws_profile: str = "",
        aws_region: str = "",
        aws_localstack: str = "",
        aws_endpoint_url: str = "",
        aws_access_key_id: str = "",
        aws_secret_access_key: str = "",
        aws_session_token: str = "",
        expires_at: float | None = None,
    ) -> int:
        """Upsert por proveedor con lo que venga. Devuelve el último sa_id o -1.

        Solo guarda un proveedor si llega (o ya había) su secreto
        (token/profile/access key): pasar solo workspace no crea una fila de
        token vacía. AWS acepta profile **o** credenciales directas.
        """
        cache = get_cache()
        last = -1
        if bitbucket_token or self._bb.get("BITBUCKET_TOKEN"):
            bb_env = _compact_env({
                "BITBUCKET_URL": bitbucket_url or self._bb.get("BITBUCKET_URL") or "",
                "BITBUCKET_WORKSPACE": workspace or self._bb.get("BITBUCKET_WORKSPACE") or "",
                "BITBUCKET_USERNAME": bitbucket_username or self._bb.get("BITBUCKET_USERNAME") or "",
                "BITBUCKET_TOKEN": bitbucket_token or self._bb.get("BITBUCKET_TOKEN") or "",
            })
            last = cache.save_authentication(self._c_id, "Bitbucket", bb_env, expires_at)
        if circleci_token or self._ci.get("CIRCLECI_TOKEN"):
            ci_env = _compact_env({
                "CIRCLECI_TOKEN": circleci_token or self._ci.get("CIRCLECI_TOKEN") or "",
                "CIRCLECI_VCS": circleci_vcs or self._ci.get("CIRCLECI_VCS") or "bb",
                "CIRCLECI_ORG": circleci_org or self._ci.get("CIRCLECI_ORG") or "",
            })
            last = cache.save_authentication(self._c_id, "CircleCi", ci_env, expires_at)
        if (aws_profile or aws_access_key_id
                or self._aws.get("AWS_PROFILE") or self._aws.get("AWS_ACCESS_KEY_ID")):
            aws_env = _compact_env({
                "AWS_PROFILE": aws_profile or self._aws.get("AWS_PROFILE") or "",
                "AWS_REGION": aws_region or self._aws.get("AWS_REGION") or "",
                "AWS_LOCALSTACK": aws_localstack or self._aws.get("AWS_LOCALSTACK") or "",
                "AWS_ENDPOINT_URL": aws_endpoint_url or self._aws.get("AWS_ENDPOINT_URL") or "",
                "AWS_ACCESS_KEY_ID": aws_access_key_id or self._aws.get("AWS_ACCESS_KEY_ID") or "",
                "AWS_SECRET_ACCESS_KEY": aws_secret_access_key or self._aws.get("AWS_SECRET_ACCESS_KEY") or "",
                "AWS_SESSION_TOKEN": aws_session_token or self._aws.get("AWS_SESSION_TOKEN") or "",
            })
            last = cache.save_authentication(self._c_id, "AWS", aws_env, expires_at)
        if last != -1:
            self.reload()
        return last

    def save_filters(self, project_prefixes: str = "", exclude_repos: str = "") -> int:
        """Persiste prefijos de proyecto y exclusiones de repos (settings)."""
        details = self._connection_details()
        settings = details["settings"]
        if project_prefixes:
            settings["project_prefixes"] = _split_commas(project_prefixes)
        if exclude_repos:
            settings["exclude_repos"] = _split_commas(exclude_repos)
        sid = get_cache().save_connection(details)
        self.reload()
        return sid

    def clear_filters(self) -> None:
        """Limpia prefijos de proyecto y exclusiones en la fila de conexión."""
        details = self._connection_details()
        details["settings"]["project_prefixes"] = []
        details["settings"]["exclude_repos"] = []
        get_cache().save_connection(details)
        self.reload()

    def save_ssm_settings(
        self,
        ssm_environments=None,
        ssm_read_secrets: bool | None = None,
    ) -> int:
        """Persiste settings de la ssm-view (SSM_ENVIRONMENTS / SSM_READ_SECRETS).

        ``ssm_environments`` admite:
        - dict ``{env: region}`` (legacy) — conserva flags/endpoint existentes;
        - lista ``[{name, region, localstack?, endpoint_url?}]`` (canónico).

        En ambos casos se escribe la tabla informativa ``aws_environment``
        (source of truth) y el mapa región se refleja en ``settings``.
        """
        details = self._connection_details()
        settings = details["settings"]
        if ssm_environments is not None:
            rows = _normalize_environments(ssm_environments)
            settings["ssm_environments"] = {
                row["name"]: row["region"] for row in rows if row.get("region")
            }
            get_cache().save_aws_environments(rows)
        if ssm_read_secrets is not None:
            settings["ssm_read_secrets"] = bool(ssm_read_secrets)
        sid = get_cache().save_connection(details)
        self.reload()
        return sid

    def remove_credentials(self) -> None:
        """Elimina todas las credenciales del cliente (no toca settings ni la fila client)."""
        get_cache().clear_authentications(self._c_id)
        self.reload()