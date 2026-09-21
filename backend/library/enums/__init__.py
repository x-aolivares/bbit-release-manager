"""Enums del library: parametros compartidos por la infra del monolito.

Nomenclatura: ``{name}_enum.py``.
"""

from .global_config_enum import GlobalConfigEnum
from .request_ttl_enum import RequestTtlEnum

__all__ = ["GlobalConfigEnum", "RequestTtlEnum"]