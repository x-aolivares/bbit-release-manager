# Route Plan — v29 · Clasificación global de params SSM por path (release vs master)

## Objetivo

Reemplazar el criterio de clasificación de parámetros por tu **regla de negocio
global por path** (sin ARN, con conteo por cantidad de repos). El frontend conserva
**la misma forma de lista actual**, sumando un atributo `count`.

## Contexto

La lógica anterior calculaba el "added" **por repo** (`origin_params - dest_params`
de ese repo) y después clasificaba `nuevo`/`reutilizado` contra el master global.
Esto hacía que un path presente en release Y en master del MISMO repo nunca
apareciera. La regla pedida es comparar la lista de release GLOBAL contra la de
master GLOBAL, por path, sin importar el repo.

## Semántica (por path, global, sin repo)

| path en release | path en master global | Resultado (`tipo`) |
|---|---|---|
| Sí | Sí | `reutilizado` (posible actualización / reutilizando) |
| Sí | No | `nuevo` |
| No | Sí | ignorado (ya productivo, no sale) |

- Sin ARN: se ignora el sufijo `:arn` de `{{resolve:ssm:path:arn}}`; comparación solo
  por path. El item lleva `arn: ""`.
- `count` = cantidad de repos del alcance donde el path aparece en release global.

## Fuentes por modo (misma clasificación final)

- `all`: release = params de todos los archivos de cada repo en ref origen; master =
  params de todos los archivos en ref destino.
- `diff`: release = params de los archivos tocados por el diff (lado origen); master =
  barrido del ref destino (via `_resolve_master`, cacheado en `master_cache`).

## Alcance
Solo repos que traen la rama origen (`branch_repos`).

## Cambios (`bbit_release/web/api/repos.py`)

- Se reemplaza `added_by_repo`/`removed_by_repo` (diff por-repo) por:
  - `release_by_repo[slug]` = paths en release del repo.
  - `dest_by_repo[slug]` = paths en master del repo (solo paths).
- `global_dest` = union de paths en master de los repos del alcance.
- `tipo = classify_ssm(release_by_repo, global_dest)` (ya implementa la regla global).
- `params` = paths de release global, cada item `{param, arn:"", tipo, qa_value,
  repos:[...], count:n}`. Master-only fuera.
- `removed` y `repos_out` quedan como info de debug (dest - release por repo).

## Criterios de aceptación

- [x] Path en release y master del mismo repo → `reutilizado` (antes no salía).
- [x] Path solo en release → `nuevo`.
- [x] Path solo en master → no aparece.
- [x] Path en release de N repos → count=N, repos=[...].
- [x] Ambos modos (`all` y `diff`) usan la misma clasificación.
- [x] Forma del item conserva `param/arn/tipo/qa_value/repos` + `count`.

## Orden de implementación

1. Refactor modo `all`
2. Refactor modo `diff`
3. Clasificación global + count
4. Tests (reutilizado mismo repo, count multirepo, master-only)
5. Bump versión

## Verificación

```bash
pytest tests/ -v
```

## Nota de versión

Cambio de criterio de comportamiento → `0.20.2` → `0.21.0`, commit `feat:`.

## Estado

- [x] modo all / modo diff con release_by_repo
- [x] clasificación global + count
- [x] tests verdes (139/139)
- [x] versión bumpeda (0.21.0)

Estado: implementado
