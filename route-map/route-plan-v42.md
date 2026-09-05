# Route Plan — v42 · root `/src` requiere trailing slash (revert de v41)

## Objetivo
Arreglar los 404 de `GET /src/{ref}?pagelen=100` en `/api/diff` hacia `/src/{master}`:
el listado raíz de `/src` requiere **trailing slash** (`/src/{ref}/`). Se revierte el
cambio de v41 (listar por nombre de rama) y se restaura el uso de SHA con el slash.

## Contexto
v41 asumió que el root `/src` no resuelve un SHA crudo, por lo que pasó `list_files`
a nombre de rama. La verificación real con la API (probe) demostró que era incorrecto:

| URL del listado raíz | Resultado real |
|---|---|
| `/src/master` | 404 |
| `/src/master/` | **200** |
| `/src/{sha}` (ej. `5b68c666…`) | 404 |
| `/src/{sha}/` | **200** |
| `/src/release/REP-325073/` (rama con `/` en el nombre) | 404 |
| `/src/release%2FREP-325073/` (URL-encoded) | 404 |

Conclusiones con datos reales:
1. El bug de v0.28.0 era la **falta de trailing slash**, no el uso de SHA.
2. El **SHA** resuelve la raíz con trailing slash (`.endswith` de directory/file no es el problema).
3. Las **ramas con `/`** (ej. `release/REP-325073`) NO resuelven `/src` ni con slash ni
   URL-encoded; solo el SHA lo hace. Por eso v41 rompía el origin en modo `all`.

## Cambios propuestos
| Archivo | Cambio |
|---|---|
| `bbit_release/bitbucket/client.py` `list_files` | Root (`tree=""`): forzar trailing slash `/src/{ref}/`. Subdirectorios (`tree` no vacío) sin cambios. Aceptar types `commit_directory`/`commit_file` además de `directory`/`file` (match por sufijo). |
| `bbit_release/web/api/repos.py` | **Revert de v41**: `_resolve_master` y `_scan_all` vuelven a pasar el **SHA** (`dest_ref`/`origin_ref`) a `list_files`, no el nombre. El SHA + trailing slash resuelven tanto master como ramas con `/`. |
| `tests/test_bitbucket_client.py` | `list_files` root con `/`; acepta `commit_directory`/`commit_file`; ref con `/` + trailing slash raíz. |
| `tests/test_web_api.py` | Revert asserts de v41: `list_files` recibe SHA (no nombre). |
| `route-map/route-plan-v42.md` | Este plan. |
| `pyproject.toml` | `0.28.1` → `0.28.2`. |

## Criterios de aceptación
1. `list_files(slug, sha)` devuelve archivos reales contra la API (probe: master=3, release/REP-325073=6 sobre bbit-test-01).
2. `raw_file` sigue funcionando con el SHA de la rama.
3. Modo `diff`: master params se llenan y **se cachean** (`cache.set_master` ya corre si hay params).
4. `pytest tests/ -q` verde.
5. Con `bbit web` real: sin `404` en `/src/{sha}/?...` en el log, `get_master_params` ya no en cache miss.

## Alcance
Solo `list_files` en client.py + revert en repos.py + tests + plan + versión.

## Orden de implementación
1. Plan (este archivo).
2. `list_files`: trailing slash raíz + tipos `commit_*`.
3. Revert repos.py a SHA.
4. Revert/ajuste de tests v41 + actualización de tests de client.
5. Bump versión 0.28.2.
6. `pytest tests/ -q`.
7. Commit `fix(RMV-42): ...` + push.

## Verificación
- Probe real y `pytest tests/ -q`.
- `bbit web` real sin 404 raíz y con master params cacheados.

## Nota de versión
Bug fix → `0.28.2` (`0.1.x`).

## Estado
- [x] Plan v42 creado
- [x] `list_files` trailing slash raíz + tipos `commit_*`
- [x] Revert repos.py a SHA
- [x] Tests actualizados
- [x] Versión 0.28.2
- [x] Tests verdes
- [x] Commit + push

Estado: implementado