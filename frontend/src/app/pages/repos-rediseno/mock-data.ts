// ============================================================================
// Mock de datos para la réplica 1:1 del mockup (docs/mockups/data/*.js).
// Vista "etapa 1": sin backend. Al cablear la lógica real, estos fixtures se
// reemplazan por los endpoints (repos-quick, flow, pr, tags, ssm/values,
// ssm/environments). Las formas de respuesta replican los archivos del mockup
// para no dejar nada ambiguo.
// ============================================================================

export interface MockedPr {
  id: number;
  title: string;
  url: string;
  state?: string;
}

export interface MockedRepoSource {
  slug: string;
  name: string;
  branch_state: 'found' | 'not_found';
  project?: string;
}

export interface MockedSsmParam {
  name: string;
  type: string;
  aws_status: 'ok' | 'missing';
  values: Record<string, string>;
}

// --- GET /api/flow: keys con slug CORTO (quirk del mockup) -------------------
export const MOCK_FLOW_PR_BY_SHORT: Record<string, MockedPr> = {
  'orders-api': {
    id: 42,
    title: 'Fix order processing bug',
    url: 'https://bitbucket.org/acme-workspace/orders-api/pull-requests/42',
  },
};

export const MOCK_FLOW_TAGS_BY_SHORT: Record<string, Record<string, string | null>> = {
  'orders-api': { uat: 'uat-1201', stgp: 'stgp-1188', prod: null },
};

export const FLOW_ENVS: string[] = ['uat', 'stgp', 'prod'];

// --- BB.config (data/config.js) ---------------------------------------------
export const BB_CONFIG = {
  workspace: 'acme-workspace',
  total_repos: 900,
  regions: ['qa', 'uat', 'stgp'],
  deploy_envs: ['uat', 'stgp'],
  default_region: 'uat',
  default_filters: {
    project_prefixes: ['bbit-trnxd-', 'bbit-accts-'],
    exclude: ['bbit-trnxd-backend-qa', 'bbit-trnxd-backend-db-scripts'],
  },
  default_branches: {
    origin: 'release/REP-325073',
    destination: 'master',
  },
  sim_latency_ms: 350,
};

// --- GET /api/repos-quick (data/repos-quick.js) ------------------------------
export const MOCK_REPO_SOURCES: MockedRepoSource[] = [
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

// --- Tabla `repositories` (data/repositories.js): PRs cacheados TTL 24h -------
// La primera consulta devuelve el PR sin llamar a Bitbucket.
export const MOCK_CACHED_PRS: Record<string, MockedPr> = {
  'bbit-trnxd-orders-api': {
    id: 42,
    title: 'Fix order processing bug',
    url: 'https://bitbucket.org/acme-workspace/bbit-trnxd-orders-api/pull-requests/42',
    state: 'OPEN',
  },
  'bbit-accts-catalog-search': {
    id: 87,
    title: 'Catalog: release REP-325073',
    url: 'https://bitbucket.org/acme-workspace/bbit-accts-catalog-search/pull-requests/87',
    state: 'OPEN',
  },
};

// --- r_details.tags (seed de data/repositories.js) ---------------------------
export const MOCK_TAGS_SEED: Record<string, string[]> = {
  'bbit-trnxd-orders-api': ['fargate', 'batch'],
  'bbit-trnxd-orders-web': ['fargate'],
  'bbit-trnxd-orders-batch': ['batch'],
  'bbit-trnxd-orders-ingest': ['step-function'],
  'bbit-trnxd-orders-reporting': ['batch'],
  'bbit-accts-catalog-search': ['fargate'],
  'bbit-accts-catalog-ingest': ['step-function'],
  'bbit-accts-catalog-admin': ['fargate'],
  'bbit-accts-catalog-images': ['workflow'],
  'bbit-accts-payments-core': ['fargate'],
  'bbit-accts-payments-gateway': ['fargate', 'workflow'],
  'bbit-accts-payments-refunds': ['step-function'],
  'bbit-accts-shipping-tracker': ['fargate'],
  'bbit-accts-identity-auth': ['workflow'],
  'bbit-accts-notifications': ['step-function', 'fargate'],
  'bbit-trnxd-backend-db-scripts': ['batch'],
};

// --- POST /api/tags: tag estable derivado de la resolución de la rama -------
// base = (slug.length * 37 + origin.length * 911) % 900 + 100
export function tagFor(slug: string, origin: string, env: string): string {
  const base = (slug.length * 37 + origin.length * 911) % 900 + 100;
  return `${env}-${base}`;
}

// --- POST /api/pr · POST /api/prs/update-titles: id/url derivados ----------
// id = 100 + slug.length * 7
export function prFor(slug: string, title: string): MockedPr {
  const id = 100 + slug.length * 7;
  return {
    id,
    title,
    url: `https://bitbucket.org/${BB_CONFIG.workspace}/${slug}/pull-requests/${id}`,
    state: 'OPEN',
  };
}

// --- GET /api/ssm/values (data/ssm-values.js) -------------------------------
const __ssm_params: Record<string, MockedSsmParam[]> = {
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

export function ssmParamsFor(slug: string): MockedSsmParam[] {
  const hit = __ssm_params[slug];
  if (hit) return hit;
  return [
    { name: `/config/${slug}/DB_HOST`, type: 'nuevo', aws_status: 'ok', values: { qa: 'host-qa', uat: 'host-uat', stgp: 'host-stgp' } },
    { name: `/config/${slug}/API_TIMEOUT`, type: 'nuevo', aws_status: 'ok', values: { qa: '3000', uat: '3000', stgp: '3000' } },
    { name: `/common/${slug}/JWT_SECRET`, type: 'reutilizado', aws_status: 'missing', values: { qa: 'jwt-qa', uat: '', stgp: '' } },
  ];
}

// --- GET /api/ssm/environments (data/ssm-environments.js) --------------------
export const MOCK_REGIONS: string[] = BB_CONFIG.regions.slice();
export const MOCK_REGION_LABELS: Record<string, string> = { qa: 'QA', uat: 'UAT', stgp: 'STGP' };