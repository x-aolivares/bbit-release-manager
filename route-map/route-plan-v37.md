# Route Plan — v37 · Borrado selectivo de sesiones (Limpiar todo / Limpiar por sesión)

## Objetivo

Separar el botón de limpieza del sidebar en dos acciones: "Limpiar todo"
(vacía el historial y toda la DB) y un botón "Limpiar" por sesión (borra solo
esa sesión del historial local y sus registros asociados en la DB). Ambas
pegan al mismo endpoint `DELETE /api/cache` con cantidades distintas de
elementos en la lista, de forma retrocompatible (sin body = limpia todo).

## Contexto

En v36 se creó `DELETE /api/cache` que siempre hace `clear_all()`. El sidebar
de sesiones tiene un único botón "Limpiar" que emite `historyCleared`, y cada
sesión tiene un botón con icono de trash que emite `sessionDeleted` pero solo
borra del localStorage del navegador. El usuario pide un borrado granular:
una acción global y una por sesión, compartiendo contrato HTTP.

El backend ya tiene `cache.invalidate(source, target, details)` (cache.py:410)
que elimina la/la sesión puntual por fingerprint (is_details con
`repositories.excluded/prefixes`) y sus requests asociados — exactamente el
shape que matchea `find_session` para una config de fron.

## Cambios propuestos

1. `bbit_release/web/api/repos.py` — `DELETE /api/cache`:
   - Sin body (o `{"sessions": []}`) → `clear_all()` (retrocompatibilidad total).
   - Con `{"sessions": [{origin, destination, project_prefixes, exclude}, ...]}`
     → por cada sesión llamar `cache.invalidate(...)` y devolver desglose
     `{"sessions": n, "requests": n}` contado antes/después por sesión.

2. `frontend/src/app/components/session-sidebar/session-sidebar.html`:
   - Botón del header: "Limpiar" → "Limpiar todo".
   - Botón por sesión: icono trash → texto "Limpiar".

3. `frontend/src/app/pages/home/home.ts`:
   - `onHistoryCleared()` ya hace `DELETE /api/cache` sin body (limpia todo).
   - `onSessionDeleted(id)`: ademas de `sessionHistory.delete(id)`, cargar la
     session con `sessionHistory.load(id)` y llamar a `DELETE /api/cache` con
     `{sessions: [{origin, destination, project_prefixes, exclude}]}`.

## Criterios de aceptación

- [ ] "Limpiar todo" vacía historial local Y toda la DB (comportamiento v36 intacto).
- [ ] "Limpiar" en una sesión borra esa sesión de `init_sesion` y sus `request`, pero NO otras sesiones de la DB ni el `service_call`.
- [ ] Llamar al endpoint sin body sigue limpiando todo (retrocompatible).
- [ ] `pytest tests/ -q` verde.
- [ ] Build Angular sin errores.

## Alcance

| Archivo | Cambio |
|---|---|
| `bbit_release/web/api/repos.py` | `DELETE /api/cache` con body opcional de sesiones |
| `frontend/src/app/components/session-sidebar/session-sidebar.html` | Botón "Limpiar todo" + botón "Limpiar" por sesión |
| `frontend/src/app/pages/home/home.ts` | `onSessionDeleted` pega al endpoint con su sesión |
| `tests/test_web_api.py` | Tests del endpoint sin body y con sesión puntual |
| `pyproject.toml` | Bump |

## Orden de implementación

1. Endpoint con body opcional en repos.py.
2. Tests del endpoint (sin body / con sesión).
3. HTML del sidebar: "Limpiar todo" + texto "Limpiar" por sesión.
4. `onSessionDeleted` conecta el endpoint.
5. Build + bump + commit + push.

## Verificación

```bash
python -m pytest tests/ -q
cd frontend && npm run build -- --configuration development
```

## Nota de versión

Nueva capacidad (borrado selectivo) manteniendo comportamiento previo →
bump `0.25.0` → `0.26.0`, commit `feat:`.

## Estado

- [x] Endpoint con body opcional y conteo por sesión
- [x] Tests del endpoint (sin body / con sesión)
- [x] Sidebar: "Limpiar todo" + botón "Limpiar" por sesión
- [x] `onSessionDeleted` pega al endpoint con su sesión
- [x] Tests y build verdes (158 tests)
- [x] Bump `0.26.0`

Estado: implementado