# Route Plan — v24 · Caché de repos y branches: eliminar queries repetidas a Bitbucket

## Objetivo

Implementar caché efectiva para `repos_with_branch` y `list_repos` en los
endpoints `/api/scan` y `/api/diff`, evitando que cada request re-queryee
TODOS los repos y branches desde la API de Bitbucket. Actualmente solo el
endpoint `/api/repos` usa `_REPO_CACHE`.

## Contexto

- Los logs muestran que cada llamada a `/api/repos`, `/api/scan` o `/api/diff`
  ejecuta `list_repos` (8 páginas × 100 repos) + `resolve_branch` (1 request
  por repo = ~150 requests).
- `_REPO_CACHE` (línea 30 de `repos.py`) solo se usa en el endpoint `list_repos`
  (líneas 272-296).
- Los endpoints `scan` (línea 433) y `diff` (líneas 695-696) llaman directamente
  a `data.client.repos_with_branch()` y `data.client.list_repos()` sin caché.
- Resultado: ~300 requests a Bitbucket por cada request web, incluso si el
  usuario repite la misma consulta.

## Cambios propuestos

### 1. Agregar `_BRANCH_REPOS_CACHE` para `repos_with_branch`

Crear una caché específica para resultados de `repos_with_branch`, ya que es
la operación más costosa (list_repos + N resolve_branch).

```python
_BRANCH_REPOS_CACHE: dict[str, list] = {}

def _branch_cache_key(origin: str, prefs: list[str] | None) -> str:
    p = ",".join(prefs or [])
    return f"{active_session_id()}|{origin}|{p}"
```

### 2. Usar caché en endpoint `scan`

Modificar `scan()` para consultar `_BRANCH_REPOS_CACHE` antes de llamar a
`repos_with_branch()`.

### 3. Usar caché en endpoint `diff`

Modificar `diff()` para consultar `_BRANCH_REPOS_CACHE` y `_REPO_CACHE`
antes de hacer llamadas a la API.

### 4. TTL de 5 minutos

Agregar un timestamp de creación a cada entrada de caché para expirar datos
stale después de 5 minutos.

## Criterios de aceptación

1. La segunda llamada a `/api/scan` con el mismo origin returns `"cached": true`.
2. La segunda llamada a `/api/diff` no re-queryea repos de Bitbucket.
3. La caché expira después de 5 minutos.
4. Tests existentes pasan sin cambios.
5. Request inicial sigue funcionando correctamente.

## Alcance

| Archivo | Cambio |
|---|---|
| `route-map/route-plan-v24.md` | este plan |
| `bbit_release/web/api/repos.py` | Agregar `_BRANCH_REPOS_CACHE`, TTL, usar caché en scan/diff |
| `tests/test_repos_cache.py` | Tests de caché |
| `pyproject.toml` | Versión 0.18.3 |

## Orden de implementación

1. Agregar `_BRANCH_REPOS_CACHE` y helper `_branch_cache_key()`.
2. Agregar TTL de 5 minutos a las entradas de caché.
3. Modificar endpoint `scan` para usar caché de branches.
4. Modificar endpoint `diff` para usar caché de branches y repos.
5. Tests de caché.
6. Versión 0.18.2 → 0.18.3, commit `fix:` + push.

## Verificación

- `python -m pytest tests/ -q` verde.
- Verificar que la segunda llamada a `/api/scan` retorna `"cached": true`.
- Verificar que los logs no muestran queries repetidas a Bitbucket.

## Nota de versión

Bug fix de caché → `0.18.2 → 0.18.3`, commit `fix:`.

## Estado

- [x] Agregar `_BRANCH_REPOS_CACHE` y helpers
- [x] Agregar TTL a cachés
- [x] Modificar endpoint scan
- [x] Modificar endpoint diff
- [x] Tests de caché
- [x] Version + commit + push

Estado: implementado en v0.18.3
