# Route Plan — v47 · "Solo destino" en la previsualización del reporte

## Objetivo
Que el reporte "Parámetros SSM" muestre las filas "solo destino" (removed)
cuando el filtro por estado las incluye.

## Contexto
RMV-46 filtró la tabla y el reporte, pero `reportText()` en modo params solo
copiaba `filteredParams()` (estados `nuevo`/`reutilizado`); los `removed()`
("solo destino") nunca entraban al reporte. Al seleccionar solo "solo destino"
la previsualización quedaba vacía.

## Cambios propuestos
- `reportText()` modo params usa `paramRows()` (mismo filtro + "solo destino"
  + filtro de repos de proyectos) en lugar de `filteredParams()`.
- La previsualización queda alineada con la tabla de respuesta.

## Criterios de aceptación
- Chip "solo destino" activado → el reporte lista esas filas.
- Sin chips activos → el reporte muestra todos (ahora incluye "solo destino").
- Build de Angular verde.

## Alcance
| Archivo | Cambio |
|---|---|
| `frontend/src/app/pages/home/home.ts` | `reportText()` modo params con `paramRows()` |
| `pyproject.toml` | `0.29.3` → `0.29.4` |

## Orden de implementación
1. `home.ts`. 2. Build. 3. Versionado + commit.

## Verificación
- `npx ng build --configuration development` sin errores.
- Manual: activar "solo destino" → previsualizar → filas presentes.

## Nota de versión
`0.29.4` — ajuste pequeño (fix UI).

## Estado
- [x] `reportText()` con `paramRows()` en modo params
- [x] Build de Angular verde
- [x] `0.29.4`

Estado: implementado