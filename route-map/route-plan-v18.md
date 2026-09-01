# Route Plan — v18 · Refresh de sección (sin recargar todo)

## Objetivo
Poder re-ejecutar una sección del scan (Proyectos / Tabla / Parámetros) desde
su propia card, sin recargar la página ni los otros pasos.

## Contexto
Tras v17 (pasos desacoplados), cada sección ya tiene su función de carga
independiente (`loadProjects` / `loadTable` / `loadParams`). El refresh por
sección es simplemente exponer un botón que re-invoca esa función.

## Cambios propuestos
- **`frontend/src/app/pages/home/home.html`**: botón "Refrescar" (con spinner
  propio) en el header de las cards "Proyectos con '{origin}'",
  "Repos con '{origin}'" y "Parámetros SSM...", que llama a su paso
  correspondiente.

## Criterios de aceptación
- [x] Cada card ofrece "Refrescar" que re-ejecuta SOLO su paso.
- [x] `ng build` sin errores.

## Alcance
| Archivo | Cambio |
|---|---|
| `frontend/src/app/pages/home/home.html` | botones Refrescar por card |

## Orden de implementación
1. Botones en los 3 headers.
2. `ng build`.
3. Versionado + commit + push.

## Verificación
- `ng build` → OK.

## Nota de versión
Ajuste pequeño → `0.14.0` → `0.15.0`.

## Estado
- [x] 1. Botones por card
- [x] 2. Build
- [ ] 3. Versionado + commit + push

Estado: implementado (build OK).
