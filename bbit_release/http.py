"""Infraestructura HTTP compartida entre clientes externos.

Centraliza el rate limiter global (token bucket) que los clientes de
Bitbucket y CircleCI comparten para no golpear las APIs con más
concurrencia de la permitida, y las constantes de retry por 429.
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

    Compartido por todos los clientes HTTP del pipeline: acota tanto el
    número de requests simultáneos (semáforo) como la frecuencia mínima
    entre requests (min_interval), de modo que sumar hilos de scan/diff
    no vuelque el rate limit de las APIs.
    """

    def __init__(self, max_concurrent: int = 4, min_interval: float = 0.25):
        self._semaphore = threading.Semaphore(max_concurrent)
        self._min_interval = min_interval
        self._last_request_time = 0.0
        self._lock = threading.Lock()

    def acquire(self) -> None:
        self._semaphore.acquire()
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_request_time
            if elapsed < self._min_interval:
                time.sleep(self._min_interval - elapsed)
            self._last_request_time = time.monotonic()

    def release(self) -> None:
        self._semaphore.release()


# Rate limiter global compartido por todas las instancias de clientes.
# Reducido a 4 para evitar rate limits de Bitbucket Cloud (429 Too Many Requests)
_global = RateLimiter(max_concurrent=4, min_interval=0.25)


def get_global_rate_limiter() -> RateLimiter:
    """Devuelve el rate limiter global compartido por los clientes."""
    return _global