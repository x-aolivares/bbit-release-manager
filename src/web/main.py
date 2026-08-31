"""FastAPI application assembly for the BBit web UI.

Phase 1: registers the API routers and serves the compiled Angular SPA.
In dev mode (``bbit web --dev``) the SPA is served by ``ng serve`` with a
proxy for ``/api``; in that mode the SPA catch-all only matters if a build
exists.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from .._version import read_version
from .api import health, repos

FRONTEND_DIST = (
    Path(__file__).resolve().parent.parent.parent / "frontend" / "dist" / "browser"
)


def _serve_spa(app: FastAPI) -> None:
    """Serve the compiled Angular app. Registered last so /api and docs win."""
    index = FRONTEND_DIST / "index.html"

    @app.get("/", include_in_schema=False)
    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str = ""):
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not found")
        candidate = FRONTEND_DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(str(candidate))
        return FileResponse(str(index))


def _serve_unavailable(app: FastAPI) -> None:
    """Fallback for environments without an Angular build."""

    @app.get("/", include_in_schema=False)
    @app.get("/{full_path:path}", include_in_schema=False)
    def not_ready(full_path: str = ""):
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not found")
        raise HTTPException(
            status_code=503,
            detail="The Angular frontend build is not available. "
            "Run `bbit web` or `npm run build` in frontend/.",
        )


def create_app() -> FastAPI:
    app = FastAPI(
        title="BBit Release Manager",
        description="Revisión de ramas y parámetros SSM contra la API de Bitbucket",
        version=read_version(),
    )
    app.include_router(health.router)
    app.include_router(repos.router)
    if FRONTEND_DIST.is_dir():
        _serve_spa(app)
    else:
        _serve_unavailable(app)
    return app


app = create_app()