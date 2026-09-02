# Route Plan — v26 · Diff contra repos conocidos de la rama

## Objetivo

Dejar de barrer todos los repos del workspace para resolver los parámetros
SSM del master en `/diff`. Usar únicamente los repos que ya sabemos que traen
la rama origen, persistidos en una tabla nueva `branch_repos`.

## Contexto

En v25 se cacheó todo en SQLite, pero `/diff` aún hace:
- `master_repos = _all_repos_cached(...)` — barre TODOS los repos del workspace
- `_resolve_master` sobre cada uno de esos repos para calcular `global_dest`
  (base de la clasificación `reutilizado`)

Esto es caro: aunque ya descubrimos qué repos traen la rama
`release/x` (`_branch_repos_cached`), seguimos resolviendo master params de
repos que quizás ni traen la rama. El usuario pide no repetir ese barrido.

## Cambios propuestos

### 1. Nueva tabla `branch_repos` en `bbit_release/cache.py`

```sql
CREATE TABLE IF NOT EXISTS branch_repos (
    cache_key TEXT PRIMARY KEY,      -- "{origin}|{destination}|{prefixes}|{exclude}"
    origin_branch TEXT NOT NULL,
    destination_branch TEXT NOT NULL,
    repos_json TEXT NOT NULL,        -- JSON de [{repo_name, ...}]
    created_at REAL NOT NULL
);
```

API:
```python
def get_branch_repos(origin, destination, prefixes, exclude) -> list[dict] | None
def set_branch_repos(origin, destination, prefixes, exclude, repos: list[dict]) -> None
def clear_branch_repos(origin, destination, prefixes, exclude) -> None
```

### 2. `bbit_release/web/api/repos.py`

- `_branch_repos_cached` ahora persiste en `branch_repos` (columnas
  `origin_branch`, `destination_branch`) en lugar de `repo_cache`, y acepta
  `destination`. En cache-hit normaliza `repo_name` → `slug`.
- En `/diff`:
  - Eliminar `_all_repos_cached` y `master_repos`.
  - Resolver master params SOLO contra `branch_repos`.
  - `global_dest` se calcula solo con esos repos → clasificación
    `reutilizado` limitada a repos que traen la rama (decisión del usuario).
- `force=1` en `/scan` y `/diff` ahora invalida `branch_repos` + `scan`/`diff`
  para re-descubrir y re-computar todo.
- Quitar `_all_repos_cached` (si queda sin uso).

### 3. Tests

- Actualizar `test_diff_reclassifies_productivo_from_repo_without_branch`
  (el repo r2 sin la rama ya no afecta la clasificación).
- Respaldar el comportamiento nuevo en `test_cache.py`.

## Criterios de aceptación

- [ ] `/diff` no llama a `list_repos` (no barre todo el workspace)
- [ ] `/diff` resuelve master params solo para repos de `branch_repos`
- [ ] Si un repo no trae la rama origen, su master params NO se consideran en `global_dest`
- [ ] La tabla `branch_repos` persiste y se reutiliza
- [ ] Tests verdes

## Alcance

| Archivo | Cambio |
|---|---|
| `bbit_release/cache.py` | Tabla + API `branch_repos` |
| `bbit_release/web/api/repos.py` | `/diff` usa `branch_repos`, quita `_all_repos_cached` |
| `tests/test_cache.py` | Tests `branch_repos` |
| `tests/test_web_api.py` | Ajuste `test_diff_reclassifies_productivo...` |
| `pyproject.toml` | Bump a `0.20.0` |

## Orden de implementación

1. Agregar tabla `branch_repos` y API en cache.py
2. `_branch_repos_cached` persiste en `branch_repos`
3. `/diff` usa `branch_repos`; quitar `_all_repos_cached`
4. Ajustar tests
5. Bump versión
6. Correr tests

## Verificación

```bash
pytest tests/ -v
```

## Nota de versión

`0.19.0` → `0.20.0` (cambio de comportamiento: clasificación `reutilizado`
limitada a repos con la rama origen; diff ya no barre todo el workspace)

## Estado

- [x] tabla branch_repos en cache.py
- [x] _branch_repos_cached persiste en branch_repos
- [x] /diff usa branch_repos y quita _all_repos_cached
- [x] tests ajustados
- [x] versión bumpeda (0.20.0)
- [x] tests verdes (132/132)

Estado: implementado
