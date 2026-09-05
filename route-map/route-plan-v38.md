# Route Plan — v38 · Request types CircleCI con cache TTL

## Objetivo

Registrar los endpoints de CircleCI en el catálogo `request_type` y cachear
sus respuestas en SQLite con TTL corto, para reducir llamadas a la API (la más
agresiva en rate-limits) dentro de `/scan` y `/tags`.

## Contexto

`request_type` cachea lo que se consulta repetido: hoy solo 5 request types
Bitbucket (`cache.py` `_SEED_REQUEST_TYPES`). CircleCI se consultaba **en
vivo** por repo dentro de `_repo_scan` (`repos.py:449-476`) y `/tags`
(`repos.py:686`), generando muchas llamadas por scan. El usuario pidió
agregarlos al catálogo y cachearlos con TTL corto.

Endpoints productivos hoy (`circleci/client.py`):
- `GET /project/{slug}` → `project_id()`
- `GET /project/{slug}/pipeline` (paginado, branch/tag) → `pipelines()`
- `GET /pipeline/{pipeline_id}/workflow` (paginado) → `workflows()`
- `GET /workflow/{workflow_id}/job` (paginado) → `workflow_jobs()`

## Cambios propuestos

1. `bbit_release/cache.py`:
   - `_CIRCLECI_BASE = "https://circleci.com/api/v2"`.
   - 4 request types nuevos (provider `CircleCi` ya existe):
     `circleci_project` (TTL 3600), `circleci_pipelines` (120),
     `circleci_workflows` (60), `circleci_workflow_jobs` (60).
   - Fachadas `get_*/set_*` por tipo (patrón `get_master`): source=
     slug/pipeline_id/workflow_id, con `{"kind": "branch"|"tag"}` en
     `circleci_pipelines` para no colisionar branch vs tag.
   - Se cachea la **lista final ya paginada** (una fila `request`, no una por página).

2. `bbit_release/circleci/client.py`:
   - Nuevo param `cache` DI (`TYPE_CHECKING` para tipo, como `recorder`).
   - `project_id()`, `pipelines()`, `workflows()`, `workflow_jobs()`:
     hit → payload del cache; miss → HTTP (con `service_call` intacto) → set.
   - `me()` queda sin cache. `deploy_for_tag`/`deploys_for_tags`/`pipeline_id_for_commit`
     se benefician solos reusando pipelines/workflows cacheados.
   - `cache=None` (default) mantiene comportamiento previo.

3. `bbit_release/web/api/repos.py` — `_circleci()` pasa `cache=get_cache()`.

## Criterios de aceptación

- [ ] Los 4 request types CircleCI existen en el seed con su TTL.
- [ ] 2do llamado del mismo recurso pega en SQLite (sin HTTP).
- [ ] branch y tag de `pipelines` no colisionan en el cache.
- [ ] `me()` sigue sin cache.
- [ ] `pytest tests/ -q` verde.

## Alcance

| Archivo | Cambio |
|---|---|
| `bbit_release/cache.py` | Seed `_CIRCLECI_BASE` + 4 request types + fachadas |
| `bbit_release/circleci/client.py` | DI `cache` + hits/misses en 4 métodos |
| `bbit_release/web/api/repos.py` | `_circleci()` con `cache=get_cache()` |
| `tests/test_cache.py` | Request types CircleCI en el seed |
| `tests/test_circleci_client.py` | Tests de cache hit/miss + branch/tag |
| `pyproject.toml` | Bump |

## Orden de implementación

1. Seed de request types + fachadas en cache.py.
2. DI cache en circleci/client.py.
3. `_circleci()` pasa el cache.
4. Tests + bump + commit + push.

## Verificación

```bash
python -m pytest tests/ -q
```

## Nota de versión

Capacidad nueva (cache de CircleCI) aditiva y retrocompatible → `0.26.0` → `0.27.0`, commit `feat:`.

## Estado

- [x] Seed de 4 request types CircleCI + fachadas
- [x] DI `cache` en circleci/client.py
- [x] `_circleci()` con `cache=get_cache()`
- [x] Tests seed + hit/miss + branch/tag (161 tests verdes)
- [x] Bump `0.27.0`

Estado: implementado