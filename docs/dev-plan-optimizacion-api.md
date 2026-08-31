# Plan de desarrollo — Optimización de consultas al API

## Objetivo

Reducir la cantidad de llamadas y el tiempo de pared del flujo `scan` + `diff`
sin cambiar el resultado funcional: mismos repos, mismas ramas, mismos PRs,
mismos tags, mismos deploys y mismos parámetros SSM.

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
  endpoint batch** ("dame el estado de N repos"). El descubrimiento es N llamadas
  mínimo y el estado por repo es M llamadas mínimo.
- Objetivo: eliminar lo **redundante** y **superponer en el tiempo** el resto.

## Cambios propuestos

### 1. Reusar el hash del PR (elimina 1 llamada/repo)

- `find_pr()` hoy descarta `source.commit.hash` del payload del PR.
- Si el PR existe, ese campo es el head de la rama origen: no hace falta
  `GET /commits/{branch}`.
- Se invierte el orden en `scan()`: primero `find_pr`, y solo si no hay PR se
  llama `commit_for_branch()`.

**Criterio de aceptación**: con PR existente, el hash de `scan` coincide con el
head reportado por Bitbucket y no se ejecuta `commit_for_branch` para ese repo.

### 2. Solo pedir raw_files que aportan (evita hasta 2×archivos/repo)

- Hoy se piden `raw ./{path}` en origen y destino **para todo archivo** del diff.
- Guarda previa: si las líneas `+` del diff no contienen `ssm:` ni `{{resolve:`,
  el archivo no puede aportar parámetros nuevos → no se pide su contenido.
- `extract_ssm_params` se sigue ejecutando igual sobre los archivos que sí pasan
  el filtro, manteniendo la precisión actual.

**Criterio de aceptación**: con un diff real, la lista de parámetros SSM es
idéntica a la actual y el número de llamadas `raw` baja (en diffs típicos, a 0).

### 3. Cache corto del listing (opcional, baja coste de scans repetidos)

- `list_repos()` cacheado ~5 minutos por sesión evita re-listar todo el
  workspace en scans consecutivos.
- Tradeoff: repos nuevos pueden tardar ese tiempo en aparecer. Se puede
  invalidar con un flag `refresh`.

**Criterio de aceptación**: un segundo `scan` consecutivo no vuelve a llamar
al listing dentro de la ventana de cache.

### 4. Paralelizar fases 2 y 3 con `ThreadPoolExecutor`

- Fase 2: una tarea por repo (commit/tags/PR/behind/deploys) en paralelo.
- Fase 3: raw_files por archivo en paralelo, respetando el filtro del punto 2.
- Dependencias preservadas: la cuenta de `behind` y el `behind` no dependen del
  orden; los deploys ya son independientes por repo.
- **Riesgo**: rate limit de Bitbucket Cloud (~1000 req/hora/IP). Con ~12 repos y
  ~5 llamadas cada uno (~60 requests) se queda en ~6% del límite. Workspaces
  enormes o sin cache requieren limitar concurrencia (max_workers acotado).

**Criterio de aceptación**: para un mismo scan, el resultado (payload JSON) es
idéntico al secuencial y el tiempo de pared baja sustancialmente.

## Alcance

| Archivo | Cambio |
|---|---|
| `src/bitbucket/client.py` | `find_pr` devuelve `source.commit.hash`; (3) cache en `list_repos` |
| `src/web/api/repos.py` | orden PR→commit en `scan` (1); filtro de archivos en `diff` (2); `ThreadPoolExecutor` en fases 2 y 3 (4) |
| `tests/test_bitbucket_client.py` | `find_pr` devuelve el hash; cache |
| `tests/test_web_api.py` | asserts de que no se llama `commit_for_branch` con PR; diffs sin SSM no piden raw; concurrencia |
| `src/circleci/client.py` | sin cambios (ya es por repo e independiente) |

## Orden de implementación

1. **(1)** `find_pr` con hash + reordenar `scan` + tests.
2. **(2)** filtro de raw_files + tests de precisión.
3. **(4)** paralelización con ThreadPool + test de paridad de payload.
4. **(3 opcional)** cache de listing + test de no-re-llamada.
5. Medición en workspace real: conteo de llamadas antes/después y tiempo.

## Verificación

- `python -m pytest tests/ -q` verde en cada paso.
- Paridad: mismo JSON de `/api/scan` y `/api/diff` entre secuencial y paralelo.
- Medición real: número de requests (log) y duración del scan.

## Nota de versión

Cuando se implemente esto, bump a `0.5.0` en `pyproject.toml` (ajuste
significativo) y actualizar el assert de health en `tests/test_web_api.py`.