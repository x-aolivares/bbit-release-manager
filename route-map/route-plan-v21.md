# Route Plan — v21 · Filtros de prefijo/exclusión aplicados en todo el flujo

## Objetivo

Corregir el bug por el cual los prefijos de proyecto (`BITBUCKET_PROJECT_PREFIXES`)
y las exclusiones (blacklist) **no se aplican** en el descubrimiento de repos de
los endpoints de mutación (`/tags`, `/prs/*`, `/circleci-config`) ni en el CLI
(`repos`, `diff`). Además, persistir prefijos y exclusiones en `config/env.base`
desde el frontend al conectar sesión, y limpiarlos al desconectar.

## Contexto

- Los prefijos de proyecto se aplican hoy **solo** vía query param en
  `/api/repos`, `/api/scan` y `/api/diff` (`_project_prefixes()`).
- `/api/tags`, `/api/prs/create-missing`, `/api/prs/update-titles` y
  `/api/circleci-config` llaman `client.repos_with_branch(origin)` **sin pasar
  prefijos**, por lo que escanean TODOS los repos del workspace.
- Las exclusiones (blacklist del frontend) se aplican **solo** en `/api/repos`
  (post-filtro por `exclude=`), nunca en scan/diff/mutación.
- No existe una clave de config para exclusiones (`BITBUCKET_EXCLUDE_REPOS` no
  existe) ni persistencia de los prefijos/exclusiones del frontend.
- Criterio de aceptación v20.5: "Slugs en `exclude=` no aparecen en proyectos
  ni se escanean" — no se cumplía para scan/mutación.

## Cambios propuestos

1. **Config**: nueva property `exclude_repos` (`BITBUCKET_EXCLUDE_REPOS`, lista en
   minúsculas). Métodos `save_filters(project_prefixes=..., exclude_repos=...)`
   (persiste ambas claves en `env.base` reutilizando la lógica de `save_tokens`)
   y `clear_filters()` (limpia ambas claves).
2. **Session API**: `POST /api/session` acepta `project_prefixes` y
   `exclude_repos` opcionales y los persiste con `save_filters()`. `DELETE
   /api/session` siempre llama a `clear_filters()` (con o sin
   `delete_credentials`).
3. **Filtrado centralizado**: helper `_filters(cfg)` que devuelve `(prefs,
   blocked)` combinando config (default) con override por query param. Todos los
   endpoints que hacen descubrimiento aplican prefijos **y** exclusiones.
4. **Endpoints de mutación** (`/tags`, `/prs/*`, `/circleci-config`): recibir y
   aplicar prefijos + exclusiones.
5. **CLI** `repos`/`diff`: aplicar `cfg.project_prefixes` y `cfg.exclude_repos`.
6. **Frontend**: al `connect()`, enviar `project_prefixes` y `exclude_repos` en
   el body. Los chips se siguen editando al vuelo (query param en `/repos`,
   `/scan`, `/diff`).
7. **`config/env.base.example`**: documentar `BITBUCKET_EXCLUDE_REPOS=`.

## Criterios de aceptación

1. Con prefijos configurados, `/tags`, `/prs/create-missing`,
   `/prs/update-titles` y `/circleci-config` solo operan sobre repos con el
   prefijo.
2. Con exclusiones configuradas, los repos excluidos **no** se escanean en
   scan/diff ni se mutan en tags/PRs/circleci-config.
3. `POST /api/session` persiste prefijos y exclusiones en `env.base`.
4. `DELETE /api/session` limpia prefijos y exclusiones (con y sin
   `delete_credentials`).
5. Retrocompat: `/repos`, `/scan`, `/diff` siguen aceptando overrides por query
   param.
6. CLI `repos`/`diff` respetan `cfg.project_prefixes` y `cfg.exclude_repos`.

## Alcance

| Archivo | Cambio |
|---|---|
| `route-map/route-plan-v21.md` | este plan |
| `bbit_release/config.py` | `exclude_repos`, `save_filters`, `clear_filters` |
| `config/env.base.example` | `BITBUCKET_EXCLUDE_REPOS=` |
| `bbit_release/web/api/repos.py` | helpers de filtro; persistencia en session; filtros en mutación |
| `bbit_release/cli.py` | prefijos/exclusiones en `repos`/`diff` |
| `frontend/src/app/pages/home/home.ts` | enviar prefijos+exclusiones en `connect()` |
| `tests/test_config.py` | `save_filters`/`clear_filters` |
| `tests/test_web_api.py` | filtros en mutación; session persiste/limpia |

## Orden de implementación

1. Config (`exclude_repos`, `save_filters`, `clear_filters`) + tests.
2. Session API: persistir en POST, limpiar en DELETE + tests.
3. Filtrado centralizado + endpoints de mutación + tests.
4. CLI.
5. Frontend.
6. Version `0.17.0 → 0.18.0`, commit `fix:` + push.

## Verificación

- `python -m pytest tests/ -q` verde.
- `npm run build` en `frontend/` (si el build corre).
- Manual en dev: prefijo + exclusión afectan scan/tags/PRs; disconnect limpia.

## Nota de versión

Ajuste significativo (corrige comportamiento de descubrimiento y persiste
filtros en config) → `0.17.0 → 0.18.0`, commit `fix:`.

## Estado

- [x] Config: `exclude_repos`, `save_filters`, `clear_filters`
- [x] Session API persiste/limpia
- [x] Filtros en scan/diff/mutación
- [x] CLI `repos`/`diff`
- [x] Frontend `connect()`
- [x] Tests verdes
- [x] Version + commit + push

Estado: implementado en v0.18.0
