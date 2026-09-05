# Route Plan — v32 · Normalización del caché

## Objetivo

Reemplazar el `ReleaseCache` plano (5 tablas `key → JSON`) por un esquema
normalizado de 4 tablas: `init_sesion`, `service_provider`, `request_type` y
`request`. El nuevo modelo agrega:

- Registro de sesiones de consulta con configuración dinámica (`is_details`).
- Catálogo de proveedores de servicios externos (Bitbucket, CircleCi, ...).
- Catálogo de tipos de request con **TTL por servicio** (`rt_ttl_seconds`).
- Historial de requests con estado (`PENDING / SUCCESS / FAILED`) y payload.

## Contexto

Hoy `bbit_release/cache.py` guarda filas planas con clave compuesta
`{origin}|{destination}|{prefijos}|{excluidos}` en 5 tablas:
`repo_cache`, `scan_cache`, `diff_cache`, `master_cache`, `branch_repos`.
Sin TTL: el dato vale hasta que se invalida (o el proceso se reinicia).

El usuario definió un esquema normalizado que además persiste **histórico**:
cada consulta del front registra una `init_sesion`, cada endpoint externo es un
`request_type` con TTL propio, y cada ejecución guarda un `request` con payload
y estado. Esto permite auditar qué se consultó, cuándo, contra qué proveedor y
con qué resultado.

## Cambios propuestos

### 1. Nuevo schema SQLite (`bbit_release/cache.py`)

Traducción del DDL MySQL a SQLite (JSON→TEXT, `AUTO_INCREMENT`→`INTEGER PK
AUTOINCREMENT`, sin `ENGINE`/`COLLATE`; `ON UPDATE CURRENT_TIMESTAMP` se
maneja desde Python).

- `init_sesion(is_id, is_source, is_target, is_details, is_created_at, is_updated_at)` + índice `(is_source, is_target)`.
- `service_provider(sp_id, sp_name UNIQUE, sp_details)` (seed: Bitbucket, CircleCi).
- `request_type(rt_id, rt_service_provider_id FK, rt_name UNIQUE, rt_ttl_seconds DEFAULT 300, rt_service_url, rt_details, rt_created_at, rt_updated_at)` + índice `(rt_service_provider_id)`.
- `request(rq_id, is_id FK, rt_id FK, rq_status DEFAULT 'PENDING', rq_details, rq_created_at, rq_updated_at)` + índices `(rt_id, rq_created_at DESC)` y `(is_id)`.
- Seed de `request_type` por operación: `get_user_repositories`, `get_branch_repositories`, `scan_release`, `diff_ssm`, `get_master_params`.

> **Corrección del DDL entregado**: los índices de `request` referencian
> `rq_request_type_id` y `rq_init_sesion_id`, columnas que no existen (las
> columnas son `rt_id` y `is_id`). Se corrigen a (`rt_id`, `rq_created_at DESC`)
> y (`is_id`).

### 2. API del nuevo `ReleaseCache`

- `create_session(source, target, details)` / `get_session(source, target, details_fingerprint)`: upsert lógico de `init_sesion`.
- `add_request(session_id, request_type_id, payload, status)`: inserta un `request`.
- `latest_success(rt_id, is_id)`: request SUCCESS más reciente sin expirar (TTL).
- `is_expired(created_at, ttl_seconds)`: compara `now - created_at > ttl`.
- `invalidate(...)` / `invalidate_all()`: borran requests y sesiones (no el catálogo).
- `get_rt(name)` / `get_provider(name)`: helpers del catálogo.

### 3. Integración en `web/api/repos.py`

Cada operación cacheada pasa a usar el flujo normalizado:

| Operación actual | `request_type` | `init_sesion` (source→target) |
|---|---|---|
| `list_repos` / `_all_repos_cached` | `get_user_repositories` | `""→all` |
| `_branch_repos_cached` | `get_branch_repositories` | `origin→destination` |
| `scan` | `scan_release` | `origin→destination` |
| `diff` | `diff_ssm` | `origin→destination` |
| `_resolve_master` | `get_master_params` | `slug→destination` |

El `is_details` de cada sesión captura la configuración de la consulta
(`project_prefixes`, `exclude`, `deploy_prefixes`, `ssm_prefixes`, `force`).

### 4. Tests

- `tests/test_cache.py`: migrado al nuevo modelo (seed, TTL, historial,
  invalidación, thread-safety).
- `tests/test_web_api.py`: ajustar fixture de cache al nuevo schema; los tests
  de `cached` y `force` siguen validando el mismo contrato HTTP.

## Criterios de aceptación

- [x] Correr `pytest tests/ -q` verde.
- [x] `/api/repos`, `/api/scan` y `/api/diff` devuelven `cached:true` en llamadas consecutivas y `false` con `force=1`.
- [x] El seed crea `service_provider` (Bitbucket, CircleCi) y sus `request_type`.
- [x] Un `request` SUCCESS expira según `rt_ttl_seconds` del tipo.
- [x] `invalidate_all` limpia requests/sesiones pero conserva el catálogo.
- [x] `docs/cache-example.md` refleja el nuevo esquema.

## Alcance

| Archivo | Cambio |
|---|---|
| `bbit_release/cache.py` | Reescritura completa al esquema normalizado |
| `bbit_release/web/api/repos.py` | Usar el nuevo flujo sesión + request |
| `tests/test_cache.py` | Migrar al nuevo modelo |
| `tests/test_web_api.py` | Ajustar fixture de cache |
| `docs/cache-example.md` | Actualizar ejemplos al nuevo schema |
| `pyproject.toml` | Bump a `0.23.0` |

## Orden de implementación

1. Reescribir `cache.py` (schema + API + seed).
2. Actualizar `web/api/repos.py`.
3. Migrar `test_cache.py` y ajustar `test_web_api.py`.
4. Actualizar `docs/cache-example.md`.
5. `pytest tests/ -q` verde.
6. Bump versión y commit.

## Verificación

```bash
python -m pytest tests/ -q
```

## Nota de versión

Ajuste significativo (reescritura del layer de persistencia, reto-compatible a
nivel HTTP) → `0.22.0` → `0.23.0`, commit `refactor:`.

## Estado

- [x] Schema nuevo en `cache.py`
- [x] Seed de catálogo
- [x] Integración en `web/api/repos.py`
- [x] Tests migrados y verdes
- [x] `docs/cache-example.md` actualizado
- [x] Versión bumpeda (0.23.0)

Estado: implementado