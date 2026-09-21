"""Entrypoint del monolito: arma la app FastAPI y la dependencias.

- ``create_app(settings)``: fabrica la app (usada por uvicorn y por tests).
- ``run()``: arranca backend (uvicorn --reload) + frontend (ng serve) juntos.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

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


def _terminate(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def run(
    host: str = "127.0.0.1",
    port: int = 8000,
    front_port: int = 4200,
) -> None:
    """Dev: backend (uvicorn --reload) + frontend (ng serve) en paralelo.

    El frontend corre en su propio puerto (dev server con HMR) y el proxy de
    angular (``frontend/proxy.conf.json``) reenvía ``/api`` al backend.
    """
    _setup_logging()
    root = Path(__file__).resolve().parent.parent
    frontend = root / "frontend"

    npm = shutil.which("npm")
    if not npm:
        logging.error("npm no está instalado: no se puede levantar el frontend.")
        sys.exit(1)

    if not (frontend / "package.json").exists():
        logging.error("No se encontró frontend/package.json (corré npm ci en frontend/).")
        sys.exit(1)

    logging.info("API  -> http://%s:%s (uvicorn --reload)", host, port)
    logging.info("SPA  -> http://localhost:%s (ng serve, HMR)", front_port)
    logging.info("Editá backend/ o frontend/src/ y los cambios se reflejan al instante.")

    backend = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn",
            "backend.handler:app",
            "--host", host,
            "--port", str(port),
            "--reload",
            "--reload-dir", str(root / "backend"),
        ],
        cwd=str(root),
    )
    front = subprocess.Popen(
        [npm, "run", "start"],
        cwd=str(frontend),
        shell=(os.name == "nt"),
    )

    try:
        while True:
            time.sleep(1)
            if front.poll() is not None:
                logging.warning("Angular dev server salió inesperadamente.")
                break
            if backend.poll() is not None:
                logging.warning("Backend (uvicorn) salió inesperadamente.")
                break
    except KeyboardInterrupt:
        logging.info("Deteniendo dev servers...")
    finally:
        _terminate(front)
        _terminate(backend)
        logging.info("Dev servers detenidos")