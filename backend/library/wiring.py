"""Ensamblado de dependencias (composition root) del monolito.

Hace el wiring entre adapters, logics, commands y los controllers, usando
la config parametrizable. Exponer esto aparte del ``handler`` permite a los
tests reemplazar piezas (ej: sqlite en tmp) sin levantar la app.
"""

from __future__ import annotations

from backend.src.adapter import RecordsRepository, ExecutionPoolService, QueryService
from backend.src.commands import SaveRecordCommand
from backend.src.logics import RecordLogic
from .config import Settings


def build_dependencies(settings: Settings | None = None) -> dict:
    """Construye y devuelve las piezas del monolito.

    Cada repositorio queda asociado a su schema sqlite (un archivo ``.db``
    por dominio) y se memoiza en el dict para compartir el pool de ejecucion.
    """
    cfg = settings or Settings.from_env()
    repository = RecordsRepository(cfg.schemas["bbit_record"])
    repository.init()

    pool = ExecutionPoolService(cfg)
    query_service = QueryService(cfg)
    logic = RecordLogic(repository)

    return {
        "settings": cfg,
        "repository": repository,
        "pool": pool,
        "query_service": query_service,
        "logic": logic,
        "save_record_command": SaveRecordCommand(logic, pool),
    }