# Consultas por repositorio — modo API puro (sin clones)

Costo real del flujo de release del scan/flow si **no** se clonaran los repos:
todo se resuelve contra las APIs de Bitbucket y CircleCI.

Escenario de referencia: escaneo `release/REP-325073 → master`, modo `diff`,
con CircleCI configurado y prefijos de deploy `uat/stgp/prod`.

| Leyenda | |
|---|---|
| 🔵 | Llamada que **elimina** el motor local-git (BBIT-33) |
| 🔴 | Llamada que **no** se puede evitar (sigue aunque haya clones) |

---

## 1. Nivel workspace (1 vez, no por repositorio)

| Requisito | Endpoint | Llamadas |
|---|---|---|
| Identidad | `GET /2.0/user` | 1 (al login) |
| Listar repos | `GET /2.0/repositories/{workspace}?pagelen=100&role=member` | `⌈N/100⌉` (139 repos → 2) |

## 2. Por repositorio — tenga la rama o no

| Requisito | Endpoint | Llamadas |
|---|---|---|
| 🔴 ¿Existe la rama `release/x`? (coincidencia **exacta**) | `GET /repositories/{ws}/{slug}/refs/branches/release/x` | **1** |

> Coincidencia **EXACTA**: si no existe la rama literal, el repo se descarta.
> No hay fallback a variantes `-Vn` ni listado de ramas para filtrar.
> La verificación se hace para todos los repos del workspace antes de
> clonar solo los que matchean; los que no, se detienen acá.

## 3. Por repositorio — con la rama (flujo del scan)

| Requisito | Endpoint | Llamadas |
|---|---|---|
| PR abierto source→dest | `GET .../pullrequests?state=OPEN&q=source.branch.name=…` | **1** |
| Commit head de la rama | `GET .../commits/{ramaEfectiva}` | **1** |
| Tags que apuntan al commit | `GET .../refs/tags?pagelen=100&q=target.hash="{sha40}"` | **1** (el sha completo filtra server-side) |
| 🔵 Diff release vs master | `GET .../diff/master?from=release/x` | **1** |
| 🔵 Master params (paths SSM) | tarball `GET bitbucket.org/{ws}/{slug}/get/master.tar.gz` | **1** |
| 🔵 Raw de archivos con indicio SSM (`/config`, `/common`, `{{resolve:ssm}}`) | por ref: `GET .../src/{ref}/{path}` | **2 × M** (M archivos × origen + destino) |

## 4. CircleCI — por repositorio (solo si `ci` configurado y el commit tiene tags)

| Requisito | Endpoint | Llamadas |
|---|---|---|
| Project id | `GET /project/bb/{ws}/{slug}` | **1** |
| Deploys por tag (+ env por prefijo) | por tag: `GET /project/.../pipeline?tag=…` → `GET /pipeline/{id}/workflow` → `GET /workflow/{id}/job` | **3 × K** (K = tags del commit) |

> BBIT-34: `deploys_for_envs` resuelve **una sola vez** pipelines/workflows/jobs
> por tag y deriva en la misma pasada tanto el deploy default como el deploy por
> env (prefijo). No hay loop por-env `deploy_for_tag` re-paginando los mismos
> pipelines (eliminó ~3×E llamadas duplicadas por tag).

## 5. AWS SSM

No entra en el scan: los valores se sirven desde la cache SQLite (`SsmStore`).
La llamada real (`get_parameter`) solo ocurre cuando el usuario clickea
**"Revisar SSM"** (por parámetro × ambiente). Fuera del flujo base.

---

## Ejemplo numérico — un repositorio típico

Repo con 3 tags (`uat-18`, `stgp-18`, `prod-18`), **M = 3** archivos con indicio
SSM en el diff, y PR existente.

| Origen | Llamadas Bitbucket | Llamadas CircleCI |
|---|---|---|
| **API puro** | 1 (rama) + 1 (PR) + 1 (commit) + 1 (tags) + 1 (diff) + 1 (master) + 6 (raws) = **12** | 1 (project) + 3×3 (deploys) = **10** |
| **Con el motor git (BBIT-33)** | 1 (rama) + 1 (PR) + 1 (commit) + 1 (tags) = **4** | 1 + 3×3 = **10** |

→ El motor local-git elimina las llamadas de **contenido** (diff, master, raws),
que es justo donde el conteo explota en repos con diffs grandes (`2 × M`).
Las llamadas de **metadata** (rama, PR, commit, tags) y **CircleCI** se mantienen.

## 6. Duplicación eliminada (BBIT-34)

`_repo_scan` (bbit 0.45.x) consultaba CircleCI **dos veces por los mismos tags**:

1. Batch `deploys_for_tags(repo, [tags])` → `pipeline → workflow → job` por tag.
2. Loop por-env `ci.deploy_for_tag(...)` → volvía a pedir `pipeline → workflow → job`.

Eran **~3 llamadas de más por tag** por repositorio; hoy solo zafaba porque el
cache SQLite devolvía `cache hit`. Desde 0.50.0 ese loop se eliminó: el scan
resuelve `deploys_for_envs` en **una pasada por tag** (ver §4).

---

## Resumen ejecutivo

- **API puro**: el piso irrenunciable por repo con rama es **~6–8 consultas de
  Bitbucket** (rama, PR, commit, tags, diff, master, raws) + **1–10 de CircleCI**.
- El **motor git mata las consultas de contenido** (diff, master, raws), que son las
  más costosas y sensibles al tamaño del diff.
- Las consultas del workspace (listado) y de verificación de rama son el costo
  fijo que queda aunque no se clone nada.