# Route Plan — v2 · Fix de PRs falsos en `find_pr`

## Objetivo
Corregir el escaneo que reportaba «PR existente» con PRs de otra pareja de ramas, impidiendo crear PRs nuevos (ej: `circleci-project-setup → release/REP-325073` marcaba los PRs de `release/REP-325073 → master`).

## Contexto
`find_pr()` usaba los query params `source_branch` y `destination_branch` en `GET /pullrequests`. Bitbucket Cloud **los ignora**: solo respeta `state`, devolviendo los PRs abiertos más recientes del repo sin filtrar por rama → falso positivo `pr.exists`. Verificado contra la API real:

| Query | Resultado bbit-test-01 |
|---|---|
| `source_branch`+`destination_branch` (vieja) | devuelve `#1 release/REP-325073 → master` (incorrecto) |
| sin filtro | igual (params ignorados, confirmado) |
| `q=source.branch.name="..." AND destination.branch.name="..."` | 0 (correcto) |

`q=` también matchea la pareja legítima (`release/REP-325073 → master` devuelve PR; bbit-test-03 tiene 2 porque quedó uno extra de la fase de pruebas).

## Cambios propuestos
- `src/bitbucket/client.py::find_pr`: reemplazar `source_branch`/`destination_branch` por `q='source.branch.name="{}" AND destination.branch.name="{}"'` con escape mínimo de comillas.
- `tests/test_bitbucket_client.py`: test `test_find_pr_filters_by_both_branches` que verifica el `q=` enviado y la pareja problemática como regresión.

## Criterios de aceptación
- [ ] Scan de `circleci-project-setup → release/REP-325073` da `with_pr=0` y `pr.exists=false` en todos los repos.
- [ ] Scan de `release/REP-325073 → master` sigue en `with_pr=3`.
- [ ] Tests verdes incluyendo la regresión del `q=`.

## Alcance
| Archivo | Cambio |
|---|---|
| `src/bitbucket/client.py` | filter por `q` en `find_pr` |
| `tests/test_bitbucket_client.py` | regresión del parámetro `q` |

## Orden de implementación
1. Fix en `find_pr`.
2. Test de regresión del `q`.
3. Bump `0.5.1` + assert health.
4. Verificación en vivo del scan (pareja nueva y vieja).

## Verificación
`python -m pytest tests/ -q` (47 verdes) + scan real contra el workspace con las dos parejas.

## Nota de versión
Bug fix → `0.5.0` → `0.5.1`.

## Estado
- [x] Fix en `find_pr`
- [x] Regresión `q`
- [x] Bump + tests
- [x] Verificación en vivo

Estado: implementado — `fix: filtrar PRs por ramas con q= en find_pr` (0.5.1).