# Route Plan — v23 · Rate limiting de Bitbucket: reducir 429 y mejorar resiliencia

## Objetivo

Mejorar la gestión de rate limiting de la API de Bitbucket Cloud para evitar
errores 429 que causan fallos en escaneos de múltiples repositorios. El problema
ocurre porque se lanzan múltiples hilos en paralelo (MAX_WORKERS=8) y cada
_repo_scan hace 5-6 llamadas API, generando 200+ solicitudes concurrentes.

## Contexto

- `BitbucketClient._request()` tiene3 reintentos con backoff exponencial
  (2s, 4s, 8s) y usa el header `Retry-After` si está presente.
- El scan endpoint (`repos.py:431-438`) lanzaThreadPoolExecutor con
  MAX_WORKERS=8, procesando todos los repos en paralelo.
- Cada `_repo_scan` ejecuta: find_pr, commit_for_branch, has_commits_ahead,
  commits_behind, tags_on_commit, etc. (5-6 llamadas API por repo).
- Con 50+ repos: 50 × 8 threads × 5 llamadas = potencialmente 2000+ solicitudes
  simultáneas, excediendo el rate limit de Bitbucket (documentado: ~60 req/min
  para repos con pocos colaboradores).
- Los logs muestran múltiples 429 en intentos 2/3 y 3/3, con retardos de 4s y 8s
  que no son suficientes para recuperarse.

## Cambios propuestos

### 1. Rate limiter global con token bucket

Agregar un **semáforo de rate limiting** que controle el número máximo de
solicitudes concurrentes a Bitbucket. En lugar de confiar solo en reintentos,
evitar saturar la API desde el principio.

**Opción elegida: `threading.Semaphore` + delay entre requests**

- Crear un `Semaphore` configurable (default: 4 requests concurrentes)
- Agregar un delay mínimo entre requests (default: 0.25s = ~240 req/min)
- Esto limita la tasa real sin bloquear innecesariamente

### 2. Aumentar reintentos y mejorar backoff

- `MAX_RETRIES`: 3 → 5
- `RETRY_BASE_DELAY`: 2.0 → 1.0 (más agresivo al inicio, más conservador después)
- Backoff: `1s → 2s → 4s → 8s → 16s` (respetando Retry-After siempre)

### 3. Reducir paralelismo en scan

- `MAX_WORKERS`: 8 → 4 (reduce la presión concurrente)
- Agregar delay entre submits en el scan loop (no lanzar todos de golpe)

### 4. Respuesta inteligente a 429

- Si recibimos 429, incrementar el delay base para requests futuros
- Trackear el último 429 timestamp para evitar saturar inmediatamente después

## Criterios de aceptación

1. Escaneo de 50+ repos **no** falla por 429 después de la corrección.
2. Los reintentos usan backoff exponencial correcto con Retry-After.
3. El rate limiter global limita requests concurrentes a ≤ 4.
4. Tests existentes pasan sin cambios significativos.
5. Performance no degrada significativamente (scan de 50 repos < 60s).

## Alcance

| Archivo | Cambio |
|---|---|
| `route-map/route-plan-v23.md` | este plan |
| `bbit_release/bitbucket/client.py` | Rate limiter global, más reintentos, backoff mejorado |
| `bbit_release/web/api/repos.py` | Reducir MAX_WORKERS, agregar delay entre submits |
| `tests/test_bitbucket_client.py` | Tests de rate limiting y reintentos |
| `pyproject.toml` | Versión 0.18.2 |

## Orden de implementación

1. Agregar rate limiter global en `client.py` (semáforo + delay).
2. Actualizar constantes MAX_RETRIES y RETRY_BASE_DELAY.
3. Mejorar lógica de backoff en `_request()` para respetar mejor Retry-After.
4. Reducir MAX_WORKERS en `repos.py` y agregar delay entre submits.
5. Tests de rate limiting.
6. Versión 0.18.1 → 0.18.2, commit `fix:` + push.

## Verificación

- `python -m pytest tests/ -q` verde.
- Verificar que el rate limiter se activa con logs de debug.
- Escaneo real de repos sin errores 429.

## Nota de versión

Bug fix de rate limiting → `0.18.1 → 0.18.2`, commit `fix:`.

## Estado

- [x] Rate limiter global en client.py
- [x] Más reintentos y backoff mejorado
- [x] Reducir MAX_WORKERS en repos.py
- [x] Tests de rate limiting
- [x] Version + commit + push

Estado: implementado en v0.18.2