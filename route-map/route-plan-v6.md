# Route Plan — v6 · Diseño unificado de las columnas de ambiente vs Tagged Commit

## Objetivo
Que el diseño de las celdas de las columnas de ambiente (color y tipografía) sea
idéntico al de la columna **Tagged Commit**: enlace azul (`var(--bb-blue)`) con
tipografía monoespaciada (`bb-mono`).

## Contexto
- Hoy la columna **Tagged Commit** renderiza un `<a class="bb-link bb-mono">`
  (azul + monospace, peso 600).
- Las columnas de ambiente renderizaban su tag con `<a class="bb-celllink {estado}">`
  donde el color dependía del estado del deploy (`bb-deploy--ok/pending/danger`:
  verde/naranja/rojo) y tipografía normal (`bb-celllink`).
- El usuario quiere uniformar: las celdas de ambiente deben verse igual a la
  columna Tagged Commit (color azul y tipografía mono).

## Cambios propuestos
- **`frontend/.../home.html`**: el tag de cada columna de ambiente pasa de
  `bb-celllink {estado}` a `bb-link bb-mono` (igual a Tagged Commit).
- **`frontend/.../home.ts`**: eliminar métodos huérfanos tras el cambio
  (`envTagClass`, `statusClass`, `statusIcon`) que solo alimentaban el color por estado.
- **`frontend/.../home.scss`**: eliminar estilos sin uso resultantes
  (`.bb-celllink`, `.bb-deploy`, `.bb-deploy--*`). Se conserva `.bb-deploy-col`
  (estilo de la columna) y `.bb-taggen-link`.

## Criterios de aceptación
- [x] Las celdas de ambiente muestran el tag como un enlace azul en tipografía mono,
      indistinguible visualmente de la columna Tagged Commit.
- [x] No quedan referencias a `bb-celllink`, `bb-deploy--*`, `envTagClass`,
      `statusClass`, `statusIcon` en templates/métodos.
- [ ] El build del frontend compila sin errores.

## Alcance
| Archivo | Cambio |
|---|---|
| `frontend/src/app/pages/home/home.html` | clase del `<a>` de ambiente → `bb-link bb-mono` |
| `frontend/src/app/pages/home/home.ts` | borrar `envTagClass`, `statusClass`, `statusIcon` |
| `frontend/src/app/pages/home/home.scss` | borrar `.bb-celllink`, `.bb-deploy`, `.bb-deploy--*` |

## Orden de implementación
1. `.html`. 2. `.ts`. 3. `.scss`. 4. Build/verificación.

## Verificación
- `ng build` sin errores (bloqueado en el entorno por versión de Node; validación
  manual de referencias: `grep` sin resultados para clases/métodos eliminados).

## Nota de versión
Ajuste estético menor → `0.7.4` → `0.7.5`.

## Estado
- [x] 1. `.html`
- [x] 2. `.ts`
- [x] 3. `.scss`
- [ ] 4. Verificación

Estado: implementado.
