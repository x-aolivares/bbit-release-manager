"""uvicorn launcher for the BBit web UI.

Two modes:
- ``run``: serve the compiled SPA + API on the given port (prod local).
- ``run_dev``: spawn ``ng serve`` (HMR) plus ``uvicorn --reload`` on the API.
  Editing ``src/`` or ``frontend/src/`` reflects instantly, no reinstall.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

from ..logger import info, success, warn, die


def _frontend_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "frontend"


def _have_tool(name: str) -> bool:
    return shutil.which(name) is not None


def _terminate(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def run(host: str = "127.0.0.1", port: int = 8000, open_browser: bool = True) -> None:
    """Serve the compiled SPA + API (blocking)."""
    import uvicorn

    from .main import app

    url = f"http://{host}:{port}"
    info(f"BBit web -> {url}")
    info(f"OpenAPI  -> {url}/openapi.json")
    if open_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=host, port=port)


def run_dev(
    host: str = "127.0.0.1",
    port: int = 8000,
    open_browser: bool = True,
) -> None:
    """Interactive dev: Angular HMR + backend auto-reload, both tracked."""
    frontend = _frontend_root()

    if not (frontend / "package.json").exists():
        die("frontend/ not scaffolded (missing package.json). Run 'bbit setup'.")

    npm = shutil.which("npm")
    if not npm:
        die(
            "npm no está instalado — instalá Node.js (>= 24) para el dev loop "
            "del frontend"
        )

    python = sys.executable
    src_dir = Path(__file__).resolve().parent.parent

    info(f"Dev server API  -> http://{host}:{port} (uvicorn --reload)")
    info("Dev server SPA  -> http://localhost:4200 (ng serve, HMR)")
    info("Editá src/ o frontend/src/ y los cambios se reflejan al instante.")

    backend = subprocess.Popen(
        [
            python, "-m", "uvicorn",
            "bbit_release.web.main:app",
            "--host", host,
            "--port", str(port),
            "--reload",
            "--reload-dir", str(src_dir),
        ],
        cwd=str(src_dir.parent),
    )
    front = subprocess.Popen(
        [npm, "run", "start"],
        cwd=str(frontend),
        shell=(os.name == "nt"),
    )

    if open_browser:
        threading.Timer(2.5, lambda: webbrowser.open("http://localhost:4200")).start()

    try:
        while True:
            time.sleep(1)
            if front.poll() is not None:
                warn("Angular dev server salió inesperadamente.")
                break
            if backend.poll() is not None:
                warn("Backend (uvicorn) salió inesperadamente.")
                break
    except KeyboardInterrupt:
        info("Deteniendo dev servers...")
    finally:
        _terminate(front)
        _terminate(backend)
        success("Dev servers detenidos")


if __name__ == "__main__":
    run()