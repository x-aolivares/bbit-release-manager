"""Controllers: endpoints que se le disponibilizan al frontend.

Nomenclatura: ``{name}_controller.py``.
"""

from .records_controller import router

__all__ = ["router"]