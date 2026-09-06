# Route Plan — v43 · Deploys por tag reales y estado del job de deploy

## Objetivo
Que el scan detecte los pipelines de tag (deploys reales) y reporte el estado
del job de deploy, no del workflow completo ni "sin deploy validado".

## Contexto
`CircleCiClient.pipelines(repo, tag=...)` enviaba `?branch=<tag>` al endpoint
`GET /project/{slug}/pipeline`. La API v2 solo filtra por **nombre de rama**
(`branch`); los pipelines de tag traen `vcs.branch=null` y `vcs.tag=<tag>`.
Resultado: la consulta devolvía 0 items → `deploy_for_tag` nunca encontraba
nada → todos los envs salían "pendiente / sin deploy validado".

Evidencia (probe real con token):
- bbit-test-03 tiene `uat-12/stgp-12/prod-12` con `rev=f50b0e12a90b` (el commit
  que el scan buscaba y reportaba "sin pipelines").
- `?branch=prod-12` → 0 items. Pipeline `prod-18` (bbit-test-02):
  `vcs.branch=null`, `vcs.tag='prod-18'`, workflow `prod-deploy-on-tag`
  status `success`, jobs `approve` (approval) y `deploy-prod` (build).

## Cambios propuestos
1. `pipelines()` caso `tag`: no enviar `branch`; paginar el proyecto y filtrar
   client-side por `vcs.tag == tag` (caso `branch` intacto). Cache
   `(repo, tag, kind=tag)` TTL 120 sin cambios.
2. `_deploy_from_workflow` recibe `prefix` (env) y calcula el **estado
   semántico** desde los jobs: si el gate de aprobación (`type=approval`) no
   está en `success` → `on_hold`; si está `canceled` → `canceled`; si está
   aprobado → status del job de deploy real (el que contiene el env, o el
   último job no-approval).
3. `DeployJob` gana `job` y `approval`. Deep-link: al gate de aprobación
   mientras esté pendiente; si está aprobado, al job de deploy real.
4. `_serialize_deploy` expone `job` y `approval`.
5. Front: badge `on_hold` → "esperando aprobación"; tooltip
   `{tag} · {workflow} · {job} · {status}`; alinear `on-hold` (guion) →
   `on_hold` (lo que devuelve la API).

## Criterios de aceptación
- `pipelines(repo, tag=...)` devuelve los pipelines con `vcs.tag == tag`
  (verificado contra la API real: prod-18 → rev=0949dfa4..., status success).
- `deploy_for_tag` ya no devuelve siempre None: encuentra el pipeline del tag
  con `vcs.revision == commit`.
- El estado refleja el job de deploy (con gate: `on_hold` mientras la
  aprobación está pendiente).
- Sin cambios en el caso `branch` (`?branch=` se sigue enviando).
- 168+ tests verdes.

## Alcance
| Archivo | Cambio |
|---|---|
| `bbit_release/circleci/client.py` | fix filtro tag; `_deploy_from_workflow(prefix)` + `_pick_deploy_job` + `_deploy_status`; `DeployJob.job/.approval`; deep-link por estado del gate |
| `bbit_release/web/api/repos.py` | `_serialize_deploy` con `job` y `approval` |
| `frontend/src/app/pages/home/home.ts` | `DeployInfo` + `job/approval`; badge `on_hold`; tooltip con job |
| `tests/test_circleci_client.py` | tests tag sin branch; estado por job; selección por prefijo; approval pendiente |
| `pyproject.toml` | `0.28.2` → `0.29.0` |

## Orden de implementación
1. `client.py` (filtro tag → helpers de estado). 2. `repos.py`.
3. `home.ts`. 4. Tests. 5. Versionado + commit.

## Verificación
- `pytest tests/ -q`.
- Probe real con token: `pipelines("bbit-test-02", tag="prod-18")` →
  pipeline con `rev=0949dfa4...`; deploy `success`, `job=deploy-prod`.
- Scan de `bbit web` mostrando badges reales (ok/falló/esperando aprobación).

## Nota de versión
`0.29.0` — ajuste significativo: arregla la detección completa de deploys por
tag (feature que estaba muda) y agrega estado del job de deploy + gate.

## Estado
- [x] Fix filtro de tag en `pipelines()`
- [x] Estado semántico del deploy (job + gate) en `_deploy_from_workflow`
- [x] `DeployJob.job/.approval` y `_serialize_deploy`
- [x] Front: badge `on_hold`, tooltip con job
- [x] Tests actualizados y agregados
- [x] `0.29.0`
- [x] `pytest tests/ -q` verde
- [ ] Probe real del pipeline de tag (prod-18)

Estado: implementado