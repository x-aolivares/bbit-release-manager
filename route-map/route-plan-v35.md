# Route Plan — v35 · Auditoría `service_call`: raw de respuestas HTTP

## Objetivo

Guardar en SQLite la respuesta **cruda** de todos los endpoints externos
(Bitbucket Cloud y CircleCI) tal como la devolvieron, para poder reprocesar y
debuguear sin re-consultar los servicios.

## Contexto

Hoy `request.rq_details` almacena el payload **ya procesado** del dominio
(repos normalizados, params SSM resueltos, scan agregado). El raw de cada
`httpx.Response` se descarta. Ambos clientes pasan por un único `_request`
(`bitbucket/client.py:126`, `circleci/client.py:81`), que llama `resp.json()`
y no conserva `status_code`, `url`, `params`, duración ni el body.

El `request` no puede guardar el raw: su `rq_details` es el payload que el hit
del cache devuelve tal cual. Cambiarlo rompería `/api/repos`, `/api/scan`, etc.

Solución: tabla nueva de auditoría `service_call` (append-only), una fila por
llamada HTTP real, capturada vía **event hooks de httpx** en ambos clientes sin
tocar los ~30 métodos de dominio.

## Cambios propuestos

1. `bbit_release/cache.py`:
   - Nueva tabla `service_call` (`sc_id`, `sc_source`, `sc_method`, `sc_url`,
     `sc_params` JSON, `sc_status`, `sc_duration_ms`, `sc_response` raw,
     `sc_created_at`).
   - Índice por `(sc_source, sc_created_at)`.
   - Método `record_service_call(...)` con `_insert`.

2. `bbit_release/bitbucket/client.py` y `circleci/client.py`:
   - Parámetro opcional `recorder: Callable[[dict], None] | None = None`.
   - `event_hooks={"request": [...], "response": [...]}` en el `httpx.Client`:
     `request` marca `time.perf_counter()`; `response` arma el dict
     (`source`, `method`, `url` path+query, `params`, `status`,
     `duration_ms`, `response` crudo `resp.text`) y llama al `recorder`.
   - Con `recorder=None` (default) todo sigue igual → retrocompatible.
   - El hook nunca lanza: envuelve el `recorder` en try/except.

3. `bbit_release/web/session.py` y `bbit_release/web/api/repos.py`:
   - Pasar `recorder` a los clientes que crean (sesión Bitbucket y CircleCI),
     escribiendo vía `get_cache().record_service_call(...)`.

## Criterios de aceptación

- [ ] Cada llamada HTTP externa genera una fila en `service_call`.
- [ ] `service_call.sc_response` contiene el raw exacto que devolvió el servicio.
- [ ] `request` (cache) no cambia su contrato: hits y payloads intactos.
- [ ] Con `recorder=None`, clientes mantienen comportamiento actual.
- [ ] El hook no crashea el flujo si el recorder falla.
- [ ] `pytest tests/ -q` verde.

## Alcance

| Archivo | Cambio |
|---|---|
| `bbit_release/cache.py` | Tabla `service_call` + `record_service_call()` |
| `bbit_release/bitbucket/client.py` | Hook httpx + `recorder` opcional |
| `bbit_release/circleci/client.py` | Hook httpx + `recorder` opcional |
| `bbit_release/web/session.py` | Recorder → cache |
| `bbit_release/web/api/repos.py` | Recorder → cache (CircleCI) |
| `tests/test_cache.py` | Test tabla/método nuevo |
| `tests/test_bitbucket_client.py` | Test hook captura raw |
| `tests/test_circleci_client.py` | Test hook captura raw |
| `pyproject.toml` | Bump a `0.24.0` |

## Orden de implementación

1. Tabla + método en `cache.py`.
2. Hooks en ambos clientes + recorder.
3. Conexión web (session + repos).
4. Tests.
5. Verificación + bump + commit + push.

## Verificación

```bash
python -m pytest tests/ -q
```

## Nota de versión

Nueva funcionalidad mayor (auditoría de respuestas crudas) → `0.23.2` → `0.24.0`,
commit `feat:`. `data/cache.db` queda fuera de git; las tablas se crean en el
primer arranque (CREATE TABLE IF NOT EXISTS).

## Estado

- [x] Tabla `service_call` + método en `cache.py`
- [x] Hooks httpx + recorder en Bitbucket y CircleCI
- [x] Conexión web a los clientes
- [x] Tests nuevos pasan (154 total)
- [x] Bump `0.24.0`

Estado: implementado