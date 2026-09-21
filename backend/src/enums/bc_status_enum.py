"""Estados controlados de respuesta del backend (códigos ``BBIT-*``).

Convención: cada miembro lleva un código estable, una descripción
genérica y el HTTP status asociado. El ``StatusDTO`` de los responses
se mapea 1:1 desde estos miembros.
"""

from __future__ import annotations

from enum import Enum


class BCStatusEnum(Enum):
    """Estados del backend: código ``BBIT-*`` + descripción + HTTP status."""

    OK = ("BBIT-000", "todos los procesos se ejecutaron correctamente", 200)
    INTERNAL_ERROR = ("BBIT-999", "ocurrió un error inesperado en el servidor", 500)

    def __init__(self, code: str, description: str, http_status: int) -> None:
        self.code = code
        self.description = description
        self.httpStatus = http_status