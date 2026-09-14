"""FastAPI application assembly for the BBit web UI.

Phase 1: registers the API routers and serves the compiled Angular SPA.
In dev mode (``bbit web --dev``) the SPA is served by ``ng serve`` with a
proxy for ``/api``; in that mode the SPA catch-all only matters if a build
exists.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from starlette.middleware.base import BaseHTTPMiddleware

from .._version import read_version
from .api import health, repos, ssm

FRONTEND_DIST = (
    Path(__file__).resolve().parent.parent.parent / "frontend" / "dist" / "browser"
)


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
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


_SENSITIVE_KEY = re.compile(
    r"(?i)(token|password|passwd|secret|credential|app_password|apikey|api_key)"
)


def _redact(payload):
    """Enmascara valores de claves sensibles (credenciales) antes de loguear."""
    if isinstance(payload, dict):
        return {k: _redact(v) if not _SENSITIVE_KEY.search(k) else "***" for k, v in payload.items()}
    if isinstance(payload, list):
        return [_redact(v) for v in payload]
    return payload


async def _log_request_body(request, call_next):
    """Middleware de debug: imprime method/path/query y el JSON body.

    Loguea todo `/api/*` (GET incluidos, con la query completa) y el body de
    POST/PUT con redacción de credenciales, para revisar qué manda el frontend
    al backend al hacer click en "Obtener repos".
    """
    web_log = logging.getLogger("bbit.web")
    if request.url.path.startswith("/api/"):
        desc = f"{request.method} {request.url.path}"
        if request.url.query:
            desc += f"?{request.url.query}"
        try:
            raw = await request.body()
        except Exception:
            raw = b""
        if raw:
            try:
                body = _redact(json.loads(raw))
                desc += f" body={json.dumps(body, ensure_ascii=False)}"
            except Exception:
                desc += f" body=<no-json {len(raw)} bytes>"
        web_log.info("HTTP %s", desc)
    return await call_next(request)


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
    _setup_logging()
    app = FastAPI(
        title="BBit Release Manager",
        description="Revisión de ramas y parámetros SSM contra la API de Bitbucket",
        version=read_version(),
    )
    app.include_router(health.router)
    app.include_router(repos.router)
    app.include_router(ssm.router)
    app.add_middleware(BaseHTTPMiddleware, dispatch=_log_request_body)
    if FRONTEND_DIST.is_dir():
        _serve_spa(app)
    else:
        _serve_unavailable(app)
    return app


app = create_app()