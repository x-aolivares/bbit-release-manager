# Route Plan — v14 · Rediseño de la tabla de parámetros SSM

## Objetivo
Rediseñar la tabla de parámetros SSM: **sin nombres de proyecto**, con una columna
**Estado** que distinga `nuevo` / `reutilizado` / `solo destino`, ordenada
**alfabéticamente**, con el **Valor en QA** placeholder `------`.

## Contexto
- Hoy `home.html` muestra una tabla de params (Parámetro · Tipo · Valor en QA ·
  Repos) y una tabla separada de `removed` (Parámetro · Repos).
- El backend (`classify_ssm`) ya computa `tipo: nuevo|reutilizado` por path, y
  `removed[]` trae los de solo-destino.
- El usuario pidió: quitar los nombres de repo, un único listado alfabético con
  badge de estado, y mantener la columna "Valor en QA" con `------` (feature futuro).

## Cambios propuestos
- **`frontend/.../home.html`**: una sola tabla con columnas Parámetro · Estado ·
  Valor en QA; hint unificado de "solo destino"; sin columna Repos.
- **`frontend/.../home.ts`**: método `paramRows()` que une `params()` + `removed()`
  y ordena alfabéticamente por `param`; `estadoLabel()` y `tipoClass()` cubriendo
  `nuevo`, `reutilizado` y `solo destino`.
- **`frontend/.../home.scss`**: grid `1.6fr 1fr 1fr`, badge `--neutral` para
  solo destino, estilo Valor en QA con guiones.

## Criterios de aceptación
- [x] La tabla no muestra nombres de proyecto.
- [x] Una sola tabla, orden alfabético, con columna Estado.
- [x] Badge distingue nuevo / reutilizado / solo destino.
- [x] Columna "Valor en QA" muestra `------`.
- [x] `ng build` sin errores.

## Alcance
| Archivo | Cambio |
|---|---|
| `frontend/src/app/pages/home/home.html` | tabla única sin Repos + hint |
| `frontend/src/app/pages/home/home.ts` | `paramRows`, `estadoLabel`, `tipoClass` |
| `frontend/src/app/pages/home/home.scss` | estilos de la nueva tabla |

## Orden de implementación
1. `home.html`.
2. `home.ts`.
3. `home.scss`.
4. `ng build`.
5. Versionado `0.9.5 → 0.10.0` + commit `feat:` + push.

## Verificación
- `ng build` con Node ≥24.15 (nvm).

## Nota de versión
Ajuste significativo de UI → `0.9.5` → `0.10.0`.

## Estado
- [x] 1. `home.html`
- [x] 2. `home.ts`
- [x] 3. `home.scss`
- [x] 4. Build
- [x] 5. Versionado + commit + push

Estado: implementado.
