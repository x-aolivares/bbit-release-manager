from __future__ import annotations

import os
import glob
import re
import sys
from pathlib import Path
from dotenv import dotenv_values


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


def _package_root_config() -> Path:
    return Path(__file__).resolve().parent.parent / "config"


class Config:
    _config_dir: Path | None = None

    def __init__(self, env: str | None = None):
        self._env = env
        self._values: dict[str, str] = {}

        config_dir = self._get_config_dir()
        self._load_file(config_dir / "env.base")
        if env:
            env_file = config_dir / f"env.{env}"
            if env_file.exists():
                self._load_file(env_file)
            else:
                available = self.known_environments()
                raise ValueError(
                    f"No config for environment '{env}'. "
                    f"Available: {', '.join(available) if available else 'none'}"
                )

        local_env = config_dir.parent / ".env"
        if local_env.exists():
            self._load_file(local_env)

    @classmethod
    def _get_config_dir(cls) -> Path:
        if cls._config_dir:
            return cls._config_dir

        override = os.environ.get("BBIT_CONFIG_DIR")
        if override and Path(override).is_dir():
            cls._config_dir = Path(override)
            return cls._config_dir

        package_root = _package_root_config()
        if package_root.is_dir():
            cls._config_dir = package_root
            return cls._config_dir

        for parent in [Path.cwd(), *Path.cwd().parents]:
            candidate = parent / "config"
            if (candidate / "env.base").is_file():
                cls._config_dir = candidate
                return cls._config_dir

        cls._config_dir = package_root
        return cls._config_dir

    @classmethod
    def with_env(cls, env: str) -> Config:
        return cls(env=env)

    @classmethod
    def reset(cls):
        cls._config_dir = None

    def _load_file(self, path: Path):
        if not path.exists():
            return
        values = dotenv_values(path)
        if values:
            self._values.update(values)

    @classmethod
    def known_environments(cls) -> list[str]:
        config_dir = cls._get_config_dir()
        pattern = str(config_dir / "env.*")
        envs = []
        for f in glob.glob(pattern):
            name = Path(f).name[len("env."):]
            if name in ("", "base") or name.endswith(".example"):
                continue
            envs.append(name)
        return sorted(envs)

    def get(self, key: str, default: str | None = None) -> str | None:
        # Precedencia: (1) valor no vacío en config, (2) BBIT_<KEY> explicit,
        # (3) os.environ. Un valor vacío en los archivos cuenta como "no
        # seteado" para permitir overrides por entorno o test.
        if key in self._values:
            raw = self._values[key]
            if raw is not None and str(raw).strip():
                return raw
        for env_key in (f"BBIT_{key}", key):
            v = os.environ.get(env_key)
            if v is not None and str(v).strip():
                return v
        return default

    def require(self, key: str) -> str:
        val = self.get(key)
        if val is None or not str(val).strip():
            raise ValueError(
                f"Missing required config: {key} "
                f"(check config/env.{self._env or 'base'} "
                f"or set BBIT_{key})"
            )
        return val

    @property
    def env(self) -> str | None:
        return self._env

    @property
    def bitbucket_url(self) -> str:
        return (self.get("BITBUCKET_URL") or "").rstrip("/")

    @property
    def bitbucket_token(self) -> str:
        return self.get("BITBUCKET_TOKEN") or ""

    @property
    def bitbucket_username(self) -> str:
        return self.get("BITBUCKET_USERNAME") or ""

    @property
    def workspace(self) -> str:
        return (self.get("BITBUCKET_WORKSPACE") or "").strip()

    @property
    def repos(self) -> list[str]:
        raw = self.get("BITBUCKET_REPOS") or ""
        return [r.strip() for r in raw.split(",") if r.strip()] if raw else []

    @property
    def default_branch(self) -> str:
        return self.get("BITBUCKET_DEFAULT_BRANCH") or "master"

    @property
    def ssm_prefixes(self) -> list[str]:
        raw = self.get("SSM_PREFIXES") or "/config,/common"
        return [p.strip().rstrip("/") for p in raw.split(",") if p.strip()]

    @property
    def circleci_token(self) -> str:
        return self.get("CIRCLECI_TOKEN") or ""

    @property
    def circleci_vcs(self) -> str:
        return (self.get("CIRCLECI_VCS") or "bb").strip()

    @property
    def circleci_org(self) -> str:
        return self.get("CIRCLECI_ORG") or self.workspace

    @property
    def deploy_prefixes(self) -> list[str]:
        raw = self.get("DEPLOY_PREFIXES") or "uat,stgp,prod"
        return [p.strip() for p in raw.split(",") if p.strip()]

    @property
    def frontend_root(self) -> Path:
        return Path(__file__).resolve().parent.parent / "frontend"

    @property
    def is_configured(self) -> bool:
        return bool(self.bitbucket_url and self.workspace and self.bitbucket_token)

    def save_tokens(
        self,
        *,
        bitbucket_token: str = "",
        circleci_token: str = "",
        workspace: str = "",
    ) -> Path:
        """Persiste tokens (y workspace) en env.base, conservando comentarios y orden.

        Reemplaza el valor de la clave si ya existe; si no, la agrega al final.
        """
        path = self._get_config_dir() / "env.base"

        updates: dict[str, str] = {}
        if bitbucket_token:
            updates["BITBUCKET_TOKEN"] = bitbucket_token
            updates["BITBUCKET_WORKSPACE"] = workspace or self.workspace
        if circleci_token:
            updates["CIRCLECI_TOKEN"] = circleci_token
        if not updates:
            return path

        if path.exists():
            lines = path.read_text(encoding="utf-8").splitlines()
        else:
            lines = ["# Shared defaults - loaded for all environments"]

        out: list[str] = []
        for line in lines:
            m = re.match(r"^([A-Z0-9_]+)=", line)
            if m and m.group(1) in updates:
                out.append(f"{m.group(1)}={updates.pop(m.group(1))}")
            else:
                out.append(line.rstrip("\r"))
        for key, value in updates.items():
            out.append(f"{key}={value}")

        with path.open("w", encoding="utf-8", newline="\n") as fh:
            fh.write("\n".join(out) + "\n")
        return path

    def remove_credentials(self) -> Path:
        """Elimina BITBUCKET_TOKEN y CIRCLECI_TOKEN de env.base (conserva lo demás)."""
        path = self._get_config_dir() / "env.base"
        if not path.exists():
            return path
        remove = {"BITBUCKET_TOKEN", "CIRCLECI_TOKEN"}
        out = [
            line.rstrip("\r")
            for line in path.read_text(encoding="utf-8").splitlines()
            if not (line.startswith(("BITBUCKET_TOKEN=", "CIRCLECI_TOKEN=")))
        ]
        with path.open("w", encoding="utf-8", newline="\n") as fh:
            fh.write("\n".join(out) + "\n")
        return path