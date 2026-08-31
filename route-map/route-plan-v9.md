# Route Plan — v9 · Quitar botón de "Generar workflows de tags"

## Objetivo
Eliminar del frontend el botón y el flujo de "Generar workflows de tags"; el
endpoint backend `POST /api/circleci-config` no se toca.

## Contexto
- El botón "Generar workflows de tags" (home.html) invocaba `generateWorkflows()`
  → `POST /api/circleci-config`.
- El usuario lo considera inútil ("eso no nos sirve") y pidió quitarlo solo a
  nivel UI/frontend; backend queda intacto.

## Cambios propuestos
- **`frontend/.../home.html`**: borrar el `<button>` "Generar workflows de tags".
- **`frontend/.../home.ts`**: borrar `generateWorkflows()` y los signals
  `workflowCfg`, `workflowCfgRepo` (quedan huérfanos).

## Criterios de aceptación
- [x] No aparece el botón "Generar workflows de tags".
- [x] Sin referencias a `generateWorkflows` / `workflowCfg` en el frontend.

## Alcance
| Archivo | Cambio |
|---|---|
| `frontend/src/app/pages/home/home.html` | borrar botón |
| `frontend/src/app/pages/home/home.ts` | borrar señal/método muertos |

## Orden de implementación
1. `.html`. 2. `.ts`. 3. Verificación por grep. 4. Versionado + commit + push.

## Verificación
- `grep` sin resultados de `generateWorkflows|workflowCfg` en `frontend/`.

## Nota de versión
Ajuste de UI menor → `0.7.7` → `0.7.8`.

## Estado
- [x] 1. `.html`
- [x] 2. `.ts`
- [x] 3. Verificación
- [x] 4. Versionado + commit + push

Estado: implementado.