# Route Plan — v30 · Checkbox "forzar consultas" aplica a todas las peticiones

## Objetivo

Que cuando el checkbox "Forzar consultas (ignorar caché)" esté seleccionado, TODAS
las peticiones del home pasen `force=1` y folden la caché — no solo
`/api/repos`, también `/api/scan` y `/api/diff`.

## Contexto

El checkbox `forceCache` (`home.ts:139`) solo se pasaba a `loadProjects()`
(`/api/repos`, `home.ts:316`). `loadTable()` (`/api/scan`) y `loadParams()`
(`/api/diff`) no enviaban `force`, así que seguían leyendo la caché aunque el
checkbox estuviera activo. El backend ya soporta `force` en scan y diff
(`repos.py:459` y `repos.py:735`, invalidan y recomputan).

## Cambios propuestos

En `frontend/src/app/pages/home/home.ts`:
- `loadTable()`: agregar `&force=${force}` a la URL de `/api/scan`.
- `loadParams()`: agregar `&force=${force}` a la URL de `/api/diff`.

## Criterios de aceptación

- [x] Con el checkbox activo, `/api/scan` recibe `force=1`.
- [x] Con el checkbox activo, `/api/diff` recibe `force=1`.
- [x] Sin el checkbox, `force=0` (comportamiento igual que antes).
- [x] Build del frontend compila.

## Alcance

| Archivo | Cambio |
|---|---|
| `frontend/src/app/pages/home/home.ts` | `force` en URLs de scan y diff |
| `pyproject.toml` | Bump a `0.21.1` |

## Orden de implementación

1. Agregar `force` a `/api/scan`
2. Agregar `force` a `/api/diff`
3. Build frontend
4. Bump versión

## Verificación

```bash
cd frontend && npx ng build
```

## Nota de versión

Bug fix del comportamiento prometido por el checkbox → `0.21.0` → `0.21.1`,
commit `fix:`.

## Estado

- [x] force en scan
- [x] force en diff
- [x] build compila
- [x] versión bumpeda (0.21.1)

Estado: implementado
