# Route Plan — v13 · Sección de parámetros SSM en el modal de listado

## Objetivo
Que el modal "Listado de proyectos" incluya un modo que liste los parámetros
SSM resultantes del análisis (los de la tabla, obtenidos tras crear el PR),
con el mismo formato de pegado que las URLs: **una línea por parámetro**,
solo el path, sin headers.

## Contexto
- El modal (`home.ts::reportText`) ya genera texto plano para `branch`, `pr` y
  `tag` a partir de `reportMode`; la señal `params()` (SsmParam[]) queda
  poblada por `/api/diff` tras `resolve()`.
- El usuario confirmó el formato deseado: solo el `param` en cada línea.

## Cambios propuestos
- **`frontend/.../home.ts`**: `reportMode` suma `'params'`; en `reportText()`,
  si es `'params'`, emitir `params().map(p => p.param).join('\n')` (sin dashes
  ni headers).
- **`frontend/.../home.html`**: botón "Parámetros SSM" en la fila de modos del
  modal; el selector de ambiente solo aplica al modo `tag`.

## Criterios de aceptación
- [x] El modal incluye el modo "Parámetros SSM".
- [x] El texto sale como 1 línea por path, listo para pegar.
- [x] `ng build` sin errores.

## Alcance
| Archivo | Cambio |
|---|---|
| `frontend/src/app/pages/home/home.ts` | `reportMode` + `reportText()` |
| `frontend/src/app/pages/home/home.html` | botón "Parámetros SSM" |

## Orden de implementación
1. `home.ts`.
2. `home.html`.
3. `ng build`.
4. Versionado `0.9.4 → 0.9.5` + commit `feat:` + push.

## Verificación
- `ng build` con Node ≥24.15 (nvm).

## Nota de versión
Ajuste pequeño → `0.9.4` → `0.9.5`.

## Estado
- [x] 1. `home.ts`
- [x] 2. `home.html`
- [x] 3. Build
- [x] 4. Versionado + commit + push

Estado: implementado.