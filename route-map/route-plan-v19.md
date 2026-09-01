# Route Plan — v19 · Prefijos de proyectos

## Objetivo
Poder configurar prefijos de nombre de proyecto (workspace) para **no escanear
todos los repos**, sino solo los cuyo slug empieza con uno de los prefijos.
Se setean como los chips de tags/ambientes (agregar 1, 2, 3…), con default en
config, y el chequeo de rama se hace **en paralelo** solo sobre los que matchean.

## Contexto
Hoy los repos se resuelven con `repos_with_branch(origin)` → `list_repos()`
(recorre TODO el workspace) y chequea la rama de a uno **en serie**. El único
filtro actual es `BITBUCKET_REPOS` (nombres exactos), sin prefijos.

Distinción clave: `DEPLOY_PREFIXES`/`prefixes` son **ambientes de deploy**
(uat/stgp/prod, columnas + tags), NO filtran qué se escanea. Esto nuevo
(`project_prefixes`) sí filtra qué proyectos se consultan.

## Cambios propuestos
- **Config** (`config.py`): propiedad `project_prefixes` desde
  `BITBUCKET_PROJECT_PREFIXES` (vacío = todos).
- **Client** (`bitbucket/client.py`):
  - `list_repos(prefixes=None)`: filtra por prefijo de slug.
  - `repos_with_branch(branch, prefixes=None)`: filtra + paraleliza el chequeo
    de rama con `ThreadPoolExecutor` (win "en paralelo").
- **API** (`web/api/repos.py`): helper `_project_prefixes(cfg, raw)` (param →
  config default); `/api/repos`, `/api/scan`, `/api/diff` aceptan
  `project_prefixes` (solo scan usa también `prefixes` = ambientes).
- **CLI** (`cli.py`): muestra `Proj prefixes` y los pasa a `list_repos`.
- **Frontend** (`home.html` / `home.ts`): sección "Prefijos de proyectos"
  (chips tipo ambientes) que pasa `project_prefixes` a los 3 pasos
  (proyectos / tabla / parámetros).

## Criterios de aceptación
- [x] Solo los repos cuyo slug arranca con un prefijo se listan/escanean.
- [x] Chequeo de rama en paralelo (ThreadPoolExecutor).
- [x] Default desde config; override por scan desde la UI.
- [x] `ng build` sin errores + 86 tests verdes.

## Alcance
| Archivo | Cambio |
|---|---|
| `bbit_release/config.py` | `project_prefixes` (BITBUCKET_PROJECT_PREFIXES) |
| `config/env.base.example` | doc del nuevo env var |
| `bbit_release/bitbucket/client.py` | `list_repos(prefixes)`, `repos_with_branch` paralelo |
| `bbit_release/web/api/repos.py` | helper + param `project_prefixes` en 3 endpoints |
| `bbit_release/cli.py` | mostrar y aplicar prefijos |
| `frontend/src/app/pages/home/home.ts` | signals + métodos + param en 3 pasos |
| `frontend/src/app/pages/home/home.html` | sección "Prefijos de proyectos" |

## Orden de implementación
1. Config + client.
2. API (+ fix stubs de tests para `prefixes=None`).
3. CLI y frontend.
4. Build + tests.
5. Versionado + commit + push.

## Verificación
- `ng build` → OK.
- `pytest` → 86 passed.

## Nota de versión
Funcionalidad nueva moderada → `0.15.0` → `0.16.0`.

## Estado
- [x] 1. Config + client
- [x] 2. API
- [x] 3. CLI + frontend
- [x] 4. Build + tests
- [ ] 5. Versionado + commit + push

Estado: implementado (build OK, 86 tests verdes).
