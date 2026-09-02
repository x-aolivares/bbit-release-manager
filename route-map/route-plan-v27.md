# Route Plan — v27 · Fix thread-safety de la caché SQLite

## Objetivo

Corregir el `sqlite3.InterfaceError: bad parameter or other API misuse` que
revienta `/diff` cuando `_resolve_master` corre en paralelo (ThreadPoolExecutor)
y varios hilos pegan a la misma conexión SQLite.

## Contexto

`/diff` y `/scan` resuelven repos en paralelo con `ThreadPoolExecutor`. Cada
hilo llama `cache.get_master`/`cache.set_master` sobre la MISMA conexión
`self._conn` (creada con `check_same_thread=False`). SQLite no permite operar
concurrentemente sobre una misma conexión: al intercalarse los `execute` sin
protección, tira `InterfaceError`. Falla típica en `_resolve_master` →
`get_master`.

## Cambios propuestos

En `bbit_release/cache.py`:
- Agregar `threading.RLock` (`self._lock`).
- Toda operación de lectura usa `_fetchone` (cursor fresco + lock + close).
- Toda escritura usa `_execute` (cursor fresco + lock + close + commit).
- `invalidate`/`invalidate_all`/`close` envuelven su bloque en `self._lock`.
- `_create_tables` corre dentro del lock en `__init__`.

## Criterios de aceptación

- [ ] `/diff` con varios repos no tira `InterfaceError`
- [ ] Test de concurrencia (32 workers × hilos) verde
- [ ] Suite completa verde

## Alcance

| Archivo | Cambio |
|---|---|
| `bbit_release/cache.py` | Lock + cursor fresco por operación |
| `tests/test_cache.py` | `test_thread_safe_concurrent_access` |
| `pyproject.toml` | Bump a `0.20.1` |

## Orden de implementación

1. Añadir lock y refactorizar cache.py
2. Agregar test de concurrencia
3. Correr suite
4. Bump versión

## Verificación

```bash
pytest tests/ -v
```

## Nota de versión

Bug fix → `0.20.0` → `0.20.1`, commit `fix:`.

## Estado

- [x] lock + cursor fresco en cache.py
- [x] test de concurrencia
- [x] suite verde (135/135)
- [x] versión bumpeda (0.20.1)

Estado: implementado
