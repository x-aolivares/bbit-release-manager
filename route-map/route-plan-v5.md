# Route Plan — v5 · Workflows de tag con aprobación + links en columnas

## Objetivo
- Que cada repo tenga un `.circleci/config.yml` con workflows por ambiente
  (`{env}-deploy-on-tag`) que corren solo cuando se crea un tag `{env}-{n}` y
  que exigen **aprobación manual** (`type: approval`) antes del deploy.
- Mostrar los links por ambiente de los tags en **sus propias columnas** (hoy
  todo el texto tag·workflow·status aparece solo en la columna Tags).

## Contexto
- Tras v4, la columna de ambiente mostraba solo un ícono (`◉/◒`) y el link
  completo vivía en la columna Tags. Los repos de prueba no tienen config.
- `deploy_for_tag` validaba `number == deploy_id` Y `revision == commit`: el
  deploy aprobado por tag es un pipeline NUEVO sobre el tag (no el pipeline que
  generó el commit), así que el filtro por número rompía la validación.

## Cambios propuestos
- **`src/circleci/configyml.py`** (nuevo): `ensure_tag_workflows(existing, envs)`.
  Merge con PyYAML: agrega jobs `deploy-{env}` y workflows `{env}-deploy-on-tag`
  (triggers `tags.only /^{env}-[0-9]+$/` + job `approve` type approval + deploy).
  Idempotente; YAML inválido → error (no sobrescribe).
- **`src/bitbucket/client.py`**: `upsert_file(slug, branch, path, content, message)`
  vía `POST /src` multipart (scope repository:write). Acepta respuesta 201 sin body.
- **`src/circleci/client.py`**: `deploy_for_tag(repo, tag, commit, prefix)` sin
  filtro de número de pipeline (solo `revision == commit` + workflow con prefijo).
- **`src/web/api/repos.py`**: `POST /api/circleci-config?origin&prefixes&repo`
  escribe/mergea el config en la rama de origen (lee el existente en el head;
  `GET /src/{ref}` no acepta ramas con `/`, hay que leer sobre el commit hash).
- **Frontend**: botones "Generar workflows de tags" (lote) y "+ Generar workflows"
  (por fila); links de ambiente movidos a su columna (deploy → `✓ tag · wf · status`,
  tag sin deploy → `◒ tag`, sin tag → `—`); Tags columna deja el nombre para tags
  de ambiente (sin duplicar el link).

## Criterios de aceptación
- [x] `POST /api/circleci-config` crea el config (o mergea solo lo faltante) y la
      segunda llamada con los mismos envs reporta `skipped` (idempotente).
- [x] El config generado tiene workflow por env con `type: approval` antes del deploy.
- [x] Columna de ambiente muestra el link del tag (con aprobación pendiente o estado
      del deploy) en su columna; la columna Tags no duplica el link de env.
- [x] `dep ensure_tag_workflows` respeta orbs/jobs/workflows existentes y no rompe
      un config válido.

## Alcance
| Archivo | Cambio |
|---|---|
| `src/circleci/configyml.py` | nuevo: merge YAML con workflows de tag + aprobación |
| `src/bitbucket/client.py` | `upsert_file` (POST /src) |
| `src/circleci/client.py` | `deploy_for_tag` sin filtro `deploy_id` |
| `src/web/api/repos.py` | `POST /api/circleci-config`; `_repo_scan` nueva firma |
| `frontend …/home.ts/html` | botones + links por ambiente en sus columnas |
| `tests/*` | configyml, upsert_file, circleci-config API, deploy_for_tag |
| `docs/requirements.txt` | `pyyaml>=6.0` |

## Orden de implementación
1. `configyml.py`. 2. `upsert_file`. 3. `deploy_for_tag`. 4. endpoint + `_repo_scan`.
5. Frontend. 6. Tests + build + verificación en vivo.

## Verificación
- `pytest tests/ -q` verde (65).
- En vivo: `POST /api/circleci-config` escribe y la segunda llamada queda `skipped`;
  los tags creados disparan pipelines en CircleCI solo si el webhook de tags está
  suscrito (en los repos de prueba no dispara: la columna muestra el tag con
  "◒ tag" hasta que exista un pipeline por tag).

## Nota de versión
Funcionalidad nueva → `0.6.0` → `0.7.0` (bump + assert health).

## Estado
- [x] 1. `configyml.py`
- [x] 2. `upsert_file`
- [x] 3. `deploy_for_tag`
- [x] 4. Endpoint + `_repo_scan`
- [x] 5. Frontend
- [x] 6. Tests + build + verificación

Estado: implementado.