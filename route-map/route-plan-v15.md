# Route Plan — v15 · Diagnóstico de deploys y display `tag - estado` por ambiente

## Objetivo
1. Investigar por qué el contador "Con deploy en prod" no sube aunque CircleCI
   reporte el workflow de deploy a prod como `success`.
2. Mostrar en las columnas de deploy de la tabla el `tag · estado`
   (p. ej. `prod-232 · success`) de forma informativa por ambiente.

## Contexto
- `stats.prod` (repos.py) cuenta repos donde
  `deploys["prod"].status == "success"`. El usuario confirma que CircleCI ya
  muestra *success* en prod, pero el contador queda en 0 → la resolución en
  `deploy_for_tag` no está encontrando/validando el deploy.
- Posibles motivos (a discriminar con logging): tag no está en el commit
  (`tags_on_commit`), `vcs.revision` del pipeline no coincide con `match_commit`,
  o ningún workflow contiene el prefijo del ambiente.
- La UI hoy muestra solo el nombre del tag por ambiente (p. ej. `prod-232`).

## Cambios propuestos
- **`src/circleci/client.py`**: logger `bbit.circleci`; logs en `deploy_for_tag`
  (pipelines hallados, descartes por `vcs.revision`, workflow/status resuelto) y
  en `deploy_job_for_pipeline` (sin workflow que contenga el prefijo).
- **`src/web/main.py`**: `logger.basicConfig` al crear la app para volcar los
  logs a stderr (visible en uvicorn).
- **`src/web/api/repos.py`**: logger `bbit.scan`; log cuando no se encuentra el
  tag `{env}-{n}` en `match_commit`.
- **`frontend/.../home.html`**: la celda de deploy muestra `{{ envTagLabel(...) }}`.
- **`frontend/.../home.ts`**: `envTagLabel()` devuelve `tag · status` (o
  `tag · created` si no hay status validado).

## Criterios de aceptación
- [x] Los logs de diagnóstico se emiten al resolver deploys por ambiente.
- [x] La tabla de repos muestra `tag · estado` por ambiente.
- [x] `ng build` sin errores y tests backend verdes.

## Alcance
| Archivo | Cambio |
|---|---|
| `src/circleci/client.py` | logs de diagnóstico |
| `src/web/main.py` | `basicConfig` |
| `src/web/api/repos.py` | log tag no encontrado |
| `frontend/src/app/pages/home/home.html` | celda deploy con `envTagLabel` |
| `frontend/src/app/pages/home/home.ts` | `envTagLabel()` |

## Orden de implementación
1. Logging backend.
2. UI `tag · estado`.
3. Tests backend + `ng build`.
4. Versionado `0.10.0 → 0.11.0` + commit `feat:` + push.

## Verificación
- `.venv/bin/python -m pytest tests/test_circleci_client.py tests/test_web_api.py`
- `ng build` con Node ≥24.15.

## Nota
Pendiente: con los logs en mano confirmar la causa raíz del contador de prod y
aplicar la corrección concreta (esto último puede ser v16).

## Nota de versión
Ajuste significativo → `0.10.0` → `0.11.0`.

## Estado
- [x] 1. Logging backend
- [x] 2. UI `tag · estado`
- [x] 3. Tests + build
- [ ] 4. Versionado + commit + push

Estado: implementado (diagnóstico).
