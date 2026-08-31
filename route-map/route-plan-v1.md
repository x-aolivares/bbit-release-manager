# Route Plan — v1 · Optimización de consultas al API

## Objetivo

Reducir la cantidad de llamadas y el tiempo de pared del flujo `scan` + `diff`
sin cambiar el resultado funcional: mismos repos, ramas, PRs, tags, deploys y
parámetros SSM.

## Contexto

El flujo actual es **secuencial** y repite trabajo:

| Fase | Llamada | Por ... |
|---|---|---|
| 1 | `GET /repositories/{workspace}` (paginado) | workspace |
| 1 | `GET .../{slug}/refs/branches/{branch}` | repo (descubrimiento) |
| 2 | `GET .../commits/{branch}` (hash) | repo con la rama |
| 2 | `GET .../commits/{base}?exclude={branch}` (behind) | repo con la rama |
| 2 | `GET .../commits/{hash}/refs/branches/tags` | repo con la rama |
| 2 | `GET .../pullrequests?state=OPEN&source_branch=...` | repo con la rama |
| 2 | CircleCI: deploys por tag/commit | repo con la rama |
| 3 | `GET .../diff/{to}?from={from}` (texto plano) | repo con la rama |
| 3 | `GET .../raw/{ref}/{path}` (origen y destino) | archivo cambiado |

### Piso del problema (no se puede bajar)

- La API de Bitbucket Cloud **no filtra repos por rama** en el listing y **no tiene
  endpoint batch**. El descubrimiento es N llamadas mínimo y el estado por repo
  es M llamadas mínimo.
- Se elimina lo **redundante** y se **superpone en el tiempo** el resto.

## Cambios propuestos

1. **Reusar el hash del PR** (elimina 1 llamada/repo): `find_pr()` devuelve
   `source.commit.hash`; si hay PR no se llama `commit_for_branch()`. Se invierte
   el orden en `scan()`: PR primero, commit solo si no hay PR.
2. **Solo pedir raw_files que aportan** (evita hasta 2×archivos/repo): si las
   líneas `+` del diff no contienen `ssm:` ni `{{resolve:`, no se pide el raw de
   origen/destino. `extract_ssm_params` se ejecuta igual sobre los que pasan el
   filtro (misma precisión).
3. **Cache corto del listing** (opcional): `list_repos()` cacheado ~5 min por
   sesión evita re-listar el workspace en scans consecutivos. Invalidable.
4. **Paralelizar fases 2 y 3** con `ThreadPoolExecutor`: una tarea por repo
   (commit/tags/PR/behind/deploys) y raw_files por archivo. `max_workers`
   acotado por el rate limit de Bitbucket (~1000 req/hora/IP).

## Criterios de aceptación

1. Con PR existente, el hash de `scan` coincide con el head de Bitbucket y no se
   ejecuta `commit_for_branch`.
2. Con un diff real, los parámetros SSM son idénticos a los actuales y las
   llamadas `raw` bajan (en diffs típicos, a 0).
3. Segundos scans no vuelven a llamar al listing dentro de la ventana de cache.
4. Para un mismo scan, el JSON de `/api/scan` y `/api/diff` es idéntico al
   secuencial y el tiempo de pared baja sustancialmente.

## Alcance

| Archivo | Cambio |
|---|---|
| `src/bitbucket/client.py` | `find_pr` con hash; cache en `list_repos` |
| `src/web/api/repos.py` | orden PR→commit en `scan`; filtro de archivos en `diff`; ThreadPool fases 2-3 |
| `tests/test_bitbucket_client.py` | `find_pr` devuelve hash; cache |
| `tests/test_web_api.py` | no se llama `commit_for_branch` con PR; diffs sin SSM no piden raw; paridad de payload |
| `src/circleci/client.py` | sin cambios |

## Orden de implementación

1. (1) `find_pr` con hash + reordenar `scan` + tests.
2. (2) filtro de raw_files + tests de precisión.
3. (4) paralelización + test de paridad de payload.
4. (3 opcional) cache de listing + test de no-re-llamada.
5. Medición en workspace real: #requests (log) y duración.

## Verificación

- `python -m pytest tests/ -q` verde en cada paso.
- Paridad de JSON entre secuencial y paralelo.
- Conteo de requests y tiempo antes/después.

## Nota de versión

Al implementar: bump a `0.5.0` en `pyproject.toml` (ajuste significativo) y
actualizar el assert de health en `tests/test_web_api.py`.

## Estado

- [x] 1. hash del PR (reusar `source.commit.hash`)
- [x] 2. filtro de raw_files por indicio SSM
- [ ] 3. cache de `list_repos` (opcional — no implementado)
- [x] 4. ThreadPoolExecutor en fases 2 y 3
- [x] medición real (requests + tiempo)

Estado: implementado (1, 2, 4) en v0.5.0. Item 3 queda pendiente (opcional).

Medición real (0.5.0, workspace real, 3 repos): `/api/scan` 3.43s, `/api/diff` 5.09s
con paridad de payload (mismos 3 params SSM; commits desde el PR, sin
`GET /commits/{branch}`). Cobertura: 46 tests verdes.