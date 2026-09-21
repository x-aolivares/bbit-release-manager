"""Entrypoint del monolito: arma la app FastAPI y la dependecias.

- ``create_app(settings)``: fabrica la app (usada por uvicorn y por tests).
- ``run()``: arranca uvicorn (script ``bbit-web``).
"""

from __future__ import annotations

import logging

from fastapi import FastAPI

from .config import Settings
from .controllers import router
from .wiring import build_dependencies


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    _setup_logging()
    container = build_dependencies(settings)

    app = FastAPI(
        title="BBit Release Manager API",
        version="0.1.0",
        description="Monolito: procesamiento de la informacion de los repositorios",
    )
    app.state.container = container
    app.include_router(router)
    return app


app = create_app()


def run() -> None:
    import uvicorn

    settings = app.state.container["settings"]
    uvicorn.run(app, host="127.0.0.1", port=8000)