# Route Plan — v4 · Tags por ambiente (validar + generar)

## Objetivo
- **(A) Validar**: para cada repo, el último commit de la rama origen debe tener un tag
  `{env}-{pipeline_id}` por cada ambiente elegido, y ese `id` debe coincidir con el
  deploy de CircleCI de ese commit (número de pipeline + revisión del commit + workflow
  que mencione el ambiente).
- **(B) Generar**: crear esos tags individualmente o por lote, por ambiente seleccionado,
  sobre el head de la rama elegida, reusando **un solo** pipeline id por commit
  (el que generó ese commit), idempotente (no sobrescribe tags existentes).

## Contexto
- Hoy `_repo_scan` (repos.py:194) obtiene tags del commit y por cada tag el primer
  pipeline de CircleCI (`deploys_for_tags`). Las columnas de ambiente usan
  `scheduled_deploys_for_commit`: filtra pipelines `trigger=schedule` con
  `vcs.revision == commit` y workflow que contenga el prefijo. No hay generación de tags.
- Datos reales: el pipeline más reciente con `vcs.revision == head` es el "id de pipeline
  que generó el commit" (validado: bbit-test-01 pipeline #3 rev e38579ed0a == head de
  release/REP-325073). Los repos de prueba no tienen tags (endpoint 404), el formato
  `{env}-{id}` se valida en el flujo real del usuario.
- Confirmado con el usuario: `{id}` = **número de pipeline** de CircleCI; el mismo id se
  usa para todos los ambientes del proyecto (`uat-232`, `stgp-232`, `prod-232`); tag se
  crea solo si no existe ya el tag y solo para ambientes seleccionados.

## Cambios propuestos

### `src/circleci/client.py`
- `pipeline_id_for_commit(repo, branch, commit) -> int | None`: pipelines de la rama,
  filtra `vcs.revision == commit`, devuelve el número más alto.
- `deploy_job_for_pipeline(repo, pipeline, prefix) -> DeployJob | None`: primer workflow
  cuyo nombre contiene el prefijo (case-insensitive) → URL `.../{number}/workflows/{wfid}`.
- `deploy_for_tag(repo, tag, deploy_id, commit, prefix) -> DeployJob | None`: pipelines
  del tag, filtra `number == deploy_id` Y `vcs.revision == commit`, luego workflow con
  prefijo.
- Se elimina `scheduled_deploys_for_commit` (la columna de ambiente pasa a ser tag-driven,
  sin fallback: columna vacía si no hay tag).

### `src/bitbucket/client.py`
- `tag_exists(slug, name) -> bool`: GET refs/tags/{name}; 404 → False.
- `create_tag(slug, name, commit) -> dict`: POST refs/tags `{"name", "target": {"hash"}}`.

### `src/web/api/repos.py`
- `_repo_scan`: por cada env, matchear tag `^{env}-(\d+)$` sobre tags del commit;
  `deploys[env]` = deploy validado o None; `match_tag[env]` = nombre del tag (para
  distinguir "—" de "tag sin deploy" en el frontend).
- Nuevo `POST /api/tags?origin=…&prefixes=…&repo=<slug opcional>`: por repo (todos los
  que tienen la rama, o solo `repo`): head → `pipeline_id_for_commit`; por env: tag
  `{env}-{id}`; `tag_exists` → skip, si no `create_tag`. Reply
  `{origin, items: [{repo, commit, pipeline_id, created, skipped, errors}]}`.

### Frontend (`home.html` / `home.ts`)
- Botón "Generar tags faltantes" (lote) en la fila de acciones + botón por fila
  "Generar tags" en la columna Tags (individual). Ambos usan los ambientes seleccionados.
- Columna de ambiente: link al deploy validado (tooltip con el tag `uat-232`); estado
  "tag sin deploy" (warning) cuando el tag existe pero no valida (match_tag sin deploy).

## Criterios de aceptación
- [ ] Con tag `uat-{id}` en el commit y pipeline con `number == id`, `revision == head`
      y workflow con "uat", la columna uat muestra el link a ese deploy.
- [ ] Sin tag en el commit → columna vacía ("—").
- [ ] Tag con id/revisión que no matchea → estado warning "tag sin deploy".
- [ ] `POST /api/tags` crea `{env}-{pipeline_id}` por ambiente seleccionado sobre el
      head de la rama, no duplica tags existentes y reusa el mismo id para todos los envs.

## Alcance
| Archivo | Cambio |
|---|---|
| `src/circleci/client.py` | 3 métodos nuevos; eliminar `scheduled_deploys_for_commit` |
| `src/bitbucket/client.py` | `tag_exists`, `create_tag` |
| `src/web/api/repos.py` | `_repo_scan` tag-driven; `POST /api/tags` |
| `frontend/src/app/pages/home/home.ts` + `home.html` | generar tags lote/fila; estados |
| `tests/test_circleci_client.py`, `tests/test_bitbucket_client.py`, `tests/test_web_api.py` | tests nuevos + ajuste |

## Orden de implementación
1. CircleCI (3 métodos + baja de `scheduled_deploys_for_commit`).
2. Bitbucket (`tag_exists`, `create_tag`).
3. `_repo_scan` tag-driven.
4. `POST /api/tags`.
5. Frontend.
6. Tests + bump + verificación en vivo.

## Verificación
- `python -m pytest tests/ -q` verde.
- En vivo: scan de la rama (ambas columnas) + `/api/tags` en repos con pipeline.
  Los repos de prueba no tienen tags → generar crea `uat-{id}` y el segundo llamado skip.

## Nota de versión
Funcionalidad nueva → `0.5.2` → `0.6.0` (bump + assert health).

## Estado
- [x] 1. CircleCI
- [x] 2. Bitbucket
- [x] 3. `_repo_scan` tag-driven
- [x] 4. `POST /api/tags`
- [x] 5. Frontend
- [x] 6. Tests + bump + verificación

Estado: implementado.