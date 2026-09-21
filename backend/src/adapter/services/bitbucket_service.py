"""Cliente del API REST de Bitbucket (v2).

Trae la lista de repositorios del workspace configurado. Autenticacion por
token (``BBIT_BITBUCKET_TOKEN``); sin token consulta solo repos publicos.

Nomenclatura: los services del adapter terminan en ``_service.py``.
"""

from __future__ import annotations

from typing import Any

import httpx

from backend.library.config import Settings


class BitbucketService:
    def __init__(self, settings: Settings) -> None:
        self._base_url = settings.bitbucket_base_url.rstrip("/")
        self._workspace = settings.bitbucket_workspace
        self._token = settings.bitbucket_token

    def _headers(self) -> dict[str, str]:
        if not self._token:
            return {}
        return {"Authorization": f"Bearer {self._token}"}

    def fetch_repositories(self) -> list[dict[str, Any]]:
        """Lista completa de repositorios del workspace (sigue la paginacion)."""
        repos: list[dict[str, Any]] = []
        url: str | None = f"{self._base_url}/repositories/{self._workspace}"

        with httpx.Client(timeout=15.0) as client:
            while url:
                response = client.get(url, headers=self._headers())
                response.raise_for_status()
                payload = response.json()
                repos.extend(payload.get("values", []))
                url = payload.get("next")

        return repos