"""Configuracion parametrizable del backend.

La concurrencia (multihilos / multiproceso) y la persistencia se controlan
desde variables de entorno para que el deploy pueda ajustarlas sin tocar
codigo:

- ``BBIT_DB_PATH``: ruta del archivo sqlite (default ``data/bbit.db``).
- ``BBIT_MAX_WORKERS``: worker count para tareas paralelas.
- ``BBIT_WORKER_MODE``: ``thread`` (ThreadPoolExecutor) o ``process``
  (ProcessPoolExecutor).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

_DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "bbit.db"


@dataclass(frozen=True, slots=True)
class Settings:
    db_path: Path = _DEFAULT_DB_PATH
    max_workers: int = 4
    worker_mode: str = "thread"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            db_path=Path(os.environ.get("BBIT_DB_PATH", str(_DEFAULT_DB_PATH))),
            max_workers=int(os.environ.get("BBIT_MAX_WORKERS", "4")),
            worker_mode=os.environ.get("BBIT_WORKER_MODE", "thread").strip().lower(),
        )