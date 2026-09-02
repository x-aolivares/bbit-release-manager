# Route Plan — v28 · Caché del list_repos completo + key de diff por SSM prefixes

## Objetivo

1. **Cachear el barrido paginado de `list_repos`** (el `GET /repositories/{ws}?...` que
   puede ser varias páginas) para que no se repita entre `/scan`, `/diff`, PR, tags,
   etc. `repos_with_branch` reutiliza la lista completa cacheada como base.
2. **Corregir cache stale de `/diff`**: `diff_cache` no incluía los `SSM_PREFIXES` en
   su key, así que cambiar `SSM_PREFIXES` seguía devolviendo el diff cacheado con los
   prefixes viejos.

## Contexto

- `repos_with_branch` llamaba `client.list_repos()` internamente SIEMPRE, sin cache.
  Cada endpoint que necesitaba repos con rama repetía el barrido paginado completo.
- `diff_cache` usaba key `{origin}|{dest}|{proj}|{exclude}` sin incorporar los SSM
  prefixes que determinan qué parámetros se extraen/marcan. Cambiar `SSM_PREFIXES` no
  invalidaba el resultado → stale cache.

## Cambios propuestos

- `client.repos_with_branch(branch, prefixes=None, repos=None)`: acepta la lista
  completa pre-cargada para no re-barrer el workspace.
- En `repos.py`, `_all_repos_cached` obtiene y cachea la lista completa del workspace
  en `repo_cache` (key `("", "all", prefs, exclude)`), compartida con `/api/repos`.
  `_branch_repos_cached` la usa como base en el primer llamado.
- `get_diff`/`set_diff`/`invalidate` aceptan `ssm_prefixes` e incorporan a la key.
- `/api/diff` pasa `cfg.ssm_prefixes` a get/set/invalidate.

## Criterios de aceptación

- [x] Un `list_repos` completo se barre a lo sumo una vez; pide repetidas reúsan la
      lista cacheada / `branch_repos`.
- [x] Cambiar `SSM_PREFIXES` produce otra key de diff (sin resultado stale).
- [x] Suite verde.

## Alcance

| Archivo | Cambio |
|---|---|
| `bbit_release/bitbucket/client.py` | `repos_with_branch(..., repos=None)` |
| `bbit_release/web/api/repos.py` | `_all_repos_cached` + `_branch_repos_cached` con base cacheada; `/api/diff` y `/api/repos` usan la base |
| `bbit_release/cache.py` | `get_diff`/`set_diff`/`invalidate` con `ssm_prefixes` en key |
| `tests/test_web_api.py` | stubs `repos_with_branch(..., repos=None)`; test de key por SSM; 2da consulta sin list_repos |

## Orden de implementación

1. `repos_with_branch` con base opcional
2. `_all_repos_cached` + `_branch_repos_cached` con base
3. SSM prefixes en key de diff
4. Tests
5. Bump versión

## Verificación

```bash
pytest tests/ -v
```

## Nota de versión

Bug fix + optimización → `0.20.1` → `0.20.2`, commit `fix:`/`feat:`.

## Estado

- [x] base cacheada en repos_with_branch
- [x] `_all_repos_cached` + branch_repos con base
- [x] key de diff con ssm_prefixes
- [x] tests verdes (136/136)
- [x] versión bumpeda (0.20.2)

Estado: implementado
