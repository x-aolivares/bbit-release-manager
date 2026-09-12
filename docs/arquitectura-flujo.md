# Arquitectura del flujo — rediseño lean (estado claro)

Decisión de prioridad: **pipeline lean, con estado claro por etapa**. Objetivo:
un solo pasaje de metadata por repo, contenido bajo demanda (o motor git),
CERO consultas duplicadas y estados con semántica única.

---

## 1. Mapa del flujo actual

Pipeline de `GET /api/flow/stream` (y su gemelo no-stream `GET /api/flow`):

| Etapa | Código | Consultas (por repo) | Problema |
|---|---|---|---|
| Resolve | `_branch_repos_cached` → `repos_with_branch` | `list_repos` (1 global) + `resolve_branch` exacto (1/repo) | OK tras quitar variantes |
| Scan meta | `_repo_scan` | `find_pr` (1) + `commit_for_branch` (1) + `tags_on_commit` (1) | OK |
| Scan re-check | `_repo_scan` (step 5) | `commit_for_branch` de nuevo si hay PR (1) | **DUPLICADO** (mismo head) |
| Scan sync | `_repo_scan` (step 4) | `has_commits_ahead` si no hay PR (1) → `no_changes` | Estado difuso; eliminado |
| CircleCI batch | `_repo_scan` | `project_id` (1) + `deploys_for_tags` = pipeline→workflow→jobs × K (3K) | OK |
| CircleCI por-env | `_repo_scan` `_env_deploy` | `deploy_for_tag` = pipeline→workflow→jobs × E (3E) | **DUPLICADO** del batch |
| Master params | `_resolve_masters` (hilo paralelo) | cache o `_read_all_params` = snapshot (1) o list+raws (1+N) | Solo si diff los usa |
| Diff | `_compute_diff` | `_branch_repos_cached` de NUEVO + `client.diff` (1) + raws candidatos (2M o snapshot cache) | **Re-resolver rama** (linea 1689) |
| Enrich | `_enrich_diff_ssm` | 0 (cache SsmStore) | OK |
| Writes | `/pr`, `/prs/*`, `/generate_tags` | `find_pr` (1) + `has_commits_ahead` (1) + create (1) | Re-consultan lo ya resuelto |

**Duplicados confirmados:** (a) commit head del scan, (b) ciclo CircleCI
batch + por-env, (c) re-resolución de `_branch_repos_cached` en el diff.

**Estado actual:** dict `ctx` mutable compartido entre fases (sin tipar),
campos `no_changes`, `synced`/`visible` y `resolved_branch` con semántica
entrecruzada.

## 2. Arquitectura objetivo

Pipeline **por etapas puras**, cada una recibe el estado de la anterior y
devuelve un estado explícito. Nada se resuelve dos veces.

### 2.1 Estado por repo (dataclass, no dict)

```python
@dataclass
class RepoState:
    slug: str
    workspace: str
    resolved_branch: str          # rama efectiva (exacta o "")
    head: str                     # sha del commit de la rama
    pr: dict | None               # resultado de find_pr
    tags: list[dict]              # tags en head
    deploys: dict[str, DeployJob | None]  # por env, del batch único
    ci_project: str | None
```

```python
@dataclass
class ScanContext:
    scope: ReleaseScope           # origin, destination, prefixes, proj, exclude, mode
    repos: dict[str, RepoState]   # solo repos con rama
    master_params: dict[str, set] # {slug: {paths}} — cacheado, se llena a demanda
    diff: DiffResult | None       # cache del diff content por repos
```

### 2.2 Etapas

1. **Resolve** (una sola vez, cacheado)
   - `scope.repos = branch_repos(origin)` — 1 `resolve_branch` exacto por repo.
   - El resultado **se reutiliza** en el diff (eliminar la re-llamada de `_compute_diff:1689`).

2. **Meta scan** (una sola vez por repo, paralelo) — `scan_repo(repo) -> RepoState`
   - `find_pr` + `commit_for_branch` + `tags_on_commit` — 3 llamadas, **un solo head** (no hay re-check; `match_commit` se calcula del PR en el estado).
   - **CircleCI en una sola pasada**: `deploys_for_env(repo, tags, prefixes)` que por cada env matchea el tag y resuelve una ÚNICA pipeline→workflow→jobs (por tag, no por env+tag). Elimina `_env_deploy`/`deploy_for_tag` por-env (ahorra ~3E llamadas/repo).
   - Eliminado `has_commits_ahead`/`no_changes`: el estado "sin cambios" se infiere del diff (exclusivamente en la etapa Content), no del scan. La UI deja de mostrar "Sin cambios contra master" como estado del scan.

3. **Content** (bajo demanda; motor git cuando hay clone)
   - diff por repo (1) → archivos con indicio SSM.
   - `master_params` solo para repos con candidatos, cacheados (`cache.get_master`).
   - raw de candidatos vía snapshot (0 extra) o `raw_file` (2M).
   - **Reusa el Resolve**: el diff recibe `scope.repos`, nunca re-llama a branch resolution.
   - Con el motor local-git: diff, master params y raws salen de la object database (0 llamadas).

4. **Enrich** — overlay SSM desde `SsmStore` (cache local). Sin cambios.

5. **Writes** — reusan `scope.repos`.
   - `create_pr` valida "no hay cambios" contra el diff cacheado (0 llamadas extra);
     si no hay diff (POST directo), cae al error de Bitbucket (sin `has_commits_ahead`).
   - `/prs/create-missing`: usa `RepoState.pr` en vez de re-`find_pr`.

### 2.3 Costo objetivo (por repo, con rama)

| Origen | Meta scan | CircleCI | Content |
|---|---|---|---|
| API puro | 3 (pr, commit, tags) | 1 + 3×K | diff (1) + master (0-1) + raws (0-2M) |
| Motor git | 3 (igual, API) | 1 + 3×K | **0** |

Se eliminan: `has_commits_ahead`/`no_changes` (estado + llamada), re-check de
commit, 3×E de CircleCI duplicado, re-resolución de rama en el diff.

## 3. Decisiones ya adoptadas

- Ramas con coincidencia **exacta** (sin variantes `-Vn`).
- **Sin** `has_commits_ahead` en el scan; el estado "sync" deja de existir en el scan.
- CircleCI: **un solo batch** por tags (Pendiente confirmar: backfill de caché de pipelines).

## 4. Plan de implementación por fases (sin romper)

1. `ScanContext`/`RepoState` tipados + `scan_repo` puro (remueve double
   commit-head y CircleCI por-env). Commit `refactor(BBIT-{N})`.
2. Eliminar `has_commits_ahead` del scan + frontend (campo `no_changes`,
   estado "Sin cambios contra", test `stats` sin `synced`). Commit `feat`/`fix`.
3. Diff reusa Resolve (sacar `_branch_repos_cached` de `_compute_diff:1689`).
4. `create_pr`/`create_missing_prs` reusan `RepoState` (Ctrl+C).
5. Bump de versión + CI (pytest + ng build/test en `feature/**`).

## 5. Backlog de decisiones (para la issue BBIT)

- Confirmar alcance del borrado de `has_commits_ahead` en Writes (`/pr` y
  `/prs/create-missing`): mantener guard con mensaje amigable (recomendado)
  vs. eliminar y dejar el error crudo de Bitbucket.
- Semántica de UI tras quitar `no_changes`: todo repo sin PR muestra botón
  "Sin PR a {dest}" (el "sin cambios" se ve al intentar crear).
- Backfill de cache de CircleCI pipelines para no re-paginar en writes.