# Route Plan — v46 · Filtro por estado en la tabla y previsualización de SSM

## Objetivo
Poder filtrar los parámetros SSM por su estado (`nuevo`, `reutilizado`,
`solo destino`) con chips toggle, tanto en la tabla de respuesta como en la
previsualización del reporte.

## Contexto
La tabla de SSM muestra todos los rows sin filtro: `paramRows()` recorre
`params()` (tipo `nuevo`/`reutilizado`) y `removed()` ("solo destino"). El
reporte en modo params copia `params()` sin filtrar. Con muchos parámetros es
difícil ver solo los que interesan.

## Cambios propuestos
- Signal `estadoFilter` (array de estados activos; vacío = todos).
- `toggleEstado()` activa/desactiva un chip; `estadoFilterActive()` decide.
- `filteredParams()` filtra `params()` por estados activos.
- `paramRows()` usa `filteredParams()` + filtra `removed()` por "solo destino".
- `reportText()` modo params usa `filteredParams()` (mismo filtro en el reporte).
- HTML: fila de chips `bb-chip` sobre la tabla; SCSS de chips (active →
  `--bb-blue`).

## Criterios de aceptación
- Con `nuevo` y `reutilizado` activados, la tabla muestra solo esos rows.
- El reporte "Parámetros SSM" respeta el mismo filtro.
- Sin chips activos se muestra todo (comportamiento previo).
- Build de Angular verde.

## Alcance
| Archivo | Cambio |
|---|---|
| `frontend/src/app/pages/home/home.ts` | `estadoFilter` + `toggleEstado`/`estadoFilterActive`/`filteredParams`; `paramRows` y `reportText` filtrados |
| `frontend/src/app/pages/home/home.html` | Chips de filtro sobre la tabla SSM |
| `frontend/src/app/pages/home/home.scss` | `.bb-chip`, `.bb-chip--active`, `.bb-params-filters` |
| `pyproject.toml` | `0.29.2` → `0.29.3` |

## Orden de implementación
1. `home.ts`. 2. `home.html`. 3. `home.scss`. 4. Build. 5. Versionado + commit.

## Verificación
- `npx ng build --configuration development` sin errores.
- Manual: cargar diff → toggler chips → tabla y reporte muestran solo el estado elegido.

## Nota de versión
`0.29.3` — ajuste pequeño (UI).

## Estado
- [x] `estadoFilter` + togglers en `home.ts`
- [x] Tabla filtrada (`paramRows`)
- [x] Reporte filtrado (`reportText` modo params)
- [x] Chips en `home.html` + estilos
- [x] Build de Angular verde
- [x] `0.29.3`

Estado: implementado