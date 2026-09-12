/**
 * Unit tests for home page flow utilities.
 *
 * These test pure functions extracted from the Home component to enable
 * TDD without Angular dependency injection overhead.
 */
import { describe, it, expect } from 'vitest';
import {
  repoUrl,
  buildFlowUrl,
  processSseEvent,
  sortRepos,
  FlowRepoItem,
  SortableRepo,
} from './flow-utils';

// ─── repoUrl ────────────────────────────────────────────────────────

describe('repoUrl', () => {
  it('returns branch_url when present', () => {
    const repo: FlowRepoItem = {
      slug: 'foo-app',
      branch_url: 'https://bitbucket.org/ws/foo-app/src/branches/feat-x',
    };
    expect(repoUrl(repo)).toBe(
      'https://bitbucket.org/ws/foo-app/src/branches/feat-x',
    );
  });

  it('throws when branch_url is empty string', () => {
    const repo: FlowRepoItem = { slug: 'bar', branch_url: '' };
    expect(() => repoUrl(repo)).toThrow("Repo 'bar' has no branch_url");
  });

  it('throws when branch_url is missing', () => {
    const repo = { slug: 'baz' } as unknown as FlowRepoItem;
    expect(() => repoUrl(repo)).toThrow("Repo 'baz' has no branch_url");
  });

  it('encodes branch names with special chars in branch_url', () => {
    const repo: FlowRepoItem = {
      slug: 'svc',
      branch_url:
        'https://bitbucket.org/ws/svc/src/branches/feat%2Fauth',
    };
    expect(repoUrl(repo)).toContain('feat%2Fauth');
  });

  it('returns full URL with deep path segments', () => {
    const repo: FlowRepoItem = {
      slug: 'deep-svc',
      branch_url:
        'https://bitbucket.org/my-org/deep-svc/src/branches/feature/bugfix-123',
    };
    expect(repoUrl(repo)).toBe(
      'https://bitbucket.org/my-org/deep-svc/src/branches/feature/bugfix-123',
    );
  });
});

// ─── buildFlowUrl ───────────────────────────────────────────────────

describe('buildFlowUrl', () => {
  it('builds standard flow URL with all params', () => {
    const url = buildFlowUrl({
      origin: 'feat-x',
      dest: 'master',
      prefixes: 'uat,stgp,prod',
      projectPrefixes: 'orders,pay',
      exclude: 'legacy-api',
      scanMode: 'diff',
      force: 0,
    });
    expect(url).toBe(
      '/api/flow?origin=feat-x&destination=master&prefixes=uat%2Cstgp%2Cprod&project_prefixes=orders%2Cpay&exclude=legacy-api&mode=diff&force=0&with_tags=0',
    );
  });

  it('includes repos param when provided', () => {
    const url = buildFlowUrl({
      origin: 'feat-x',
      dest: 'master',
      prefixes: 'uat',
      projectPrefixes: '',
      exclude: '',
      scanMode: 'diff',
      force: 0,
      repos: ['repo-a', 'repo-b'],
    });
    expect(url).toContain('repos=repo-a%2Crepo-b');
  });

  it('omits repos param when array is empty', () => {
    const url = buildFlowUrl({
      origin: 'feat-x',
      dest: 'master',
      prefixes: 'uat',
      projectPrefixes: '',
      exclude: '',
      scanMode: 'diff',
      force: 0,
      repos: [],
    });
    expect(url).not.toContain('repos=');
  });

  it('sets force=1 for cache bypass', () => {
    const url = buildFlowUrl({
      origin: 'feat-x',
      dest: 'master',
      prefixes: '',
      projectPrefixes: '',
      exclude: '',
      scanMode: 'diff',
      force: 1,
    });
    expect(url).toContain('force=1');
  });

  it('sets with_tags=1 when ambientes seleccionados', () => {
    const url = buildFlowUrl({
      origin: 'feat-x',
      dest: 'master',
      prefixes: 'uat,prod',
      projectPrefixes: '',
      exclude: '',
      scanMode: 'diff',
      force: 0,
      withTags: true,
    });
    expect(url).toContain('with_tags=1');
  });

  it('sets with_tags=0 when sin ambientes', () => {
    const url = buildFlowUrl({
      origin: 'feat-x',
      dest: 'master',
      prefixes: '',
      projectPrefixes: '',
      exclude: '',
      scanMode: 'diff',
      force: 0,
      withTags: false,
    });
    expect(url).toContain('with_tags=0');
  });

  it('encodes special characters in origin', () => {
    const url = buildFlowUrl({
      origin: 'feat/with spaces',
      dest: 'master',
      prefixes: '',
      projectPrefixes: '',
      exclude: '',
      scanMode: 'diff',
      force: 0,
    });
    expect(url).toContain(
      `origin=${encodeURIComponent('feat/with spaces')}`,
    );
  });
});

// ─── processSseEvent ────────────────────────────────────────────────

describe('processSseEvent', () => {
  const mkRepo = (slug: string): FlowRepoItem => ({
    slug,
    branch_url: `https://bitbucket.org/ws/${slug}/src/branches/x`,
  });

  it('repo event appends to current list', () => {
    const existing = [mkRepo('a'), mkRepo('b')];
    const result = processSseEvent(
      'repo',
      JSON.stringify(mkRepo('c')),
      existing,
    );
    expect(result).not.toBeNull();
    expect(result!.type).toBe('repo');
    if (result!.type === 'repo') {
      expect(result!.repos).toHaveLength(3);
      expect(result!.repos[2].slug).toBe('c');
    }
  });

  it('repo event on empty list yields single item', () => {
    const result = processSseEvent(
      'repo',
      JSON.stringify(mkRepo('x')),
      [],
    );
    expect(result).not.toBeNull();
    expect(result!.type).toBe('repo');
    if (result!.type === 'repo') {
      expect(result!.repos).toHaveLength(1);
    }
  });

  it('stats event returns stats object', () => {
    const stats = { repos: 35, with_pr: 12, prod: 8 };
    const result = processSseEvent(
      'stats',
      JSON.stringify(stats),
      [],
    );
    expect(result).not.toBeNull();
    expect(result!.type).toBe('stats');
    if (result!.type === 'stats') {
      expect(result!.stats.repos).toBe(35);
      expect(result!.stats.with_pr).toBe(12);
      expect(result!.stats.prod).toBe(8);
    }
  });

  it('diff event returns diff object', () => {
    const diff = {
      origin: 'feat-x',
      destination: 'master',
      prefixes: ['uat'],
      params: [],
      removed: [],
    };
    const result = processSseEvent('diff', JSON.stringify(diff), []);
    expect(result).not.toBeNull();
    expect(result!.type).toBe('diff');
    if (result!.type === 'diff') {
      expect(result!.diff.origin).toBe('feat-x');
      expect(result!.diff.prefixes).toEqual(['uat']);
    }
  });

  it('error event returns message', () => {
    const result = processSseEvent(
      'error',
      JSON.stringify({ error: 'Sin sesion activa.' }),
      [],
    );
    expect(result).not.toBeNull();
    expect(result!.type).toBe('error');
    if (result!.type === 'error') {
      expect(result!.message).toBe('Sin sesion activa.');
    }
  });

  it('error event without error field returns default', () => {
    const result = processSseEvent('error', JSON.stringify({}), []);
    expect(result).not.toBeNull();
    expect(result!.type).toBe('error');
    if (result!.type === 'error') {
      expect(result!.message).toBe('Stream error');
    }
  });

  it('done event returns done marker', () => {
    const result = processSseEvent('done', '{}', []);
    expect(result).not.toBeNull();
    expect(result!.type).toBe('done');
  });

  it('unknown event type returns null', () => {
    const result = processSseEvent('unknown-type', '{}', []);
    expect(result).toBeNull();
  });

  it('repo event does not mutate original array', () => {
    const existing = [mkRepo('a')];
    processSseEvent('repo', JSON.stringify(mkRepo('b')), existing);
    expect(existing).toHaveLength(1);
  });

  it('repo event emits with missing optional fields (behind absent)', () => {
    const raw = { slug: 's1', branch_url: 'https://x' };
    const result = processSseEvent('repo', JSON.stringify(raw), []);
    expect(result).not.toBeNull();
    expect(result!.type).toBe('repo');
    if (result!.type === 'repo') {
      expect(result!.repos[0]).not.toHaveProperty('behind');
    }
  });

  it('stats event emits without synced key', () => {
    const raw = { repos: 10, with_pr: 5, prod: 3 };
    const result = processSseEvent('stats', JSON.stringify(raw), []);
    expect(result).not.toBeNull();
    if (result!.type === 'stats') {
      expect(result!.stats).not.toHaveProperty('synced');
      expect(result!.stats).toEqual({ repos: 10, with_pr: 5, prod: 3 });
    }
  });

  it('throws on malformed JSON', () => {
    expect(() => processSseEvent('repo', 'not-json', [])).toThrow();
  });

  it('repo event preserves all existing repos', () => {
    const existing = [mkRepo('a'), mkRepo('b'), mkRepo('c')];
    const result = processSseEvent('repo', JSON.stringify(mkRepo('d')), existing);
    if (result?.type === 'repo') {
      expect(result.repos.map((r) => r.slug)).toEqual(['a', 'b', 'c', 'd']);
    }
  });
});

// ─── sortRepos ──────────────────────────────────────────────────────

describe('sortRepos', () => {
  const repo = (
    slug: string,
    overrides: Partial<SortableRepo> = {},
  ): SortableRepo => ({
    slug,
    name: slug,
    match_tag: { uat: `${slug}-uat`, prod: `${slug}-prod` },
    deploys: { uat: { status: 'success' }, prod: { status: 'on_hold' } },
    ...overrides,
  });

  it('sorts by name ascending by default', () => {
    const rows = [repo('zapp'), repo('alpha'), repo('mid')];
    expect(sortRepos(rows, 'name', 'asc').map((r) => r.slug)).toEqual([
      'alpha',
      'mid',
      'zapp',
    ]);
  });

  it('sorts by name descending', () => {
    const rows = [repo('alpha'), repo('zapp'), repo('mid')];
    expect(sortRepos(rows, 'name', 'desc').map((r) => r.slug)).toEqual([
      'zapp',
      'mid',
      'alpha',
    ]);
  });

  it('does not mutate the input array', () => {
    const rows = [repo('b'), repo('a')];
    const before = rows.slice();
    sortRepos(rows, 'name', 'asc');
    expect(rows).toEqual(before);
  });

  it('sorts by env status: ok before running before missing tag', () => {
    const rows = [
      repo('b', { match_tag: { uat: 'b-uat' }, deploys: { uat: { status: 'queued' } } }),
      repo('a', { match_tag: { uat: 'a-uat' }, deploys: { uat: { status: 'success' } } }),
      repo('c', { match_tag: { uat: null } }),
    ];
    expect(sortRepos(rows, 'status', 'asc', 'uat').map((r) => r.slug)).toEqual([
      'c', // sin tag primero
      'a', // ok
      'b', // corriendo
    ]);
  });

  it('sorts failed repos (error) after the rest', () => {
    const rows = [
      repo('ok', { match_tag: { uat: 'ok-uat' }, deploys: { uat: { status: 'success' } } }),
      repo('bad', { error: 'crash', match_tag: { uat: 'bad-uat' } }),
    ];
    expect(sortRepos(rows, 'status', 'asc', 'uat').map((r) => r.slug)).toEqual([
      'ok',
      'bad',
    ]);
  });

  it('ties break by slug so order is stable', () => {
    const rows = [repo('b'), repo('a2'), repo('a1')];
    const out = sortRepos(rows, 'name', 'asc');
    expect(out.map((r) => r.slug)).toEqual(['a1', 'a2', 'b']);
  });
});
