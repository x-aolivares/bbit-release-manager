# Route Plan — v45 · Spinner de procesando con duración mínima (cache)

## Objetivo
Que el modal "Procesando…" se muestre al menos 2 segundos, incluso cuando la
consulta se resuelve al instante desde la cache. Sin el mínimo, las consultas
rápidas (cache) parpadeaban y daban la sensación de que no hicieron nada.

## Contexto
`bbit_release/cache.py` devuelve respuestas cacheadas (SQLite) casi
instantáneas; el spinner inline parpadeaba. Además, `loadRepos`/`loadParams`
no liberaban su flag al fallar la request (spinner podía quedar colgado), y el
nuevo modal heredaba ese problema.

## Cambios propuestos
- Helper `releaseBusy(startedAt, done)`: difiere el apagado del flag hasta
  completar `busyMinMs = 2000` desde el arranque de la consulta.
- `loadRepos` y `loadParams`: guardan el timestamp al iniciar y liberan el
  flag vía `releaseBusy` en `complete` Y en `error` (fix de flag colgado).
- El modal existente (RMV-44) se muestra los 2s mínimos sin cambios.

## Criterios de aceptación
- Consulta por cache: el modal permanece ~2s y se cierra solo.
- Consulta lenta (>2s): el modal no se corta antes de terminar.
- Error de red: el modal se cierra a los 2s (ya no se queda colgado).
- Build de Angular verde.

## Alcance
| Archivo | Cambio |
|---|---|
| `frontend/src/app/pages/home/home.ts` | `busyMinMs` + `releaseBusy`; uso en `loadRepos`/`loadParams` (complete y error) |
| `pyproject.toml` | `0.29.1` → `0.29.2` |

## Orden de implementación
1. `home.ts`. 2. Build. 3. Versionado + commit.

## Verificación
- `npx ng build --configuration development` sin errores.
- Prueba manual: repetir "Refrescar" (2ª vez con cache) → modal se ve ~2s.

## Nota de versión
`0.29.2` — ajuste pequeño (UX).

## Estado
- [x] `releaseBusy` con mínimo 2s
- [x] `loadRepos`/`loadParams` liberan flag en complete y error
- [x] Build de Angular verde
- [x] `0.29.2`

Estado: implementado