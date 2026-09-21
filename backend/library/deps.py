"""Inyeccion de dependencias para los controllers (FastAPI Depends).

El container se guarda en ``app.state.deps`` por el ``handler.create_app``;
asi los tests pueden montar una app con su propio container (ej: sqlite en
tmp) sin depender de estado global.
"""

from __future__ import annotations

from fastapi import Request

from backend.src.commands import SaveRecordCommand


def get_container(request: Request) -> dict:
    container = getattr(request.app.state, "container", None)
    if container is None:
        raise RuntimeError("handler.create_app() no inicializo app.state.container")
    return container


def get_save_record_command(request: Request) -> SaveRecordCommand:
    return get_container(request)["save_record_command"]