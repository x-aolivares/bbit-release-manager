import { Injectable } from '@angular/core';
import { APP_CONFIG } from './app-config';
import { RepoStore } from './repo-store';
import {
  BranchState,
  Environment,
  FlowRepo,
  FlowResponse,
  Pr,
  Repo,
  SsmParam,
} from './types';

interface RepoTableRow {
  slug: string;
  name: string;
  branch_state: BranchState;
  project?: string;
}

const REPO_TABLE: RepoTableRow[] = [
  { slug: 'bbit-trnxd-orders-api', name: 'Orders API', branch_state: 'found', project: 'Orders Platform' },
  { slug: 'bbit-trnxd-orders-web', name: 'Orders Web', branch_state: 'found' },
  { slug: 'bbit-trnxd-orders-batch', name: 'Orders Batch', branch_state: 'not_found' },
  { slug: 'bbit-trnxd-orders-ingest', name: 'Orders Ingest', branch_state: 'found' },
  { slug: 'bbit-trnxd-orders-reporting', name: 'Orders Reporting', branch_state: 'not_found' },
  { slug: 'bbit-accts-catalog-search', name: 'Catalog Search', branch_state: 'found' },
  { slug: 'bbit-accts-catalog-ingest', name: 'Catalog Ingest', branch_state: 'found' },
  { slug: 'bbit-accts-catalog-admin', name: 'Catalog Admin', branch_state: 'not_found' },
  { slug: 'bbit-accts-catalog-images', name: 'Catalog Images', branch_state: 'found' },
  { slug: 'bbit-accts-payments-core', name: 'Payments Core', branch_state: 'found' },
  { slug: 'bbit-accts-payments-gateway', name: 'Payments Gateway', branch_state: 'found' },
  { slug: 'bbit-accts-payments-refunds', name: 'Payments Refunds', branch_state: 'not_found' },
  { slug: 'bbit-accts-shipping-tracker', name: 'Shipping Tracker', branch_state: 'found' },
  { slug: 'bbit-accts-identity-auth', name: 'Identity Auth', branch_state: 'found' },
  { slug: 'bbit-accts-notifications', name: 'Notifications', branch_state: 'found' },
  { slug: 'bbit-trnxd-backend-db-scripts', name: 'Backend DB Scripts', branch_state: 'found' },
];

const FLOW_PRS: Record<string, Pr> = {
  'orders-api': { id: 42, title: 'Fix order processing bug', url: 'https://bitbucket.org/acme-workspace/orders-api/pull-requests/42' },
};

const FLOW_TAGS: Record<string, Record<string, string | null>> = {
  'orders-api': { uat: 'uat-1201', stgp: 'stgp-1188', prod: null },
};

const SSM_PARAMS: Record<string, SsmParam[]> = {
  'orders-api': [
    { name: '/config/orders/api/DB_URL', type: 'nuevo', aws_status: 'ok', values: { qa: 'db-qa', uat: 'db-uat', stgp: 'db-stgp' } },
    { name: '/config/orders/api/API_KEY', type: 'reutilizado', aws_status: 'ok', values: { qa: 'ak-qa', uat: 'ak-uat', stgp: 'ak-stgp' } },
    { name: '/common/orders/JWT_SECRET', type: 'nuevo', aws_status: 'missing', values: { qa: 'jwt-qa', uat: '', stgp: '' } },
  ],
  'orders-web': [
    { name: '/config/orders/web/APP_PORT', type: 'nuevo', aws_status: 'ok', values: { qa: '8080', uat: '8080', stgp: '8080', prod: '8080' } },
    { name: '/common/orders/JWT_SECRET', type: 'reutilizado', aws_status: 'missing', values: { qa: 'jwt-qa', uat: '', stgp: '', prod: '' } },
  ],
};

function defaultSsmParams(slug: string): SsmParam[] {
  return [
    { name: '/config/' + slug + '/DB_HOST', type: 'nuevo', aws_status: 'ok', values: { qa: 'host-qa', uat: 'host-uat', stgp: 'host-stgp' } },
    { name: '/config/' + slug + '/API_TIMEOUT', type: 'nuevo', aws_status: 'ok', values: { qa: '3000', uat: '3000', stgp: '3000' } },
    { name: '/common/' + slug + '/JWT_SECRET', type: 'reutilizado', aws_status: 'missing', values: { qa: 'jwt-qa', uat: '', stgp: '' } },
  ];
}

@Injectable({ providedIn: 'root' })
export class MockApi {
  constructor(private readonly repoStore: RepoStore) {}

  private wait<T>(payload: T): Promise<T> {
    return new Promise((resolve) =>
      setTimeout(() => resolve(structuredClone(payload)), APP_CONFIG.sim_latency_ms),
    );
  }

  getReposQuick(query: {
    origin: string;
    destination: string;
    project_prefixes: string;
    exclude: string;
  }): Promise<{ repos: Repo[]; count: number }> {
    const origin = (query.origin || '').trim();
    const destination = (query.destination || '').trim();
    const prefixes = (query.project_prefixes || '')
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean);
    const exclude = (query.exclude || '')
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean);

    const repos = REPO_TABLE.filter((r) => !exclude.includes(r.slug))
      .filter((r) => !prefixes.length || prefixes.some((p) => r.slug.startsWith(p)))
      .map((r) => {
        const base: Repo = {
          slug: r.slug,
          name: r.name,
          workspace: APP_CONFIG.workspace,
          default_branch: 'master',
          resolved_branch: r.branch_state === 'found' ? origin : '',
          branch_state: origin && destination ? r.branch_state : 'found',
          tags: this.repoStore.getRepoTags(r.slug),
        };
        if (r.project) base.project = r.project;
        base.pr = origin && destination ? this.repoStore.getPr(r.slug, origin, destination) : null;
        return base;
      });

    return this.wait({ repos, count: repos.length });
  }

  getFlow(query: {
    origin: string;
    destination: string;
    with_diff?: boolean;
    with_tags?: boolean;
  }): Promise<FlowResponse> {
    const origin = (query.origin || '').trim();
    const destination = (query.destination || '').trim();

    const repos: FlowRepo[] = Object.keys({ ...FLOW_PRS, ...FLOW_TAGS }).map((slug) => ({
      slug,
      pr: FLOW_PRS[slug] || null,
      tags: APP_CONFIG.deploy_envs.reduce<Record<string, string | null>>((acc, env) => {
        acc[env] = (FLOW_TAGS[slug] && FLOW_TAGS[slug][env]) || null;
        return acc;
      }, {}),
      has_diff: query.with_diff !== false,
    }));

    return this.wait({ origin, destination, repos, total: APP_CONFIG.total_repos });
  }

  getSsmEnvironments(): Promise<{ environments: Environment[]; deploy: string[]; default_region: string }> {
    const labels: Record<string, string> = { qa: 'QA', uat: 'UAT', stgp: 'STGP' };
    return this.wait({
      environments: APP_CONFIG.regions.map((code) => ({ code, label: labels[code] || code.toUpperCase() })),
      deploy: APP_CONFIG.deploy_envs,
      default_region: APP_CONFIG.default_region,
    });
  }

  getSsmValues(repo: string): Promise<{ repo: string; params: SsmParam[] }> {
    const params = SSM_PARAMS[repo] || defaultSsmParams(repo);
    return this.wait({ repo, params });
  }

  createPr(query: {
    repo: string;
    origin: string;
    destination: string;
    title?: string;
  }): Promise<{ slug: string; origin: string; destination: string; pr: Pr; cached: boolean; cached_ttl_hours: number }> {
    const slug = query.repo;
    const origin = query.origin;
    const destination = query.destination;
    const title = query.title || `Release: ${origin} → ${destination}`;
    const pr: Pr = {
      id: 100 + slug.length * 7,
      title,
      url: `https://bitbucket.org/acme-workspace/${slug}/pull-requests/${100 + slug.length * 7}`,
      state: 'OPEN',
    };
    this.repoStore.setPr(slug, origin, destination, pr);
    return this.wait({
      slug,
      origin,
      destination,
      pr,
      cached: true,
      cached_ttl_hours: this.repoStore.TTL_MS / 3600000,
    });
  }

  createTags(query: {
    repo: string;
    origin: string;
    destination: string;
    prefixes: string[];
  }): Promise<{ repo: string; origin: string; destination: string; tags: Record<string, string> }> {
    const slug = query.repo;
    const prefixes = query.prefixes || [];
    const origin = query.origin || 'release/REP-325073';
    const base = (slug.length * 37 + origin.length * 911) % 900 + 100;
    const tags: Record<string, string> = {};
    for (const p of prefixes) tags[p] = p + '-' + base;
    return this.wait({ repo: slug, origin, destination: query.destination || 'master', tags });
  }

  updatePrTitles(query: {
    repos: string[];
    origin: string;
    destination: string;
    title?: string;
  }): Promise<{ repos: Pr[] }> {
    const origin = query.origin;
    const destination = query.destination;
    const title = query.title || `Release: ${origin} → ${destination}`;
    return this.wait({
      repos: (query.repos || []).map((slug) => {
        const id = 100 + slug.length * 7;
        return { id, title, url: `https://bitbucket.org/acme-workspace/${slug}/pull-requests/${id}` };
      }),
    });
  }
}