"""Logica de negocio: procesamiento de informacion y llamadas a repositorios."""

from .record_logic import RecordLogic
from .scan_repositories_logic import ScanRepositoriesLogic

__all__ = ["RecordLogic", "ScanRepositoriesLogic"]