# Route Plan — v41 · `list_files` por nombre de rama (root `/src` 404 con SHA)

## Objetivo
Evitar los 404 de `GET /src/{sha}?pagelen=100` en `/api/diff` listando el árbol raíz por **nombre de rama** y reservando el SHA para `raw_file` (que sí funciona).

## Contexto
Evidencia del log real (bbit-test-01/02/03, release/REP-325073 → master, mode=diff):

```
09:31:07,280 /src/5eae7745e6eb41c2acca717ba7470f2657198e06?pagelen=100       → 404
09:31:10,289 /src/5eae7745e6eb41c2acca717ba7470f2657198e06/feature_flags.json → 200
```

El listado raíz de `/src` no resuelve un SHA crudo como ref (mismo SHA da 404 como
raíz pero 200 como ruta de archivo). Bitbucket sí resuelve nombre de rama en la
URL (el propio `GET /diff/release/REP-325073?from=master` respondió 200).

Hoy `_resolve_master` (modo `diff`) y `_scan_all` (modo `all`) intercalan refs de
tipo SHA en `list_files`, así que el listado raíz devuelve 404 → `[]` → los
master params quedan vacíos, NO se cachean (`set_master` solo corre si hay params)
y la clasificación `reutilizado`/`nuevo` queda degradada siempre.

## Cambios propuestos
| Archivo | Cambio |
|---|---|
| `bbit_release/web/api/repos.py` | `_resolve_master`: `client.list_files(repo.slug, destination)` (nombre) — el SHA (`dest_ref`) se conserva para `raw_file` vía `_read_files_params`. |
| `bbit_release/web/api/repos.py` | `_scan_all` (mode `all`): `list_files(slug, origin_name)` con `origin_name = getattr(repo, "resolved_branch", "") or origin`, y `list_files(slug, destination)`. Los SHAs de `_repo_refs` se conservan para los raws. |
| `bbit_release/bitbucket/client.py` | Sin cambios. `list_files` 404 → `[]` sigue tolerando una rama inexistente. |
| `tests/test_web_api.py` | Actualizar `test_diff_mode_all_lists_whole_repo` (assert de nombres); agregar test modo `diff`: `list_files` recibe `"master"`, `raw_file` recibe el SHA. |
| `tests/test_bitbucket_client.py` | Agregar test: `list_files` con ref `release/REP-325073` (slashes) arma URL y recursión correctamente. |
| `route-map/route-plan-v41.md` | Este plan. |
| `pyproject.toml` | `0.28.0` → `0.28.1`. |

## Criterios de aceptación
1. Modo `diff`: `list_files` de master usa el nombre de rama (`destination`), no el SHA; `raw_file` sigue con SHA y funciona.
2. Modo `all`: listados raíz con `resolved_branch or origin` y `destination`.
3. Master params se llenan y se cachean (TTL existente) → la corrida siguiente no re-paga las lecturas.
4. Rama inexistente sigue → `[]` (tolerado, sin falso error).
5. Con credenciales reales (`bbit web`): sin `404` en `/src/{rama}?pagelen=100` en el log.
6. `pytest tests/ -q` verde.

## Alcance
Solo `bbit_release/web/api/repos.py` + tests + plan + versión. No toca `client.py` ni el pipeline de CircleCI.

## Orden de implementación
1. Plan (este archivo).
2. Cambio en `_resolve_master` y `_scan_all`.
3. Tests (actualizar + nuevos).
4. Convención de commit con identificador `(RMV-{N})` en `AGENTS.md`.
5. Bump versión.
6. `pytest tests/ -q`.
7. Commit `fix(RMV-41): ...` + push.

## Verificación
- `pytest tests/ -q` (166+ casos).
- Correr `bbit web` y revisar en el log que `/src/{rama}?pagelen=100` responda 200 y desaparezcan los 404 raíz.

## Nota de versión
Bug fix → `0.28.1` (`0.1.x`).

## Estado
- [x] Plan v41 creado
- [x] Cambio `_resolve_master`
- [x] Cambio `_scan_all`
- [x] Tests actualizados + nuevos
- [x] AGENTS.md: identificador en commits
- [x] Versión 0.28.1
- [x] Tests verdes
- [x] Commit + push

Estado: implementado