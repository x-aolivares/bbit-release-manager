"""Logica del escaneo de repositorios con cache por TTL.

Flujo (request_history como bitacora del ultimo request al endpoint):

1. Si NO hay registro previo del endpoint -> se consulta Bitbucket.
2. Si hay registro pero ``rh_updated_at`` vencio (TTL superado) -> Bitbucket.
3. Si hay registro y es fresco -> se devuelve el cache de ``repositories``.

Al consultar Bitbucket se mappea, se reemplaza el contenido de
``repositories`` y se actualiza ``rh_updated_at``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError

from ..adapter.entities.repositories_entity import RepositoryEntity
from ..adapter.repositories.repositories_repository import RepositoriesRepository
from ..adapter.repositories.request_history_repository import RequestHistoryRepository
from ..adapter.services.bitbucket_service import BitbucketService
from backend.library.enums import RequestTtlEnum
from ..models.scan_repositories_model import ScanRepositoriesModel, ScanRepositoriesResponse

_SCAN_ENDPOINT = "/api/scan-repositories"


class ScanRepositoriesLogic:
    def __init__(
        self,
        repositories_repository: RepositoriesRepository,
        request_history_repository: RequestHistoryRepository,
        bitbucket_service: BitbucketService,
    ) -> None:
        self._repositories = repositories_repository
        self._request_history = request_history_repository
        self._bitbucket = bitbucket_service

    def run(self) -> ScanRepositoriesResponse:
        record = self._request_history.get_by_endpoint(_SCAN_ENDPOINT)
        if record is not None and self._is_fresh(record.rh_updated_at, RequestTtlEnum.SCAN_REPOSITORIES):
            return self._from_cache()
        return self._refresh()

    def _refresh(self) -> ScanRepositoriesResponse:
        raw_repos = self._bitbucket.fetch_repositories()
        stored = {entity.r_name: entity for entity in self._repositories.get_all()}
        models = [
            self._merge_stored(self._map_repo(raw), stored.get(raw.get("slug") or raw.get("name")))
            for raw in raw_repos
        ]
        self._repositories.replace_all(
            [(model.slug, model.model_dump_json()) for model in models]
        )
        self._request_history.touch(_SCAN_ENDPOINT)
        return ScanRepositoriesResponse(repositories=models)

    def _merge_stored(
        self,
        fresh: ScanRepositoriesModel,
        stored: RepositoryEntity | None,
    ) -> ScanRepositoriesModel:
        """Conserva los tags/sources que otros endpoints ya guardaron en
        ``r_details``: el refresh no pisa el enrich previo."""
        if stored is None:
            return fresh
        enriched = self._entity_to_model(stored)
        return fresh.model_copy(
            update={"name": enriched.name, "tags": enriched.tags, "sources": enriched.sources}
        )

    def _from_cache(self) -> ScanRepositoriesResponse:
        entities = self._repositories.get_all()
        models = [self._entity_to_model(entity) for entity in entities]
        return ScanRepositoriesResponse(repositories=models)

    def _is_fresh(self, updated_at: str, default_ttl: RequestTtlEnum) -> bool:
        try:
            last = datetime.strptime(updated_at, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return False
        elapsed_minutes = (datetime.now(timezone.utc).replace(tzinfo=None) - last).total_seconds() / 60
        return elapsed_minutes < default_ttl.value

    @classmethod
    def _map_repo(cls, raw: dict[str, Any]) -> ScanRepositoriesModel:
        slug = raw.get("slug") or raw.get("name") or ""
        name = raw.get("name") or slug
        return ScanRepositoriesModel(
            name=name,
            slug=slug,
            tags=[],
            sources=[],
        )

    @classmethod
    def _entity_to_model(cls, entity: RepositoryEntity) -> ScanRepositoriesModel:
        try:
            return ScanRepositoriesModel.model_validate_json(entity.r_details)
        except ValidationError:
            return ScanRepositoriesModel(
                name=entity.r_name,
                slug=entity.r_name,
                tags=[],
                sources=[],
            )