"""Ensamblado de dependencias (composition root) del monolito.

Hace el wiring entre adapters, logics, commands y los controllers, usando
la config parametrizable. Exponer esto aparte del ``handler`` permite a los
tests reemplazar piezas (ej: sqlite en tmp) sin levantar la app.
"""

from __future__ import annotations

from .adapter.repositories.records_repository import RecordsRepository
from .adapter.services.execution_pool_service import ExecutionPoolService
from .commands.save_record_command import SaveRecordCommand
from .config import Settings
from .logics.record_logic import RecordLogic


def build_dependencies(settings: Settings | None = None) -> dict:
    """Construye y devuelve las piezas del monolito.

    Cada repositorio queda asociado a su schema sqlite (un archivo ``.db``
    por dominio) y se memoiza en el dict para compartir el pool de ejecucion.
    """
    cfg = settings or Settings.from_env()
    repository = RecordsRepository(cfg.schemas["record"])
    repository.init()

    pool = ExecutionPoolService(cfg)
    logic = RecordLogic(repository)

    return {
        "settings": cfg,
        "repository": repository,
        "pool": pool,
        "logic": logic,
        "save_record_command": SaveRecordCommand(logic, pool),
    }