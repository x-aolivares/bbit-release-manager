# Route Plan — v12 · Estado 'sin cambios' en la celda PR de la tabla

## Objetivo
Que en la tabla de repos se vea de forma descriptiva cuando la rama origen
existe pero no tiene cambios contra la rama destino: en lugar del botón
"Sin PR a {dest}" (que fallaría al crear), mostrar un texto muteado
"Sin cambios contra {dest}".

## Contexto
- `/scan` ya reporta `pr`, `behind`, `commit` por repo; no trae si la rama
  tiene commits por delante de `destination`.
- `client.has_commits_ahead` (GET /commits/{branch}?exclude=base, pagelen=1)
  determinaba eso; tolera errores devolviendo `True` (fail-open) y lanza
  `BitbucketAuthError`. El guard de `create_pr` ya lo usa.
- Con PR abierto no tiene sentido el mensaje → no se calcula (ahorra una
  llamada por repo) y `no_changes` va `False`.

## Cambios propuestos
- **`src/web/api/repos.py`**: en `_repo_scan`, si no hay PR abierto, computar
  `no_changes = not has_commits_ahead(...)` y sumarlo al payload del repo.
- **`frontend/.../home.ts`**: `no_changes?: boolean` en `ScanRepo`.
- **`frontend/.../home.html`**: en la celda PR, si `repo.no_changes` mostrar
  `<span class="bb-muted">Sin cambios contra {{ destination }}` con tooltip,
  sin botón; si no, se mantiene el botón "Sin PR a {dest}".

## Criterios de aceptación
- [x] Payload de `/scan` incluye `no_changes` por repo.
- [x] Con PR abierto `no_changes` es `False` y no hay llamada extra.
- [x] Celda PR muestra texto muteado "Sin cambios contra {dest}" sin botón.
- [x] Tests verdes.

## Alcance
| Archivo | Cambio |
|---|---|
| `src/web/api/repos.py` | `no_changes` en `_repo_scan` |
| `frontend/src/app/pages/home/home.ts` | campo `no_changes` |
| `frontend/src/app/pages/home/home.html` | texto muteado en celda PR |
| `tests/test_web_api.py` | stubs `has_commits_ahead` + aserción `no_changes` |

## Orden de implementación
1. `_repo_scan` + payload.
2. Frontend (interface + celda).
3. Tests.
4. Versionado `0.9.2 → 0.9.3` + commit `feat:` + push.

## Verificación
- `python -m pytest` (suite verde).
- `ng build` sin errores.

## Nota de versión
Ajuste pequeño → `0.9.2` → `0.9.3`.

## Estado
- [x] 1. Backend
- [x] 2. Frontend
- [x] 3. Tests
- [x] 4. Versionado + commit + push

Estado: implementado.