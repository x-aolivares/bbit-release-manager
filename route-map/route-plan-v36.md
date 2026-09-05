# Route Plan — v36 · Botón "Limpiar" vacía la DB + logs por tabla

## Objetivo

Que el botón "Limpiar" del sidebar no solo borre el historial local del
navegador, sino que también vacíe los registros del cache SQLite, con logs
por tabla (cuántos registros se eliminaron de cada una).

## Contexto

Hoy `onHistoryCleared()` (`home.ts:287`) solo llama `sessionHistory.clear()`
(localStorage del navegador). La DB `data/cache.db` queda intacta. El backend
ya tiene `invalidate_all()` (`cache.py:444`) que borra `request` +
`init_sesion`, pero no `service_call` y loguea el total agregado, sin
desglosar por tabla.

El usuario quiere: (1) el botón limpie la DB, (2) logs que muestren cuánto se
borró de cada tabla.

## Cambios propuestos

1. `bbit_release/cache.py`:
   - Nuevo método `clear_all()` que borra `request`, `init_sesion` y
     `service_call`, logueando por tabla la cantidad de filas eliminadas a
     nivel INFO (p. ej.
     `cache clear_all: 12 request(s), 3 sesion(es), 47 service_call(s)`).
   - Usar `rowcount` por DELETE para la cantidad real por tabla.

2. `bbit_release/web/api/repos.py`:
   - Nueva ruta `DELETE /api/cache` que llama `get_cache().clear_all()` y
     devuelve `{"ok": True, "cleared": {...}}` con el desglose por tabla.

3. `frontend/src/app/pages/home/home.ts`:
   - `onHistoryCleared()` (o el flujo "Limpiar") también llama a
     `DELETE /api/cache` además de `sessionHistory.clear()`.

## Criterios de aceptación

- [ ] "Limpiar" borra historial local Y registros de la DB.
- [ ] Aparece un log INFO por tabla con cantidades reales (rowcount).
- [ ] `service_call` se vacía junto con `request` e `init_sesion`.
- [ ] `pytest tests/ -q` verde.
- [ ] Build Angular sin errores.

## Alcance

| Archivo | Cambio |
|---|---|
| `bbit_release/cache.py` | `clear_all()` con logs por tabla |
| `bbit_release/web/api/repos.py` | `DELETE /api/cache` |
| `frontend/src/app/pages/home/home.ts` | Limpiar → endpoint + historial local |
| `tests/test_cache.py` | Test de `clear_all` y logs/rowcount |
| `pyproject.toml` | Bump a `0.25.0` |

## Orden de implementación

1. `clear_all()` en cache.py.
2. Endpoint `DELETE /api/cache`.
3. Front: conectar el botón Limpiar.
4. Tests + build + bump + commit + push.

## Verificación

```bash
python -m pytest tests/ -q
cd frontend && npm run build -- --configuration development
```

## Nota de versión

Cambio de comportamiento (el botón ahora borra la DB) → `0.24.2` → `0.25.0`,
commit `feat:`.

## Estado

- [x] `clear_all()` con logs por tabla
- [x] Endpoint `DELETE /api/cache`
- [x] Front conecta el botón Limpiar
- [x] `invalidate_all()` desglosa request/sesiones
- [x] Tests y build verdes (156 tests)
- [x] Bump `0.25.0`

Estado: implementado