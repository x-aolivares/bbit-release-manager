# Route Plan — v11 · Listado de URLs seleccionables (3/3)

## Objetivo
Generar un listado copiable con las URLs de cada repo de la iniciativa en un
modal: **ramas de origen**, **PRs** y **tags** (con selector de ambiente si hay
más de uno). Es texto plano con **1 línea por repo** en orden alfabético de
slug, lista solo para copiar y pegar (sin headers): la URL, o `-------------------`
(19 guiones) cuando el dato no existe.

## Contexto
- Tras resolver las ramas, el front ya conoce por repo (`ScanRepo`):
  `branch_url`, `pr.url` (cuando `pr.exists`), `match_tag[env]` y el helper
  `envTagHref(repo, env)` con la URL del tag/su pipeline.
- El botón de "Generar workflows de tags" se quitó en `c8d7cb8`; queda lugar en
  la fila de acciones para el nuevo botón "Generar listado de proyectos".
- No hay backend nuevo: `scan()` ya entrega todos los datos necesarios; el modal
  es 100% frontend.

## Cambios propuestos
- **`frontend/.../home.ts`**:
  - Señal `reportOpen` (modal), `reportMode: 'branch' | 'pr' | 'tag'`,
    `reportEnv` (índice de `prefixes()`).
  - `reportText()` arma el texto: por repo en orden alfabético una línea con la
    URL (o `-------------------` si falta); sin headers para pegado directo.
  - Botón externo de cerrar/copiar al portapapeles (`navigator.clipboard`).
- **`frontend/.../home.html`**:
  - Botón "Generar listado de proyectos" en `.bb-actions`.
  - Modal overlay (máscara + panel) con: 3 botones de modo; selector de ambiente
    solo si `prefixes().length > 1`; textarea `readonly` con el texto; botones
    "Copiar" y "Cerrar".
- **`frontend/.../home.scss`**: estilos del overlay y del panel del modal.

## Criterios de aceptación
- [x] Modal se abre solo con `repos().length > 0`.
- [x] Modos: rama origen, PRs, tags (con selector de ambiente si hace falta).
- [x] Texto: 1 línea por repo, orden alfabético por slug; URL o 19 guiones, sin headers.
- [x] "Copiar" copia el texto al portapapeles y muestra feedback.
- [x] `ng build` sin errores.

## Alcance
| Archivo | Cambio |
|---|---|
| `frontend/src/app/pages/home/home.ts` | señales + `reportText()` |
| `frontend/src/app/pages/home/home.html` | botón + modal |
| `frontend/src/app/pages/home/home.scss` | estilos modal |

## Orden de implementación
1. `route-plan-v11.md` (este archivo).
2. `home.ts`: señales y `reportText()`.
3. `home.html`: botón y modal.
4. `home.scss`: estilos.
5. Versionado `0.8.0 → 0.9.0` + commit `feat:` + push.

## Verificación
- `ng build` (con Node ≥24.15 vía `nvm use 24.15.0`) sin errores.
- Revisión manual del texto generado por modo.

## Nota de versión
Nueva funcionalidad de reporte → `0.8.0` → `0.9.0`.

## Estado
- [x] 1. route-plan
- [x] 2. `home.ts`
- [x] 3. `home.html`
- [x] 4. `home.scss`
- [x] 5. Versionado + commit + push

Estado: implementado.