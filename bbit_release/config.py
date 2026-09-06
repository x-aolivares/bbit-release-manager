from __future__ import annotations

import os
import re
import sys
from copy import deepcopy
from pathlib import Path

from .cache import get_cache

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


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


def _default_details() -> dict:
    """Config canónica por defecto: credenciales por servicio + settings."""
    return {
        "bypass_cache": False,
        "credentials": {
            "bitbucket": {
                "url": "https://bitbucket.org",
                "workspace": "",
                "username": "",
                "token": "",
            },
            "circle": {"token": "", "vcs": "bb", "org": ""},
            # Reservado para BBIT-1 (sesión SSO: profile/region/... de AWS).
            "aws": {},
        },
        "settings": {
            "repos": [],
            "project_prefixes": [],
            "exclude_repos": [],
            "default_branch": "master",
            "ssm_prefixes": ["/config", "/common"],
            "deploy_prefixes": ["uat", "stgp", "prod"],
        },
    }


_legacy_migration_done = False


def _split_commas(raw: str) -> list[str]:
    return [p.strip() for p in raw.split(",") if p.strip()]


def _is_production_db() -> bool:
    """True si la cache apunta al data/cache.db real (no una DB de test)."""
    return get_cache().db_path == _PROJECT_ROOT / "data" / "cache.db"


def _migrate_legacy_env_base(path: Path | None = None) -> bool:
    """One-shot: importa ``config/env.base`` histórico a la fila de conexión y
    elimina el archivo.

    Solo corre sobre la base por defecto (data/cache.db); con una DB temporal
    (tests) no toca archivos reales. Idempotente: si ya existe la fila de
    conexión, solo se borra el archivo legacy.
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
    if cache.get_connection() is None:
        details = _default_details()
        bb = details["credentials"]["bitbucket"]
        circle = details["credentials"]["circle"]
        settings = details["settings"]
        url = (values.get("BITBUCKET_URL") or "").strip()
        if url:
            bb["url"] = url
        bb["workspace"] = (values.get("BITBUCKET_WORKSPACE") or "").strip()
        bb["username"] = values.get("BITBUCKET_USERNAME") or ""
        bb["token"] = values.get("BITBUCKET_TOKEN") or ""
        circle["token"] = values.get("CIRCLECI_TOKEN") or ""
        circle["vcs"] = (values.get("CIRCLECI_VCS") or "").strip() or "bb"
        circle["org"] = (values.get("CIRCLECI_ORG") or "").strip()
        settings["repos"] = _split_commas(values.get("BITBUCKET_REPOS") or "")
        settings["project_prefixes"] = _split_commas(values.get("BITBUCKET_PROJECT_PREFIXES") or "")
        settings["exclude_repos"] = _split_commas(values.get("BITBUCKET_EXCLUDE_REPOS") or "")
        settings["default_branch"] = (values.get("BITBUCKET_DEFAULT_BRANCH") or "").strip() or "master"
        settings["ssm_prefixes"] = _split_commas(values.get("SSM_PREFIXES") or "/config,/common")
        settings["deploy_prefixes"] = _split_commas(values.get("DEPLOY_PREFIXES") or "uat,stgp,prod")
        cache.save_connection(details)
    try:
        legacy.unlink()
    except OSError:
        pass
    return True


class Config:
    """Configuración y credenciales desde la fila de conexión en SQLite.

    Reemplaza los archivos ``config/env.*``: las credenciales por servicio
    (bitbucket, circle, aws) y los settings viven en el JSON ``is_details`` de
    la fila ``config/connection`` de ``init_sesion`` (data/cache.db).
    """

    def __init__(self):
        if _is_production_db():
            _migrate_legacy_env_base()
        self.reload()

    @classmethod
    def reset(cls):
        """Limpia el flag de migración one-shot (para tests)."""
        global _legacy_migration_done
        _legacy_migration_done = False

    def reload(self) -> "Config":
        """Recarga desde la fila de conexión (tras un save/clear)."""
        details = _default_details()
        conn = get_cache().get_connection()
        if conn:
            if isinstance(conn.get("credentials"), dict):
                details["credentials"] = {**details["credentials"], **conn["credentials"]}
            if isinstance(conn.get("settings"), dict):
                details["settings"] = {**details["settings"], **conn["settings"]}
        self._details = details
        self._creds = details["credentials"]
        self._settings = details["settings"]
        return self

    # -- credenciales ----------------------------------------------------------

    @property
    def bitbucket_url(self) -> str:
        return (self._creds["bitbucket"].get("url") or "https://bitbucket.org").rstrip("/")

    @property
    def bitbucket_token(self) -> str:
        return self._creds["bitbucket"].get("token") or ""

    @property
    def bitbucket_username(self) -> str:
        return self._creds["bitbucket"].get("username") or ""

    @property
    def workspace(self) -> str:
        return (self._creds["bitbucket"].get("workspace") or "").strip()

    @property
    def circleci_token(self) -> str:
        return self._creds["circle"].get("token") or ""

    @property
    def circleci_vcs(self) -> str:
        return (self._creds["circle"].get("vcs") or "bb").strip()

    @property
    def circleci_org(self) -> str:
        return self._creds["circle"].get("org") or self.workspace

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
    def deploy_prefixes(self) -> list[str]:
        return list(self._settings.get("deploy_prefixes") or [])

    @property
    def frontend_root(self) -> Path:
        return _PROJECT_ROOT / "frontend"

    @property
    def is_configured(self) -> bool:
        return bool(self.bitbucket_url and self.workspace and self.bitbucket_token)

    # -- persistencia -----------------------------------------------------------

    def save_tokens(
        self,
        *,
        bitbucket_token: str = "",
        circleci_token: str = "",
        workspace: str = "",
    ) -> int:
        """Persiste credenciales (BB token + workspace, Circle token) en la fila
        de conexión. Devuelve el ``is_id`` de la fila; ``-1`` si no hay cambios.
        """
        if not (bitbucket_token or circleci_token):
            return -1
        details = deepcopy(self._details)
        bb = details["credentials"]["bitbucket"]
        if bitbucket_token:
            bb["token"] = bitbucket_token
            bb["workspace"] = workspace or bb.get("workspace") or ""
        if circleci_token:
            details["credentials"]["circle"]["token"] = circleci_token
        sid = get_cache().save_connection(details)
        self.reload()
        return sid

    def save_filters(self, project_prefixes: str = "", exclude_repos: str = "") -> int:
        """Persiste prefijos de proyecto y exclusiones de repos en la conexión.

        Solo escribe las claves cuyo valor no sea vacío.
        """
        details = deepcopy(self._details)
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
        details = deepcopy(self._details)
        details["settings"]["project_prefixes"] = []
        details["settings"]["exclude_repos"] = []
        get_cache().save_connection(details)
        self.reload()

    def remove_credentials(self) -> None:
        """Elimina la fila de conexión completa (credenciales + settings)."""
        get_cache().clear_connection()
        self.reload()