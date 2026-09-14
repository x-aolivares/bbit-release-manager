/**
 * Pure utility functions for the home scan flow.
 *
 * Extracted from Home component to enable direct unit testing without
 * Angular dependency injection or mock setup.
 */

export interface FlowRepoItem {
  slug: string;
  branch_url: string;
}

export interface FlowStats {
  repos: number;
  with_pr: number;
  prod: number;
}

export interface FlowDiff {
  origin: string;
  destination: string;
  prefixes: string[];
  params: unknown[];
  removed: unknown[];
}

export interface FlowField {
  slug: string;
  field: string;
  value: unknown;
}

export interface FlowEventPayload {
  repos?: FlowRepoItem[];
  stats?: FlowStats;
  diff?: FlowDiff;
  field?: FlowField;
  error?: string;
}

export type RepoSortKey = 'name' | 'status';
export type RepoSortDir = 'asc' | 'desc';

export interface SortableRepo {
  slug: string;
  name?: string;
  error?: string | null;
  match_tag?: Record<string, string | null>;
  deploys?: Record<string, { status?: string } | null>;
}

/**
 * Sort value rank for a repo in a given env column (badge state).
 * Missing tag first (no deploy attempted), then success, then the rest.
 */
function statusRank(repo: SortableRepo, env: string): string {
  if (repo.error) return 'zz';
  const tag = repo.match_tag?.[env.toLowerCase()] ?? null;
  if (!tag) return 'a-sin-tag';
  const status = repo.deploys?.[env.toLowerCase()]?.status ?? '';
  switch (status) {
    case 'success':
      return 'b-ok';
    case 'running':
    case 'queued':
      return 'c-corriendo';
    case 'on_hold':
      return 'd-esperando';
    default:
      return status ? 'e-' + status : 'f-sin-deploy';
  }
}

/**
 * Sort the repo rows by a table header key.
 *
 * - `name`: sorts by repo name (tie-break by slug).
 * - `status`: sorts by the badge state of the given env column.
 *
 * The array is copied (previous rows are untouched) and ties break by slug
 * so the order is stable regardless of the SSE completion order.
 */
export function sortRepos<T extends SortableRepo>(
  rows: T[],
  key: RepoSortKey,
  dir: RepoSortDir,
  env?: string,
): T[] {
  const factor = dir === 'asc' ? 1 : -1;
  return [...rows].sort((a, b) => {
    const va = key === 'status' && env ? statusRank(a, env) : (a.name || a.slug).toLowerCase();
    const vb = key === 'status' && env ? statusRank(b, env) : (b.name || b.slug).toLowerCase();
    if (va !== vb) {
      return va < vb ? -factor : factor;
    }
    return a.slug.localeCompare(b.slug);
  });
}

/**
 * Returns the URL to navigate to when clicking a repo name.
 * Points to the branch URL (origin branch) rather than the repo root.
 *
 * @throws {Error} if repo has no branch_url
 */
export function repoUrl(repo: FlowRepoItem): string {
  if (!repo.branch_url) {
    throw new Error(`Repo '${repo.slug}' has no branch_url`);
  }
  return repo.branch_url;
}

/**
 * Build the URL for the batch flow endpoint.
 */
export function buildFlowUrl(params: {
  origin: string;
  dest: string;
  prefixes: string;
  projectPrefixes: string;
  exclude: string;
  scanMode: string;
  force: number;
  repos?: string[];
  withTags?: boolean;
  withDiff?: boolean;
}): string {
  let url = `/api/flow?origin=${encodeURIComponent(params.origin)}&destination=${encodeURIComponent(params.dest)}&prefixes=${encodeURIComponent(params.prefixes)}&project_prefixes=${encodeURIComponent(params.projectPrefixes)}&exclude=${encodeURIComponent(params.exclude)}&mode=${params.scanMode}&force=${params.force}&with_tags=${params.withTags ? 1 : 0}&with_diff=${params.withDiff === false ? 0 : 1}`;
  if (params.repos && params.repos.length) {
    url += `&repos=${encodeURIComponent(params.repos.join(','))}`;
  }
  return url;
}

/**
 * Process a single SSE event and return the action to apply.
 *
 * Returns a discriminated union describing what changed, so the caller
 * can update signals without parsing logic in the component.
 */
export function processSseEvent<T extends FlowRepoItem>(
  eventType: string,
  data: string,
  currentRepos: T[],
): { type: 'repo'; repos: T[] }
  | { type: 'stats'; stats: FlowStats }
  | { type: 'diff'; diff: FlowDiff }
  | { type: 'field'; field: FlowField }
  | { type: 'error'; message: string }
  | { type: 'done' }
  | null {
  const parsed = JSON.parse(data) as FlowEventPayload;
  switch (eventType) {
    case 'repo':
      return { type: 'repo', repos: [...currentRepos, parsed as unknown as T] };
    case 'stats':
      return { type: 'stats', stats: parsed as unknown as FlowStats };
    case 'diff':
      return { type: 'diff', diff: parsed as unknown as FlowDiff };
    case 'field':
      return { type: 'field', field: parsed as unknown as FlowField };
    case 'error':
      return { type: 'error', message: parsed.error ?? 'Stream error' };
    case 'done':
      return { type: 'done' };
    default:
      return null;
  }
}
