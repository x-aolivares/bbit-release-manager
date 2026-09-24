"""Controllers: endpoints que se le disponibilizan al frontend.

Nomenclatura: ``{name}_controller.py``.
"""

from .records_controller import router as records_router
from .repositories_controller import router as repositories_router
from .session_controller import router as session_router

routers = [records_router, repositories_router, session_router]

__all__ = ["records_router", "repositories_router", "session_router", "routers"]