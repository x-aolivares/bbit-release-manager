# Route Plan — v40 · Optimización de consultas a APIs externas en el scan `/release`

## Objetivo

Reducir el tiempo de las primeras consultas del scan `/release` (cache frío):
hoy el barrido de ramas + el scan de repos pagan ~2.5-4 min por el rate limiter
global de Bitbucket (~4 req/s) y por llamadas redundantes por repo.

## Contexto

Con ~800 repos de workspace, el filtro de prefijos deja ~300-400 candidatos.
Flujo en frío:

- **Barrido** `repos_with_branch` (client.py): `list_repos` paginado + 1-2
  requests por repo (`resolve_branch` → `has_branch` y, si la rama literal no
  existe, `list_branches`) → ~500-700 requests.
- **Scan por repo** `_repo_scan` (repos.py): `find_pr` (1) + `commit_for_branch`
  (1-4: re-resuelve cuando la rama literal no existe, p.ej. `release/REP-1-V2`)
  + `has_commits_ahead` (1, ya se skipea con PR) + `commits_behind` (1-N páginas
  con `pagelen=100`) + `tags_on_commit` (1-N páginas de tags) + CircleCI.
- Todo encerrado por el `_RateLimiter` global `max_concurrent=4,
  min_interval=0.25` (client.py:51): el `acquire()` espacia a máximo
  ~1 request cada 0.25s global → techo ~4 req/s.

Los request types `get_user_repositories` y `get_branch_repositories` tenían TTL
300s, por lo que el barrido se repetía a los 5 min; y el seed usaba
`INSERT OR IGNORE`, así que cambiar el TTL en `_SEED_REQUEST_TYPES` no
actualizaba DBs existentes.

## Cambios propuestos

1. **Rate limiter balanceado** (`client.py`): `max_concurrent=8`,
   `min_interval=0.125` (~8 req/s, 2x). `MAX_WORKERS=4 → 8` en `client.py` y
   `repos.py`. Riesgo de 429 absorbido por el backoff existente
   (`MAX_RETRIES=5`, `RETRY_BASE_DELAY=1.0`).
2. **Reuso de rama resuelta**: `Repository` gana `resolved_branch: str = ""`.
   `repos_with_branch` lo puebla con el resultado de `resolve_branch`.
   `commit_for_branch(slug, branch, resolved="")` consulta directo el `resolved`
   y evita re-resolver (ahorra 2-3 requests por repo con variante `-V2`).
   El resultado se persiste en la cache `branch_repos` y `_repo_scan` lo pasa.
3. **`commits_behind` en 1 request**: pide `pagelen=1` y lee `size` del
   envelope (total de la consulta); si el servidor no devuelve `size`, paga el
   paginado anterior como fallback.
4. **TTL del barrido**: `get_user_repositories` 300 → 1800s y
   `get_branch_repositories` 300 → 3600s. El seed pasa de `INSERT OR IGNORE` a
   `INSERT ... ON CONFLICT(rt_name) DO UPDATE` para aplicar los TTL nuevos a
   DBs existentes.
5. **`_SUBMIT_DELAY` eliminado**: el rate limiter ya espacia; se saca el
   `time.sleep(0.1)` por submit en el scan loop (ahorra ~3.4s por scan).

No se toca `find_pr`/`has_commits_ahead`/`tags_on_commit`: `find_pr` ya skipea
`has_commits_ahead` cuando hay PR open, y el filtro `q` en `refs/tags` no está
verificado contra la API real.

## Criterios de aceptación

- [ ] Barrido a ~8 req/s con `resolved_branch` persistido en la cache.
- [ ] `commit_for_branch` con `resolved` hace 1 request (sin re-resolver).
- [ ] `commits_behind` devuelve `size` en 1 request y pagina como fallback.
- [ ] TTLs nuevos aplicados también a DBs existentes (seed upsert).
- [ ] Scan loop sin `_SUBMIT_DELAY`.
- [ ] Tests Python verdes; stubs de test aceptan `resolved`.

## Alcance

| Archivo | Cambio |
|---|---|
| `bbit_release/bitbucket/client.py` | `MAX_WORKERS=8`, rate limiter 8 rps, `Repository.resolved_branch`, `repos_with_branch` lo puebla, `commit_for_branch(resolved=)`, `commits_behind` con `size` |
| `bbit_release/web/api/repos.py` | `MAX_WORKERS=8`, quita `_SUBMIT_DELAY`, `_branch_repos_cached` persiste/propaga `resolved_branch`, `_repo_scan` lo pasa |
| `bbit_release/cache.py` | TTL `get_user_repositories` 1800 / `get_branch_repositories` 3600, seed upsert `ON CONFLICT` |
| `tests/test_bitbucket_client.py` | Tests de `resolved`, `size`, fallback |
| `tests/test_cache.py` | TTLs nuevos + test upsert |
| `tests/test_web_api.py` | Stubs `commit_for_branch(..., resolved="")` |
| `route-map/route-plan-v40.md` | Este plan |
| `pyproject.toml` | Bump `0.28.0` |

## Orden de implementación

1. `client.py`: rate limiter + `MAX_WORKERS` + `resolved_branch` +
   `commit_for_branch(resolved=)` + `commits_behind(size)`.
2. `repos.py`: `MAX_WORKERS`, `_branch_repos_cached`, `_repo_scan`, quitar
   `_SUBMIT_DELAY` e import de `time`.
3. `cache.py`: TTL + seed upsert.
4. Tests: stubs `resolved`, casos nuevos del cliente y del seed.
5. `pytest tests/ -q` verdes → bump + route-plan + commit `feat:` + push.

## Verificación

```bash
python -m pytest tests/ -q
```

## Nota de versión

Ajuste significativo de rendimiento (sin cambio de comportamiento) → `0.28.0`.

## Estado

- [x] `client.py`: rate limiter 8 rps, `MAX_WORKERS=8`, `Repository.resolved_branch`
- [x] `client.py`: `repos_with_branch` puebla `resolved_branch`
- [x] `client.py`: `commit_for_branch(resolved=)` sin re-resolver
- [x] `client.py`: `commits_behind` con `size` + fallback
- [x] `repos.py`: `MAX_WORKERS=8`, se quita `_SUBMIT_DELAY`
- [x] `repos.py`: `_branch_repos_cached` y `_repo_scan` usan `resolved_branch`
- [x] `cache.py`: TTLs subidos + seed upsert
- [x] 166 tests Python verdes
- [x] Bump `0.28.0`

Estado: implementado