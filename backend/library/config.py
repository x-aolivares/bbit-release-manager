"""Configuracion parametrizable del backend.

La concurrencia (multihilos / multiproceso) y la persistencia se controlan
desde variables de entorno para que el deploy pueda ajustarlas sin tocar
codigo:

- ``BBIT_DB_DIR``: directorio base de los schemas sqlite (default ``data/``).
- ``BBIT_DB_<SCHEMA>_PATH``: override de la ruta de un schema puntual
  (ej: ``BBIT_DB_PROFILE_PATH``).
- ``BBIT_MAX_WORKERS``: worker count para tareas paralelas.
- ``BBIT_WORKER_MODE``: ``thread`` (ThreadPoolExecutor) o ``process``
  (ProcessPoolExecutor).

Persistencia por dominios: cada schema es un archivo ``.db`` con el MISMO
nombre del schema (``bbit_record.db``), para que los locks de sqlite sean
independientes por dominio y la notacion ``schema.tabla`` sea directa.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

_DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "data"

#: Dominios de persistencia: el schema ES el nombre del archivo (``bbit_x.db``).
DB_SCHEMAS: tuple[str, ...] = (
    "bbit_record",
    "bbit_profile",
    "bbit_transaction",
    "bbit_authentication",
)


def _schema_env(schema: str) -> str:
    """Env var de override para un schema (``bbit_record`` -> ``BBIT_DB_RECORD_PATH``)."""
    return f"BBIT_DB_{schema.removeprefix('bbit_').upper()}_PATH"


@dataclass(frozen=True, slots=True)
class Settings:
    db_dir: Path = _DEFAULT_DATA_DIR
    max_workers: int = 4
    worker_mode: str = "thread"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            db_dir=Path(os.environ.get("BBIT_DB_DIR", str(_DEFAULT_DATA_DIR))),
            max_workers=int(os.environ.get("BBIT_MAX_WORKERS", "4")),
            worker_mode=os.environ.get("BBIT_WORKER_MODE", "thread").strip().lower(),
        )

    @property
    def schemas(self) -> Mapping[str, Path]:
        """Ruta de cada schema de persistencia (override por env).

        El alias sqlite del schema coincide con el nombre del archivo:
        ``bbit_transaction`` vive en ``data/bbit_transaction.db``.
        """
        return {
            schema: Path(os.environ.get(_schema_env(schema), str(self.db_dir / f"{schema}.db")))
            for schema in DB_SCHEMAS
        }