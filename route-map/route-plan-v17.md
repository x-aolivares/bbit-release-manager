# Route Plan — v17 · Separar el scan en pasos (proyectos / tabla / parámetros)

## Objetivo
Desacoplar el flujo de scan en pasos independientes que se disparan por click
(lazy), en vez de encadenarlos automáticamente. "Proyectos" devuelve solo los
repos con la rama; "Tabla" devuelve la tabla con PR/tags/deploys/sync;
"Parámetros" devuelve los parámetros SSM.

## Contexto
- `GET /api/repos?origin` ya devolvía solo `{slug,name,workspace,default_branch}`
  (proyectos con la rama) → reutilizado como step "Proyectos".
- `GET /api/scan` devolvía la tabla (ya desacoplada de params).
- `GET /api/diff` devolvía los parámetros.
- El acoplamiento estaba en el frontend: `resolve()` llamaba `/api/scan` y,
  si había repos, **encadenaba** `loadParams()` → `/api/diff`.
- El botón "Resolver ramas" + `loading` global agrupaba todo.

## Cambios propuestos
- **`frontend/src/app/pages/home/home.ts`**:
  - `projects = signal<ScanProject[]>([])` + spinners por paso
    (`projectsLoading`, `tableLoading`, `paramsLoading`).
  - `loadProjects()` → `GET /api/repos?origin` (solo proyectos, orden alfabético).
  - `loadTable()` → `GET /api/scan` (tabla + stats + CI, sin encadenar params).
  - `loadParams()` sin argumento → `GET /api/diff`.
  - `resolve()` pasa a ser alias de `loadTable()` (tras crear/actualizar PRs o
    tags refresca la tabla, nunca los params).
  - `disconnect()` limpia también `projects`.
- **`frontend/src/app/pages/home/home.html`**: 3 botones de paso con spinner
  propio ("Obtener proyectos", "Cargar tabla", "Cargar parámetros") + card
  "Proyectos con '{origin}'" con los slugs como pills ordenadas.

## Criterios de aceptación
- [x] "Obtener proyectos" trae solo la lista de repos (sin tabla ni params).
- [x] "Cargar tabla" trae la tabla sin cargar automáticamente los params.
- [x] "Cargar parámetros" trae los params bajo demanda.
- [x] `ng build` sin errores y tests backend verdes.

## Alcance
| Archivo | Cambio |
|---|---|
| `frontend/src/app/pages/home/home.ts` | steps desacoplados + signals por paso |
| `frontend/src/app/pages/home/home.html` | botones de paso + card proyectos |

## Orden de implementación
1. Frontend: separar métodos y signals.
2. Frontend: UI de pasos + card proyectos.
3. `ng build` + `pytest`.
4. Versionado + commit + push.

## Verificación
- `ng build` (Node ≥24) → OK.
- `python -m pytest -q` → 86 passed.

## Nota de versión
Ajuste significativo → `0.13.0` → `0.14.0`.

## Estado
- [x] 1. Pasos en frontend
- [x] 2. UI de pasos + card
- [x] 3. Build + tests
- [ ] 4. Versionado + commit + push

Estado: implementado (build + tests verdes).
