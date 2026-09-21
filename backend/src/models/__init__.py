"""DTOs: modelos que serializan las respuestas hacia el frontend.

Nomenclatura: ``{name}_model.py``.
"""

from .record_model import RecordCreate, RecordOut

__all__ = ["RecordCreate", "RecordOut"]