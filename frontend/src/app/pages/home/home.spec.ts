import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import {
  provideHttpClientTesting,
  HttpTestingController,
} from '@angular/common/http/testing';
import { ActivatedRoute, Router } from '@angular/router';
import { of } from 'rxjs';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { Home } from './home';

// BBIT-56: mock del EventSource global (no existe real en el runner Vitest).
// Registra instancias abiertas y permite simular eventos SSE (open/repo/
// stats/query_diff/diff/done/error) + fallos de red vía onerror.
class MockEventSource {
  static instances: MockEventSource[] = [];

  url: string;
  readyState = 0;
  onerror: ((e: Event) => void) | null = null;
  private listeners = new Map<string, (e: MessageEvent) => void>();

  constructor(url: string) {
    this.url = url;
    MockEventSource.instances.push(this);
  }

  addEventListener(type: string, fn: (e: MessageEvent) => void): void {
    this.listeners.set(type, fn);
  }

  close(): void {
    this.readyState = 2;
  }

  emit(type: string, data?: string): void {
    const fn = this.listeners.get(type);
    if (fn) fn({ data } as MessageEvent);
  }

  networkError(): void {
    if (this.onerror) this.onerror(new Event('error'));
  }

  static last(): MockEventSource {
    const es = MockEventSource.instances[MockEventSource.instances.length - 1];
    if (!es) throw new Error('Ningún EventSource fue abierto');
    return es;
  }

  static reset(): void {
    MockEventSource.instances = [];
  }
}

function repoItem(slug: string, overrides: Record<string, unknown> = {}): string {
  return JSON.stringify({
    slug,
    name: slug.toUpperCase(),
    workspace: 'ws',
    branch_url: `https://bitbucket.org/ws/${slug}`,
    commit: 'a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2',
    tags: [],
    pr: { exists: false },
    deploys: {},
    match_tag: {},
    visible: true,
    ...overrides,
  });
}

function deploy(env: string, slug: string) {
  return {
    deploys: {
      [env]: {
        workflow: `wf-${slug}`,
        status: 'success',
        created_at: '2026-09-01T12:00:00Z',
        url: `https://circleci.com/job/${slug}`,
      },
    },
    match_tag: { [env]: `${env}-1` },
  };
}

async function mountHome() {
  TestBed.configureTestingModule({
    imports: [Home],
    providers: [
      provideHttpClient(),
      provideHttpClientTesting(),
      {
        provide: ActivatedRoute,
        useValue: {
          snapshot: { params: {}, data: {} },
          params: of({}),
          queryParams: of({}),
        },
      },
      { provide: Router, useValue: { navigate: vi.fn() } },
    ],
  });
  await TestBed.compileComponents();

  const fixture = TestBed.createComponent(Home);
  const component = fixture.componentInstance;
  const httpMock = TestBed.inject(HttpTestingController);

  // HTTP del constructor (health + session inactiva → sin cadena de /scan).
  httpMock.expectOne('/api/health').flush({ status: 'ok', version: '0.70.0', connected: false, workspace: null, identity: null, repo_count: 0 });
  httpMock.expectOne('/api/session').flush({ active: false, stored: false });

  return { fixture, component, httpMock };
}

// BBIT-66: arranque con credenciales guardadas (stored) — a diferencia de
// mountHome (sesión inactiva), dispara el primer load vía /api/repositories
// SIN pasar por /api/session/reuse.
async function mountHomeStored() {
  TestBed.configureTestingModule({
    imports: [Home],
    providers: [
      provideHttpClient(),
      provideHttpClientTesting(),
      {
        provide: ActivatedRoute,
        useValue: {
          snapshot: { params: {}, data: {} },
          params: of({}),
          queryParams: of({}),
        },
      },
      { provide: Router, useValue: { navigate: vi.fn() } },
    ],
  });
  await TestBed.compileComponents();

  const fixture = TestBed.createComponent(Home);
  const component = fixture.componentInstance;
  const httpMock = TestBed.inject(HttpTestingController);

  httpMock.expectOne('/api/health').flush({ status: 'ok', version: '0.70.0', connected: false, workspace: null, identity: null, repo_count: 0 });
  httpMock.expectOne('/api/session').flush({ active: false, stored: true });

  return { fixture, component, httpMock };
}

// BBIT-56: simula una "carga completa" exitosa (estrategia full) con N repos.
// Primea el reposCache local para que loadRepos no pegue a /repos-quick y
// aterrice directo en el SSE, luego emite repo/stats/done.
function primeAndScan(component: Home, slugs: string[], prefixes = ['uat']) {
  component.origin = 'release/x';
  component.destination = 'master';
  component.prefixes.set(prefixes);
  component.reposCache.set(
    slugs.map((slug) => ({ slug, name: slug.toUpperCase(), workspace: 'ws', default_branch: 'master' })),
  );
  component.loadRepos();
  const es = MockEventSource.last();
  for (const slug of slugs) {
    // El backend emite deploys + match_tag dentro del evento repo.
    const deployEnv: Record<string, unknown> = {};
    const matchTag: Record<string, unknown> = {};
    for (const p of prefixes) {
      deployEnv[p] = { workflow: `${p}-${slug}`, status: 'success', created_at: '2026-09-01T12:00:00Z', url: `https://circleci.com/job/${slug}` };
      matchTag[p] = `${p}-1`;
    }
    es.emit('repo', repoItem(slug, { deploys: deployEnv, match_tag: matchTag }));
  }
  es.emit('stats', JSON.stringify({ repos: slugs.length, with_pr: 0, prod: 0 }));
  es.emit('done', JSON.stringify({}));
  return es;
}

describe('Home (BBIT-56: columnas de ambiente bajo demanda)', () => {
  beforeEach(() => {
    MockEventSource.reset();
    (globalThis as any).EventSource = MockEventSource;
    if (typeof crypto !== 'undefined' && !crypto.randomUUID) {
      (crypto as any).randomUUID = () => '00000000-0000-4000-8000-000000000000';
    }
  });

  it('addPrefix marca el prefijo pendiente: la columna NO se pinta ni hay red', async () => {
    const { component } = await mountHome();
    primeAndScan(component, ['r1'], ['uat']);
    expect(component.loadedPrefixes()).toEqual(['uat']);

    const opened = MockEventSource.instances.length;
    component.prefixInput = 'stgp';
    component.addPrefix();

    // Prefijo agregado a la consulta, pero aún NO cargado (columna oculta).
    expect(component.prefixes()).toEqual(['uat', 'stgp']);
    expect(component.loadedPrefixes()).toEqual(['uat']);
    // Cero trabajo de red por agregar el prefijo solo.
    expect(MockEventSource.instances.length).toBe(opened);
  });

  it('"obtener repositorios" tras agregar un ambiente hace recarga selectiva SOLO de la columna nueva', async () => {
    const { component } = await mountHome();
    primeAndScan(component, ['r1'], ['uat']);
    expect(component.loadedPrefixes()).toEqual(['uat']);

    component.prefixInput = 'stgp';
    component.addPrefix();
    component.loadRepos();

    // Estrategia selective → SSE con prefixes=stgp (sin uat) y sin diff.
    const es = MockEventSource.last();
    expect(es.url).toContain('prefixes=stgp');
    expect(es.url).not.toContain('uat');
    expect(es.url).toContain('with_diff=0');

    // El item de la recarga selectiva trae SOLO deploys de la columna nueva.
    es.emit('repo', repoItem('r1', deploy('stgp', 'r1')));
    es.emit('stats', JSON.stringify({ repos: 1, with_pr: 0, prod: 0 }));
    es.emit('done', JSON.stringify({}));

    // Ahora sí se pinta la columna: loadedPrefixes incluye el prefijo nuevo.
    expect(component.loadedPrefixes()).toEqual(['uat', 'stgp']);
  });

  it('recarga selectiva mergea por columna sin pisar los deploys existentes', async () => {
    const { component } = await mountHome();
    primeAndScan(component, ['r1'], ['uat']);

    expect(component.repos()[0].deploys).toMatchObject({ uat: { status: 'success' } });
    expect(component.repos()[0].match_tag).toMatchObject({ uat: 'uat-1' });

    component.prefixInput = 'stgp';
    component.addPrefix();
    component.loadRepos();
    const es = MockEventSource.last();
    es.emit('repo', repoItem('r1', deploy('stgp', 'r1')));
    es.emit('stats', JSON.stringify({ repos: 1, with_pr: 0, prod: 0 }));
    es.emit('done', JSON.stringify({}));

    // Columnas existentes (uat) preservadas + columna nueva (stgp) mergeada.
    expect(component.repos()[0].deploys).toMatchObject({
      uat: { status: 'success' },
      stgp: { status: 'success' },
    });
    expect(component.repos()[0].match_tag).toMatchObject({ uat: 'uat-1', stgp: 'stgp-1' });
  });

  it('blacklist cambia filtros cliente-side: la tabla se refiltra local sin abrir SSE', async () => {
    const { component } = await mountHome();
    primeAndScan(component, ['r1', 'r2']);
    expect(component.repos().map((r) => r.slug)).toEqual(['r1', 'r2']);

    const opened = MockEventSource.instances.length;
    component.blacklistInput = 'r2';
    component.addBlacklist();
    component.loadRepos();

    // Estrategia removal → applyRemovals filtra en el cliente, cero red.
    expect(MockEventSource.instances.length).toBe(opened);
    expect(component.repos().map((r) => r.slug)).toEqual(['r1']);
  });

  it('consulta idéntica a la última exitosa hace cero trabajo de red', async () => {
    const { component } = await mountHome();
    primeAndScan(component, ['r1'], ['uat']);
    expect(component.loadedPrefixes()).toEqual(['uat']);

    const opened = MockEventSource.instances.length;
    component.loadRepos();

    // Estrategia identical → ni SSE ni /repos-quick; solo refresh de keys.
    expect(MockEventSource.instances.length).toBe(opened);
    expect(component.loadedPrefixes()).toEqual(['uat']);
  });

  it('el backend puede anticipar la respuesta con query_diff y queda en lastQueryDiff', async () => {
    const { component } = await mountHome();
    component.origin = 'release/x';
    component.destination = 'master';
    component.prefixes.set(['uat']);
    component.reposCache.set([{ slug: 'r1', name: 'R1', workspace: 'ws', default_branch: 'master' }]);

    component.loadRepos();
    const es = MockEventSource.last();

    const diff = { identical: true, added: {}, removed: {}, changed: {} };
    es.emit('query_diff', JSON.stringify(diff));

    expect(component.lastQueryDiff()).toEqual(diff);

    es.emit('repo', repoItem('r1', deploy('uat', 'r1')));
    es.emit('stats', JSON.stringify({ repos: 1, with_pr: 0, prod: 0 }));
    es.emit('done', JSON.stringify({}));
    expect(component.repos().map((r) => r.slug)).toEqual(['r1']);
  });

  it('repos-quick consultado con origin+destination: la tabla solo pinta repos with branch_state found', async () => {
    const { component, httpMock } = await mountHome();
    component.origin = 'release/x';
    component.reposCache.set([]);
    component.loadRepos();

    // El request de repos-quick lleva origin y destination.
    const req = httpMock.expectOne((r) => r.url === '/api/repos-quick');
    expect(req.request.params.get('origin')).toBe('release/x');
    expect(req.request.params.get('destination')).toBe('master');

    req.flush({
      repos: [
        { slug: 'r1', name: 'R1', workspace: 'ws', default_branch: 'master', branch_state: 'found', resolved_branch: 'release/x' },
        { slug: 'r2', name: 'R2', workspace: 'ws', default_branch: 'master', branch_state: 'not_found', resolved_branch: '' },
      ],
      count: 2,
    });

    // displayFilteredRepos excluye repos sin la rama antes de pintar.
    expect(component.repos().map((r) => r.slug)).toEqual(['r1']);

    // La fase pesada sigue con SSE solo para los repos de la tabla.
    const es = MockEventSource.last();
    expect(es.url).toContain('prefixes=uat');
    es.emit('repo', repoItem('r1', deploy('uat', 'r1')));
    es.emit('stats', JSON.stringify({ repos: 1, with_pr: 0, prod: 0 }));
    es.emit('done', JSON.stringify({}));
    expect(component.repos().map((r) => r.slug)).toEqual(['r1']);
  });

  it('reposCache sin branch_state (retrocompat): todos los repos se pintan como placeholders', async () => {
    const { component } = await mountHome();
    component.origin = 'release/x';
    component.reposCache.set([
      { slug: 'r1', name: 'R1', workspace: 'ws', default_branch: 'master' },
      { slug: 'r2', name: 'R2', workspace: 'ws', default_branch: 'master' },
    ]);
    component.loadRepos();

    // Sin branch_state el filtro BBIT-58 no aplica (lo decide el SSE).
    expect(component.repos().map((r) => r.slug)).toEqual(['r1', 'r2']);

    const es = MockEventSource.last();
    // r2 sin la rama llega del SSE como visible:false y se saca de la tabla.
    es.emit('repo', JSON.stringify({ slug: 'r2', visible: false, reason: 'branch_not_found', branch_state: 'not_found' }));
    es.emit('repo', repoItem('r1', deploy('uat', 'r1')));
    es.emit('stats', JSON.stringify({ repos: 1, with_pr: 0, prod: 0 }));
    es.emit('done', JSON.stringify({}));
    expect(component.repos().map((r) => r.slug)).toEqual(['r1']);
  });

  it('primer load: /api/repositories decide el scan y mapea sources a la tabla actual', async () => {
    const { component, httpMock } = await mountHome();

    component.loadInitialRepositories();

    const req = httpMock.expectOne('/api/repositories');
    req.flush({
      status: { code: 'BBIT-000', message: 'OK' },
      body: {
        repositories: [
          {
            slug: 'orders-api',
            tags: ['batch', 'fargate'],
            sources: [
              {
                branch: 'release/x',
                url: 'https://bitbucket.org/ws/orders-api/branch/release/x',
                head_commit: 'abcdef1234567890abcdef1234567890abcdef12',
                tags: [{ name: 'uat-1201', url: 'https://app.circleci.com/pipelines/1' }],
                ssm: ['/common/db_url'],
                targets: [
                  {
                    branch: 'master',
                    head_commit: 'master000',
                    pr: { status: 'open', title: 'Release: release/x → master', url: 'https://bitbucket.org/ws/orders-api/pull-requests/12' },
                    ssm: [],
                  },
                ],
              },
            ],
          },
          { slug: 'legacy', tags: [], sources: [] },
        ],
      },
    });

    // Primer source → rama principal de la fila (branch_url/commit/PR).
    const [r, legacy] = component.repos();
    expect(r.slug).toBe('orders-api');
    expect(r.name).toBe('orders-api');
    expect(r.branch_url).toBe('https://bitbucket.org/ws/orders-api/branch/release/x');
    expect(r.commit).toBe('abcdef1234567890abcdef1234567890abcdef12');
    expect(r.resolved_branch).toBe('release/x');
    expect(r.branch_state).toBe('found');
    expect(r.pr).toMatchObject({
      exists: true,
      url: 'https://bitbucket.org/ws/orders-api/pull-requests/12',
      title: 'Release: release/x → master',
      state: 'open',
    });
    expect(r.tags[0]).toMatchObject({ name: 'uat-1201' });
    expect(r.tags[0].deploy?.url).toBe('https://app.circleci.com/pipelines/1');

    // Flow tags del repo van a reposCache → la tabla renderiza igual que con flow.
    expect(component.reposCache()[0].tags).toEqual(['batch', 'fargate']);
    expect(component.reposCache()[1].branch_state).toBeUndefined();
    expect(component.projects().map((p) => p.slug)).toEqual(['orders-api', 'legacy']);
  });

  it('primer load: error de /api/repositories se muestra en lugar de la tabla', async () => {
    const { component, httpMock } = await mountHome();

    component.loadInitialRepositories();
    const req = httpMock.expectOne('/api/repositories');
    req.error(new ProgressEvent('network-error'));

    expect(component.error()).toContain('No se pudieron cargar los repositorios.');
  });

  it('arranque con credenciales guardadas NO reusa sesión: pega directo al orquestador', async () => {
    const { fixture, httpMock } = await mountHomeStored();

    // El orquestador decide: no hay POST /api/session/reuse.
    httpMock.expectNone('/api/session/reuse');
    const req = httpMock.expectOne('/api/repositories');
    req.flush({
      status: { code: 'BBIT-000', description: 'ok' },
      body: {
        repositories: [
          {
            slug: 'orders-api',
            tags: ['fargate'],
            sources: [{ branch: 'release/x', url: 'https://x/b', head_commit: 'abc', tags: [], targets: [] }],
          },
        ],
      },
      session: { active: true, identity: 'J', workspace: 'ws', repo_count: 1 },
    });

    const component = fixture.componentInstance;
    expect(component.connected()).toBe(true);
    expect(component.identity()).toBe('J');
    expect(component.repoCount()).toBe(1);
    expect(component.repos().map((r) => r.slug)).toEqual(['orders-api']);
    httpMock.verify();
  });
});