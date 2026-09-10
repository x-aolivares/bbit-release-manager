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

export interface FlowEventPayload {
  repos?: FlowRepoItem[];
  stats?: FlowStats;
  diff?: FlowDiff;
  error?: string;
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
}): string {
  let url = `/api/flow?origin=${encodeURIComponent(params.origin)}&destination=${encodeURIComponent(params.dest)}&prefixes=${encodeURIComponent(params.prefixes)}&project_prefixes=${encodeURIComponent(params.projectPrefixes)}&exclude=${encodeURIComponent(params.exclude)}&mode=${params.scanMode}&force=${params.force}`;
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
    case 'error':
      return { type: 'error', message: parsed.error ?? 'Stream error' };
    case 'done':
      return { type: 'done' };
    default:
      return null;
  }
}
