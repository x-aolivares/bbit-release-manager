"""Servicio de pool de ejecucion parametrizable (multihilos / multiproceso).

El backend debe soportar multihilos, paralelismo y concurrencia, y todo debe
ser parametrizable. Este servicio expone un pool construido según la config:

- ``BBIT_WORKER_MODE=thread``  -> ``ThreadPoolExecutor`` (I/O bound)
- ``BBIT_WORKER_MODE=process`` -> ``ProcessPoolExecutor`` (CPU bound)
- ``BBIT_MAX_WORKERS=N``       -> tamanio del pool

Los controllers solo usan ``.map``/``.submit``; nunca crean pools a mano.

Nomenclatura: los services del adapter terminan en ``_service.py``.
"""

from __future__ import annotations

from concurrent.futures import Executor, ProcessPoolExecutor, ThreadPoolExecutor

from ...config import Settings


class ExecutionPoolService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def build(self) -> Executor:
        if self._settings.worker_mode == "process":
            return ProcessPoolExecutor(max_workers=self._settings.max_workers)
        return ThreadPoolExecutor(max_workers=self._settings.max_workers)