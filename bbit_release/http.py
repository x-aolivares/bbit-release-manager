"""Infraestructura HTTP compartida entre clientes externos.

Centraliza los rate limiters por proveedor (token bucket) que los clientes
de Bitbucket y CircleCI usan para no golpear las APIs con más concurrencia
de la permitida, y las constantes de retry por 429.

BBIT-51: cada proveedor tiene SU PROPIO limiter. Un tarball grande de
Bitbucket no demora requests de CircleCI en otro hilo, y levantar N workers
de scan no multiplica el límite real por proveedor. Modo soportado: un solo
proceso uvicorn (workers=1) — el limiter es por proceso; con workers>1 cada
proceso tiene sus propios limiters (sin coordinación entre procesos).
"""

from __future__ import annotations

import threading
import time

# Límites de retry compartidos ante 429 (por servicio).
# Aumentado a 3 para manejar rate limits con backoff exponencial
MAX_RETRIES = 3
RETRY_BASE_DELAY = 0.5  # Reducido: backoff exponencial lo aumentará


class RateLimiter:
    """Token bucket simple para limitar requests concurrentes externos.

    Por proveedor (BBIT-51): acota tanto el número de requests simultáneos
    (semáforo) como la frecuencia mínima entre requests (min_interval), de
    modo que sumar hilos de scan/diff no vuelque el rate limit de la API.
    No compartir slots entre proveedores evita que un request pesado de uno
    demore los de otro.
    """

    def __init__(self, max_concurrent: int = 4, min_interval: float = 0.25):
        self._semaphore = threading.Semaphore(max_concurrent)
        self._min_interval = min_interval
        self._last_request_time = 0.0
        self._lock = threading.Lock()

    def acquire(self, timeout: float | None = None) -> bool:
        """Adquiere un slot; True si lo obtuvo (y ya esperó el intervalo)."""
        if not self._semaphore.acquire(timeout=timeout):
            return False
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_request_time
            if elapsed < self._min_interval:
                time.sleep(self._min_interval - elapsed)
            self._last_request_time = time.monotonic()
        return True

    def release(self) -> None:
        self._semaphore.release()


# Rate limiters independientes por proveedor (BBIT-51): Bitbucket y CircleCI
# no comparten tokens.
_RATE_LIMITERS: dict[str, RateLimiter] = {
    # Bitbucket Cloud: conservador (429 Too Many Requests habitual en /2.0).
    "bitbucket": RateLimiter(max_concurrent=4, min_interval=0.25),
    # CircleCI v2: límites propios, desacoplados de Bitbucket.
    "circleci": RateLimiter(max_concurrent=4, min_interval=0.25),
}


def get_rate_limiter(provider: str) -> RateLimiter:
    """Rate limiter independiente por proveedor (BBIT-51).

    ``provider`` en ``("bitbucket", "circleci")``; un request pesado de un
    proveedor nunca demora los slots del otro.
    """
    return _RATE_LIMITERS[provider]


def get_global_rate_limiter() -> RateLimiter:
    """Rate limiter por proceso del stack (retrocompat, single-worker mode).

    Deprecated: el stack se ejecuta como un único proceso uvicorn (workers=1);
    en ese modo este limiter coincide con el de Bitbucket. Los clientes
    internos NO lo usan más: usan ``get_rate_limiter("bitbucket"|"circleci")``.
    """
    return _RATE_LIMITERS["bitbucket"]