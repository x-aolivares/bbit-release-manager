"""Controllers: endpoints que se le disponibilizan al frontend.

Nomenclatura: ``{name}_controller.py``.
"""

from .records_controller import router as records_router
from .repositories_controller import router as repositories_router

routers = [records_router, repositories_router]

__all__ = ["records_router", "repositories_router", "routers"]