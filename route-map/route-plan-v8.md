# Route Plan — v8 · No intentar crear PR cuando no hay cambios entre ramas

## Objetivo
Si no hay cambios entre la rama origen y la destino, el sistema debe responder
`no se puede crear el PR` de forma definitiva y no reintentar la creación en
cada corrida (bucle de fallos).

## Contexto
- `POST /prs/create-missing` recorre repos sin PR abierto y llama a
  `create_pr`; si la rama origen no tiene commits nuevos frente a la destino,
  Bitbucket rechaza el alta y el repo cae en `failed` **siempre** que se
  ejecute la acción → el usuario "se queda en bucle" viendo el mismo fallo.
- `POST /pr` (PR individual por repo) tiene el mismo problema: el botón
  "Sin PR" reintenta contra Bitbucket y falla igual.
- Bitbucket no permite crear un PR sin cambios entre source y destination.

## Cambios propuestos
- **`src/bitbucket/client.py`**: nuevo método `has_commits_ahead(slug, branch,
  base)` → `GET /commits/{branch}?exclude={base}&pagelen=1`; `False` si la rama
  no tiene commits que la base no tenga (no hay nada que PR-ear). Ante error de
  red/API (no auth) devuelve `True` para no bloquear la resolución del cliente.
- **`src/web/api/repos.py`**:
  - `POST /pr`: antes de crear, si `has_commits_ahead` es falso → `400` con
    `{"ok": False, "error": "...no hay cambios entre <origin> y <dest>...", "no_changes": True}`.
  - `POST /prs/create-missing`: acumular en `no_changes` los repos sin cambios
    y saltarlos (no llamar a `create_pr`).
- **`frontend/.../home.ts`**: el mensaje de `syncPrs('create')` incluye el
  bucket `sin cambios: ...`.

## Criterios de aceptación
- [x] Un repo sin commits adelante de la destino no llama a `create_pr`.
- [x] `POST /pr` con `no_changes` devuelve 400 y mensaje claro, sin intentar crear.
- [x] Tests verdes.

## Alcance
| Archivo | Cambio |
|---|---|
| `src/bitbucket/client.py` | `has_commits_ahead` |
| `src/web/api/repos.py` | guard previo en `/pr` y bucket `no_changes` |
| `frontend/src/app/pages/home/home.ts` | mensaje con `sin cambios` |
| `tests/test_web_api.py`, `tests/test_bitbucket_client.py` | stubs + casos |

## Orden de implementación
1. `has_commits_ahead`. 2. Guards en API. 3. Frontend. 4. Tests. 5. Verificación.
6. Versionado + commit + push.

## Verificación
- `python -m pytest` (tests verdes).

## Nota de versión
Fix de flujo PR → `0.7.6` → `0.7.7`.

## Estado
- [x] 1. `has_commits_ahead`
- [x] 2. Guards en API
- [x] 3. Frontend
- [x] 4. Tests
- [x] 5. Verificación
- [x] 6. Versionado + commit + push

Estado: implementado.