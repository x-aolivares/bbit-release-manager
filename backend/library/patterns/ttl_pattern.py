"""Pattern de vigencia por TTL: valida si un timestamp sigue fresco.

Recibe el TTL del servicio que lo consulta (un miembro de ``RequestTtl``): el
mismo patrón sirve para cualquier flujo que cachee contra ``request_history``
(los minutos vienen del enum, una sola fuente).
"""

from __future__ import annotations

from datetime import datetime, timezone

from backend.library.enums import RequestTtlEnum


class Ttl:
    @staticmethod
    def is_fresh(updated_at: str, ttl: RequestTtlEnum) -> bool:
        """Devuelve ``True`` si ``updated_at`` no superó el ``ttl`` del servicio."""
        try:
            last = datetime.strptime(updated_at, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return False
        elapsed_minutes = (datetime.now(timezone.utc).replace(tzinfo=None) - last).total_seconds() / 60
        return elapsed_minutes < ttl.value