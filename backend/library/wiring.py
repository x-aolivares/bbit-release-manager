"""Ensamblado de dependencias (composition root) del monolito.

Hace el wiring entre adapters, logics, commands y los controllers, usando
la config parametrizable. Exponer esto aparte del ``handler`` permite a los
tests reemplazar piezas (ej: sqlite en tmp) sin levantar la app.
"""

from __future__ import annotations

from backend.src.adapter import (
    AuthenticationRepository,
    BitbucketService,
    ExecutionPoolService,
    QueryService,
    RecordsRepository,
    RepositoriesRepository,
    RequestHistoryRepository,
)
from backend.src.commands import SaveRecordCommand, ScanRepositoriesCommand, SessionCommand
from backend.src.logics import RecordLogic, ScanRepositoriesLogic, SessionLogic
from .config import Settings


def build_dependencies(settings: Settings | None = None) -> dict:
    """Construye y devuelve las piezas del monolito.

    Cada repositorio queda asociado a su schema sqlite (un archivo ``.db``
    por dominio) y se memoiza en el dict para compartir el pool de ejecucion.
    """
    cfg = settings or Settings.from_env()
    repository = RecordsRepository(cfg.schemas["bbit_record"])
    repository.init()

    repositories_repository = RepositoriesRepository(cfg.schemas["bbit_release"])
    repositories_repository.init()
    request_history_repository = RequestHistoryRepository(cfg.schemas["bbit_release"])
    request_history_repository.init()

    authentication_repository = AuthenticationRepository(cfg.schemas["bbit_authentication"])
    authentication_repository.init()

    pool = ExecutionPoolService(cfg)
    query_service = QueryService(cfg)
    bitbucket_service = BitbucketService(cfg)

    logic = RecordLogic(repository)
    scan_logic = ScanRepositoriesLogic(
        repositories_repository=repositories_repository,
        request_history_repository=request_history_repository,
        bitbucket_service=bitbucket_service,
    )
    session_logic = SessionLogic(authentication_repository)

    return {
        "settings": cfg,
        "repository": repository,
        "repositories_repository": repositories_repository,
        "request_history_repository": request_history_repository,
        "authentication_repository": authentication_repository,
        "pool": pool,
        "query_service": query_service,
        "bitbucket_service": bitbucket_service,
        "logic": logic,
        "scan_logic": scan_logic,
        "save_record_command": SaveRecordCommand(logic, pool),
        "scan_repositories_command": ScanRepositoriesCommand(scan_logic),
        "session_command": SessionCommand(session_logic),
    }