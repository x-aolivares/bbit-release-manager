"""DTOs: modelos que serializan las respuestas hacia el frontend.

Nomenclatura: ``{name}_model.py``.
"""

from .record_model import RecordCreate, RecordOut
from .scan_repositories_model import (
    ScanRepositoriesModel,
    ScanRepositoriesModelCreate,
    ScanRepositoriesResponse,
)

__all__ = [
    "RecordCreate",
    "RecordOut",
    "ScanRepositoriesModel",
    "ScanRepositoriesModelCreate",
    "ScanRepositoriesResponse",
]