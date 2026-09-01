# Route Plan — v20 · Clasificación SSM vs productivo + caché de proyectos + rama por prefijo

## Objetivo

Corregir la clasificación de parámetros SSM para que el estado `reutilizado`
se calcule contra el **master de TODOS los repos del workspace** (filtrados por
proyecto), no solo contra los repos que traen la rama origen. Así, un repo
totalmente nuevo que referencia parámetros ya productivos queda bien etiquetado
como `reutilizado` (productivo), no como `nuevo`.

Además: cachear el descubrimiento de proyectos (para no repetir N llamadas),
permitir una blacklist de proyectos, forzar el refresco con un checkbox, y
resolver la rama origen por prefijo (`release/REP-325073` → matchea
`release/REP-325073-V2`).

## Contexto

- `/api/diff` obtiene `repos = repos_with_branch(origin, prefixes=proj)`, es
  decir **solo repos que tienen la rama origen**. Luego `dest_by_repo[slug]` se
  llena con los master de esos repos únicamente, y `classify_ssm` arma
  `global_dest` con esos masters.
- **Bug**: si un parámetro existe en master de un repo que NO tiene la rama
  origen, no entra en `global_dest`, y el parámetro se marca `nuevo` cuando en
  realidad es productivo reutilizado.
- `extract_ssm_params` filtra los paths `{{resolve:ssm:...}}` con `startswith`
  sin límite de segmento (pasa `/configfoo`, `/commonwealth`), inconsistente
  con el regex de paths pelados que sí exige `/config/`.
- El botón "Obtener proyectos" llama `/api/repos` y hoy **no cachea**; cada
  consulta repite el descubrimiento de repos (que además son N llamadas con la
  verificación de rama en `repos_with_branch`).
- La rama origen hoy se busca literal; si se tipea `release/REP-325073` solo se
  matchea esa rama exacta, no `release/REP-325073-V2`.

## Cambios propuestos

### Clasificación SSM correcta (core)

- En `/api/diff`: separar el universo de repos en dos
  - `branch_repos` = repos con la rama origen (para leer `origin_params`).
  - `master_repos` = TODOS los repos filtrados por proyecto (para leer
    `dest_params`/master). `master_repos` ⊇ `branch_repos`.
- Leer `origin_params` (release) desde `branch_repos` y `dest_params` (master)
  desde `master_repos`.
- `global_dest` = unión de los masters de todos los repo.
- Estados por param (solo params que aparecen en algún release):
  - `nuevo`: en release, no en ningún master.
  - `reutilizado`: en release de algún repo y en master de algún repo (productivo
    reutilizado para la iniciativa).
- El caso "solo master" (productivo no incluido en esta iniciativa) **no se
  muestra** en la tabla.

### Límite de prefijo (bug)

- Unificar `_match_prefix` con el mismo criterio del regex de paths pelados:
  `path == "/{slug}"` o `path.startswith("/{slug}/")` (límite de segmento).
- Tests: `/configfoo`, `/commonwealth`, `/config_1` en ambas formas (pelada y
  `{{resolve:ssm:...}}`) deben quedar excluidos.

### Caché de proyectos

- `/api/repos` cachea la lista de repos (+ quién tiene la rama) en memoria por
  sesión, con TTL. Nueva query `force=1` para invalidar y volver a consultar.
- New query `exclude=` para blacklist de slugs que no se buscan.
- El parámetro fuerza/caché se expone también al resto de endpoints que hacen
  descubrimiento (scan/diff) si preserva retrocompatibilidad.

### Rama origen por prefijo

- `repos_with_branch` (u helper equivalente) acepta matcheo por prefijo: si la
  rama no existe literal, se buscan ramas que empiecen con `origin/` y se
  resuelve la más reciente (máxima versión) como rama efectiva.

## Criterios de aceptación

1. Un param en release de un repo y en master de un repo SIN la rama origen se
   clasifica `reutilizado`, no `nuevo`.
2. Un param solo en release (sin master en ningún repo) se clasifica `nuevo`.
3. `{{resolve:ssm:/configfoo/...}}` y `/commonwealth/...` no se extraen con
   prefijo `/config,/common`.
4. `/api/repos` con `force=0` no repite el descubrimiento dentro del TTL;
   `force=1` sí lo refresca.
5. Slugs en `exclude=` no aparecen en proyectos ni se escanean.
6. Rama `release/REP-325073` resuelve `release/REP-325073-V2` como válida.
7. Tabla y obtención de parámetros siguen funcionando como antes (retrocompat).

## Alcance

| Archivo | Cambio |
|---|---|
| `bbit_release/scan/params.py` | límite de segmento en `_match_prefix` |
| `bbit_release/web/api/repos.py` | clasificación contra master global; caché; force; exclude; rama prefijo |
| `bbit_release/bitbucket/client.py` | helper rama por prefijo si hace falta |
| `bbit_release/web/session.py` | caché de proyectos por sesión |
| `frontend/src/app/pages/home/home.ts` | filtro params por proyecto; checkbox fuerza; blacklist |
| `frontend/src/app/pages/home/home.html` | rediseño sección proyectos + controles |
| `tests/test_params.py` | límite de prefijo; clasificación |
| `tests/test_web_api.py` | /api/diff vs master global; /api/repos cache/force/exclude |

## Orden de implementación

1. Límite de prefijo + tests (chico, aislado).
2. Clasificación SSM vs master global + tests.
3. Rama origen por prefijo + tests.
4. Caché de proyectos + force + exclude en `/api/repos` + tests.
5. Frontend (filtro params, checkbox fuerza, blacklist) y rediseño.
6. Versionado + commit + push.

## Verificación

- `python -m pytest tests/ -q` verde.
- Si el build de Angular corre: `npm run build` en `frontend/`.
- Verificación manual del flujo en dev.

## Nota de versión

Ajuste significativo (cambia comportamiento de clasificación y agrega caché/
filtros) → `0.16.1 → 0.18.0` (izquierdo), commit `feat:`/`fix:` según el caso.

## Estado

- [x] Límite de prefijo
- [x] Clasificación vs master global
- [x] Rama origen por prefijo
- [x] Caché + force + exclude
- [x] Frontend (fuerza caché + blacklist + filtro params por proyecto)
- [ ] Rediseño sección "Proyectos" / vistas (siguiente iteración)

Estado: implementado en v0.17.0 (rediseño queda como trabajo pendiente en una
iteración posterior, tal como lo indicó el usuario: primero funcional, luego
acomodar el diseño).
