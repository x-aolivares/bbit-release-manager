import { Router } from '@angular/router';
import { Component, inject, signal, computed, OnInit } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { IonContent } from '@ionic/angular/ion-content';
import { IonCard } from '@ionic/angular/ion-card';
import { IonCardContent } from '@ionic/angular/ion-card-content';
import { SessionHistoryService, SessionConfig } from '../../services/session-history.service';
import { SessionSidebarComponent } from '../../components/session-sidebar/session-sidebar';
import { TopbarComponent } from '../../components/topbar/topbar';
import { AuthPanelComponent } from '../../components/auth-panel/auth-panel';
import { ConfigModalComponent } from '../../components/config-modal/config-modal';
import { ConfirmModalComponent } from '../../components/confirm-modal/confirm-modal';
import { LoadingModalComponent } from '../../components/loading-modal/loading-modal';
import { BuscadorComponent } from '../../components/buscador/buscador';
import { ReposBuscadosComponent, type ReposBuscarRow, type RepoBranchState, type ReposBuscarSelection } from '../../components/repos-buscados/repos-buscados';
import { AccionesMasivasComponent, type FlowTagsRequest, type DeployTagsRequest } from '../../components/acciones-masivas/acciones-masivas';
import { CrearPrComponent } from '../../components/crear-pr/crear-pr';
import { RepoDetalleComponent, type DetalleRepo, type DetalleParam } from '../../components/repo-detalle/repo-detalle';
import { buildFlowUrl, decideLoadStrategy, processSseEvent, sortRepos } from './flow-utils';
import type { RepoSortKey, RepoSortDir } from './flow-utils';

interface Health {
  status: string;
  version: string;
  connected: boolean;
  workspace: string | null;
  identity: string | null;
  repo_count: number;
}

interface DeployInfo {
  workflow: string;
  status: string;
  created_at: string;
  url: string;
  job?: string;
  approval?: string;
}

interface TagRow {
  name: string;
  deploy: DeployInfo | null;
}

interface PrInfo {
  exists: boolean;
  url?: string;
  title?: string;
  state?: string;
}

interface ScanRepo {
  slug: string;
  name: string;
  workspace: string;
  default_branch?: string;
  branch_url: string;
  commit: string;
  error?: string | null;
  visible?: boolean;
  reason?: string;
  branch_state?: 'found' | 'not_found';
  resolved_branch?: string;
  tags: TagRow[];
  pr: PrInfo;
  deploys: Record<string, DeployInfo | null>;
  match_tag: Record<string, string | null>;
  ci_project?: string | null;
  ci_vcs?: string | null;
}

interface ScanProject {
  slug: string;
  name: string;
  workspace: string;
  default_branch: string;
}

interface ScanStats {
  repos: number;
  with_pr: number;
  prod: number;
}

interface SsmParam {
  param: string;
  arn: string;
  tipo: 'nuevo' | 'reutilizado' | string;
  qa_value: string | null;
  aws_status: 'ok' | 'missing' | 'skipped';
  repos: string[];
  type?: string;
  env_values?: Record<string, string>;
}

interface RemovedParam {
  param: string;
  repos: string[];
}

interface DiffResponse {
  origin: string;
  destination: string;
  prefixes: string[];
  mode: string;
  params: SsmParam[];
  removed: RemovedParam[];
}

interface QuickRepo {
  slug: string;
  name: string;
  workspace: string;
  default_branch: string;
  resolved_branch?: string;
  branch_state?: string;
  tags?: string[];
}

@Component({
  selector: 'app-home',
  templateUrl: './home.html',
  styleUrl: './home.scss',
  standalone: true,
  imports: [
    IonContent,
    IonCard, IonCardContent,
    SessionSidebarComponent,
    TopbarComponent,
    AuthPanelComponent,
    ConfigModalComponent,
    ConfirmModalComponent,
    LoadingModalComponent,
    BuscadorComponent,
    ReposBuscadosComponent,
    AccionesMasivasComponent,
    CrearPrComponent,
    RepoDetalleComponent,
  ],
})
export class Home implements OnInit {
  private http = inject(HttpClient);
  private sessionHistory = inject(SessionHistoryService);
  private router = inject(Router);

  health = signal<Health | null>(null);
  connected = signal(false);
  identity = signal('');
  repoCount = signal(0);
  loading = signal(false);
  error = signal<string | null>(null);

  private readonly busyMinMs = 2000;

  private releaseBusy(startedAt: number, done: () => void): void {
    const remaining = this.busyMinMs - (Date.now() - startedAt);
    if (remaining <= 0) {
      done();
      return;
    }
    window.setTimeout(done, remaining);
  }

  origin = '';
  destination = 'master';
  prefixInput = '';
  prefixes = signal<string[]>(['uat', 'stgp', 'prod']);
  loadedPrefixes = signal<string[]>([]);
  lastQueryKey: string | null = null;
  lastQueryBaseKey: string | null = null;
  lastBackendKey: string | null = null;

  private queryKey(): string {
    return JSON.stringify({
      origin: this.origin,
      destination: this.projectsDest(),
      prefixes: [...this.prefixes()].sort(),
      projectPrefixes: [...this.projectPrefixes()].sort(),
      exclude: [...this.blacklisted()].sort(),
      mode: this.scanMode(),
    });
  }

  private queryBaseKey(): string {
    return JSON.stringify({
      origin: this.origin,
      destination: this.projectsDest(),
      projectPrefixes: [...this.projectPrefixes()].sort(),
      exclude: [...this.blacklisted()].sort(),
      mode: this.scanMode(),
    });
  }

  private queryBackendKey(): string {
    return JSON.stringify({
      origin: this.origin,
      destination: this.projectsDest(),
      prefixes: [...this.prefixes()].sort(),
      mode: this.scanMode(),
    });
  }

  projectPrefixInput = '';
  projectPrefixes = signal<string[]>([]);
  addingProjectPrefix = signal(false);

  blacklistInput = '';
  blacklisted = signal<string[]>([]);
  addingBlacklist = signal(false);
  forceCache = signal(false);

  lastQueryDiff = signal<{ identical: boolean; added: Record<string, unknown>; removed: Record<string, unknown>; changed: Record<string, unknown> } | null>(null);

  repos = signal<ScanRepo[]>([]);
  projects = signal<ScanProject[]>([]);
  reposCache = signal<QuickRepo[]>([]);
  params = signal<SsmParam[]>([]);
  removed = signal<RemovedParam[]>([]);
  scanMode = signal<'diff' | 'all'>('diff');
  stats = signal<ScanStats | null>(null);
  addingPrefix = signal(false);
  ciConfigured = signal(true);
  ciError = signal<string | null>(null);
  storedCreds = signal(false);
  askDisconnect = signal(false);
  syncingPrs = signal(false);

  configOpen = signal(false);
  clientAlias = signal('local');
  gitClonesDir = signal('');
  gitClonesEnabled = signal(false);

  reposLoading = signal(false);
  paramsLoading = signal(false);
  tableLoaded = signal(false);
  paramsLoaded = signal(false);

  spinnerVisible = signal(false);
  private spinnerTimer: ReturnType<typeof setTimeout> | undefined;

  finalizing = signal(false);

  repoSortKey = signal<RepoSortKey>('name');
  repoSortEnv = signal<string | null>(null);
  repoSortDir = signal<RepoSortDir>('asc');

  readonly sortedRepos = computed(() =>
    sortRepos(this.repos(), this.repoSortKey(), this.repoSortDir(), this.repoSortEnv() ?? undefined),
  );

  toggleRepoSort(key: RepoSortKey, env?: string): void {
    if (this.repoSortKey() === key && this.repoSortEnv() === (env ?? null)) {
      this.repoSortDir.set(this.repoSortDir() === 'asc' ? 'desc' : 'asc');
    } else {
      this.repoSortKey.set(key);
      this.repoSortEnv.set(env ?? null);
      this.repoSortDir.set('asc');
    }
  }

  sortArrow(key: RepoSortKey, env?: string): string {
    if (this.repoSortKey() !== key || this.repoSortEnv() !== (env ?? null)) {
      return '';
    }
    return this.repoSortDir() === 'asc' ? '↑' : '↓';
  }

  private clearSpinner(): void {
    if (this.spinnerTimer !== undefined) {
      clearTimeout(this.spinnerTimer);
      this.spinnerTimer = undefined;
    }
    this.spinnerVisible.set(false);
  }

  private scheduleSpinnerCap(): void {
    this.spinnerVisible.set(true);
    this.spinnerTimer = setTimeout(() => this.spinnerVisible.set(false), 2000);
  }

  creatingPr = signal<string | null>(null);
  tagging = signal(false);
  taggingRepo = signal<string | null>(null);

  estadoFilter = signal<string[]>([]);
  readonly estadoOptions = ['nuevo', 'reutilizado', 'solo destino'];

  toggleEstado(estado: string): void {
    const cur = this.estadoFilter();
    this.estadoFilter.set(cur.includes(estado) ? cur.filter((e) => e !== estado) : [...cur, estado]);
  }

  estadoFilterActive(estado: string): boolean {
    const f = this.estadoFilter();
    return f.length === 0 || f.includes(estado);
  }

  filteredParams(): SsmParam[] {
    return this.params().filter((p) => this.estadoFilterActive(p.tipo));
  }

  sidebarOpen = signal(false);
  currentSessionId = signal<string | null>(null);
  sessions = signal<SessionConfig[]>([]);

  prefixCols(): string {
    return this.loadedPrefixes().map(() => ' 9.5rem').join('');
  }

  private syncLoadedPrefixes(): void {
    this.loadedPrefixes.set(this.prefixes().slice());
    this.lastQueryKey = this.queryKey();
    this.lastQueryBaseKey = this.queryBaseKey();
    this.lastBackendKey = this.queryBackendKey();
  }

  addPrefix() {
    const p = this.prefixInput.trim().toLowerCase();
    if (p && !this.prefixes().includes(p)) {
      this.prefixes.update((list) => [...list, p]);
    }
    this.prefixInput = '';
  }

  removePrefix(index: number) {
    const p = this.prefixes()[index];
    this.prefixes.update((list) => list.filter((_, i) => i !== index));
    if (p) {
      this.loadedPrefixes.update((list) => list.filter((x) => x !== p));
    }
  }

  addProjectPrefix() {
    const p = this.projectPrefixInput.trim().toLowerCase();
    if (p && !this.projectPrefixes().includes(p)) {
      this.projectPrefixes.update((list) => [...list, p]);
    }
    this.projectPrefixInput = '';
  }

  removeProjectPrefix(index: number) {
    this.projectPrefixes.update((list) => list.filter((_, i) => i !== index));
  }

  addBlacklist() {
    const p = this.blacklistInput.trim().toLowerCase();
    if (p && !this.blacklisted().includes(p)) {
      this.blacklisted.update((list) => [...list, p]);
    }
    this.blacklistInput = '';
  }

  removeBlacklist(index: number) {
    this.blacklisted.update((list) => list.filter((_, i) => i !== index));
  }

  projectPrefixParam(): string {
    return this.projectPrefixes().join(',');
  }

  private repoInProjects(slugs: string[]): boolean {
    const prefs = this.projectPrefixes().map((p) => p.toLowerCase());
    if (!prefs.length) return true;
    return slugs.some((s) => prefs.some((p) => s.toLowerCase().startsWith(p)));
  }

  constructor() {
    this.http.get<Health>('/api/health').subscribe({
      next: (h) => {
        this.health.set(h);
        console.log(`[BBit] Backend version: ${h.version} | Frontend: 0.48.3`);
      },
      error: () => this.error.set('No se pudo contactar la API.'),
    });
    this.http.get<any>('/api/session').subscribe({
      next: (r) => {
        this.clientAlias.set(r.client_alias ?? 'local');
        this.gitClonesDir.set(r.git?.clones_dir ?? '');
        this.gitClonesEnabled.set(!!r.git?.enabled);
        if (r.active) {
          this.connected.set(true);
          this.identity.set(r.identity ?? '');
          this.repoCount.set(r.repo_count ?? 0);
          this.loadLatestSession();
        } else if (r.stored) {
          this.reuseSession(() => this.loadLatestSession());
        } else if (r.needs_tokens) {
          this.configOpen.set(true);
          this.storedCreds.set(false);
        } else {
          this.storedCreds.set(false);
        }
      },
    });

    this.refreshSessions();
  }

  private loadLatestSession(): void {
    const latest = this.sessionHistory.getLatest();
    if (latest) {
      this.loadSession(latest);
    }
  }

  ngOnInit(): void {
  }

  private refreshSessions(): void {
    this.sessions.set(this.sessionHistory.getAll());
  }

  private saveCurrentSession(): void {
    if (!this.origin) return;
    const session = this.sessionHistory.save({
      origin: this.origin,
      destination: this.destination || 'master',
      prefixes: this.prefixes(),
      projectPrefixes: this.projectPrefixes(),
      blacklisted: this.blacklisted(),
    });
    this.currentSessionId.set(session.id);
    this.refreshSessions();
  }

  protected onSessionSelected(session: SessionConfig): void {
    this.loadSession(session);
    this.sidebarOpen.set(false);
  }

  protected onSessionDeleted(id: string): void {
    const session = this.sessionHistory.load(id);
    this.sessionHistory.delete(id);
    if (this.currentSessionId() === id) {
      this.currentSessionId.set(null);
    }
    this.refreshSessions();
    if (session) {
      this.http.delete<any>('/api/cache', { body: { sessions: [{
        origin: session.origin,
        destination: session.destination,
        project_prefixes: session.projectPrefixes,
        exclude: session.blacklisted,
      }] } }).subscribe({
        error: () => this.error.set('No se pudo limpiar la sesión del cache del backend.'),
      });
    }
  }

  protected onHistoryCleared(): void {
    this.sessionHistory.clear();
    this.currentSessionId.set(null);
    this.refreshSessions();
    this.http.delete<any>('/api/cache').subscribe({
      next: () => this.repos.set([]),
      error: () => this.error.set('No se pudo limpiar el cache del backend.'),
    });
  }

  private loadSession(session: SessionConfig): void {
    this.origin = session.origin;
    this.destination = session.destination;
    this.prefixes.set(session.prefixes);
    this.projectPrefixes.set(session.projectPrefixes);
    this.blacklisted.set(session.blacklisted);
    this.forceCache.set(false);
    this.currentSessionId.set(session.id);
    this.params.set([]);
    this.removed.set([]);
    this.paramsLoaded.set(false);
    this.loadRepos();
  }

  reuseSession(onSuccess?: () => void) {
    this.loading.set(true);
    this.error.set(null);
    const body: Record<string, string> = {};
    const startTime = Date.now();
    const timeoutId = window.setTimeout(() => {
      this.loading.set(false);
      this.error.set('Timeout: no se pudo reusar la sesión. Revisá tus credenciales.');
    }, 15000);

    this.http.post<any>('/api/session/reuse', body).subscribe({
      next: (r) => {
        window.clearTimeout(timeoutId);
        if (r.ok) {
          this.connected.set(true);
          this.identity.set(r.identity ?? '');
          this.repoCount.set(r.repo_count ?? 0);
          this.clientAlias.set(r.client_alias ?? 'local');
          this.storedCreds.set(false);
          if (onSuccess) {
            onSuccess();
          }
          this.releaseBusy(startTime, () => this.loading.set(false));
        } else {
          this.error.set(r.error ?? 'Error al reutilizar la sesión.');
          this.storedCreds.set(false);
          this.releaseBusy(startTime, () => this.loading.set(false));
        }
      },
      error: (e) => {
        window.clearTimeout(timeoutId);
        this.error.set(e.error?.error ?? 'Las credenciales guardadas dejaron de funcionar. Generá de nuevo.');
        this.storedCreds.set(false);
        this.releaseBusy(startTime, () => this.loading.set(false));
      },
      complete: () => {
        window.clearTimeout(timeoutId);
      }
    });
  }

  onConnectRequested(r: { alias: string; workspace: string; token: string; circleciToken: string }) {
    this.loading.set(true);
    this.error.set(null);
    const body: Record<string, string> = {
      workspace: r.workspace,
      token: r.token,
      project_prefixes: this.projectPrefixParam(),
      exclude_repos: this.blacklisted().join(','),
    };
    if (r.alias.trim()) {
      body['alias'] = r.alias.trim();
    }
    if (r.circleciToken) {
      body['circleci_token'] = r.circleciToken;
    }
    if (this.gitClonesDir().trim()) {
      body['git_clones_dir'] = this.gitClonesDir().trim();
    }

    const startTime = Date.now();
    const timeoutId = window.setTimeout(() => {
      this.loading.set(false);
      this.error.set('Timeout: la conexión tardó demasiado. Revisá tus credenciales o la red.');
    }, 15000);

    this.http.post<any>('/api/session', body).subscribe({
        next: (r) => {
          window.clearTimeout(timeoutId);
          if (r.ok) {
            this.connected.set(true);
            this.identity.set(r.identity);
            this.repoCount.set(r.repo_count);
            this.clientAlias.set(r.client_alias ?? 'local');
            this.storedCreds.set(false);
            this.releaseBusy(startTime, () => this.loading.set(false));
          } else {
            this.error.set(r.error ?? 'Error de conexión');
            this.releaseBusy(startTime, () => this.loading.set(false));
          }
        },
        error: (e) => {
          window.clearTimeout(timeoutId);
          this.error.set(e.error?.error ?? 'Error de red al conectar.');
          this.releaseBusy(startTime, () => this.loading.set(false));
        },
        complete: () => {
          window.clearTimeout(timeoutId);
        }
      });
  }

  disconnect(deleteCredentials: boolean) {
    this.askDisconnect.set(false);
    this.http.delete<any>(`/api/session${deleteCredentials ? '?delete_credentials=1' : ''}`).subscribe({
      next: () => {
        this.connected.set(false);
        this.identity.set('');
        this.repoCount.set(0);
        this.repos.set([]);
        this.reposCache.set([]);
        this.projects.set([]);
        this.params.set([]);
        this.removed.set([]);
        this.stats.set(null);
        this.clientAlias.set('local');
        this.configOpen.set(false);
        this.storedCreds.set(!deleteCredentials);
        this.detalleOpen.set(false);
        this.detalleSlug.set(null);
      },
    });
  }

  projectsDest(): string {
    return this.destination || 'master';
  }

  private scanSse(
    withDiff: boolean,
    onRepo: (item: ScanRepo) => void,
    onStats: (stats: ScanStats) => void,
    onDiff: (diff: DiffResponse) => void,
    onDone: () => void,
    onError: (msg: string) => void,
    onTimeout: () => void,
    onRepoHidden: (slug: string) => void,
    onField: (slug: string, field: string, value: unknown) => void,
    prefixesOverride: string[] | null = null,
  ): boolean {
    const dest = this.projectsDest();
    const scanPrefixes = prefixesOverride ?? this.prefixes();
    const prefixes = scanPrefixes.join(',');
    const exclude = this.blacklisted().join(',');
    const force = this.forceCache() ? 1 : 0;
    const url = `/api/flow/stream?origin=${encodeURIComponent(this.origin)}&destination=${encodeURIComponent(dest)}&prefixes=${encodeURIComponent(prefixes)}&project_prefixes=${encodeURIComponent(this.projectPrefixParam())}&exclude=${encodeURIComponent(exclude)}&mode=${this.scanMode()}&force=${force}&with_tags=${scanPrefixes.length > 0 ? 1 : 0}&with_diff=${withDiff ? 1 : 0}`;

    console.log(`[SSE] Opening connection to: ${url}`);
    let firstEventReceived = false;
    const timeout = setTimeout(() => {
      if (!firstEventReceived) {
        console.warn(`[SSE] Timeout after 45s, no events received. Closing connection.`);
        es.close();
        onTimeout();
      }
    }, 45000);

    let repos: ScanRepo[] = [];
    const es = new EventSource(url);

    es.addEventListener('open', () => {
      console.log(`[SSE] Connection opened successfully`);
    });

    es.addEventListener('query_diff', (e: MessageEvent) => {
      console.log(`[SSE] Received query_diff event:`, e.data.substring(0, 100));
      firstEventReceived = true;
      const parsed = processSseEvent('query_diff', e.data, repos);
      if (parsed?.type === 'query_diff') {
        this.lastQueryDiff.set(parsed.query_diff);
      }
    });

    es.addEventListener('repo', (e: MessageEvent) => {
      console.log(`[SSE] Received repo event:`, e.data.substring(0, 100));
      firstEventReceived = true;
      const parsed = processSseEvent('repo', e.data, repos);
      if (parsed?.type === 'repo') {
        repos = parsed.repos;
        const repo = parsed.repos[parsed.repos.length - 1];
        if (repo.visible !== false) {
          onRepo(repo);
        } else {
          console.log(`[SSE] Repo ${repo.slug} sin rama (visible=false), removiendo de tabla`);
          onRepoHidden(repo.slug);
        }
      }
    });

    es.addEventListener('field', (e: MessageEvent) => {
      console.log(`[SSE] Received field event:`, e.data.substring(0, 100));
      firstEventReceived = true;
      const parsed = processSseEvent('field', e.data, repos);
      if (parsed?.type === 'field') {
        onField(parsed.field.slug, parsed.field.field, parsed.field.value);
      }
    });

    es.addEventListener('stats', (e: MessageEvent) => {
      console.log(`[SSE] Received stats event`);
      const parsed = processSseEvent('stats', e.data, repos);
      if (parsed?.type === 'stats') {
        this.finalizing.set(true);
        this.spinnerVisible.set(true);
        onStats(parsed.stats as unknown as ScanStats);
      }
    });

    es.addEventListener('diff', (e: MessageEvent) => {
      console.log(`[SSE] Received diff event`);
      const parsed = processSseEvent('diff', e.data, repos);
      if (parsed?.type === 'diff') {
        onDiff(parsed.diff as unknown as DiffResponse);
      }
    });

    es.addEventListener('done', () => {
      console.log(`[SSE] Received done event`);
      clearTimeout(timeout);
      es.close();
      this.finalizing.set(false);
      this.clearSpinner();
      onDone();
    });

    es.addEventListener('error', (e: MessageEvent) => {
      if (!e.data) return;
      console.error(`[SSE] Received error event:`, e.data);
      clearTimeout(timeout);
      es.close();
      const parsed = processSseEvent('error', e.data, repos);
      if (parsed?.type === 'error') {
        this.finalizing.set(false);
        this.clearSpinner();
        onError(parsed.message);
      } else {
        onError('Stream error');
      }
    });

    es.onerror = (e) => {
      console.error(`[SSE] Connection error (readyState=${es.readyState}):`, e);
      clearTimeout(timeout);
      es.close();
      this.finalizing.set(false);
      this.clearSpinner();
      onTimeout();
    };

    return true;
  }

  loadRepos(retryOnly = false, withDiff = false) {
    if (!this.origin) return;
    this.reposLoading.set(true);
    this.error.set(null);
    this.creatingPr.set(null);
    this.finalizing.set(false);
    this.scheduleSpinnerCap();
    if (!retryOnly) {
      this.tableLoaded.set(true);
      if (withDiff) {
        this.paramsLoading.set(true);
        this.params.set([]);
        this.removed.set([]);
        this.paramsLoaded.set(false);
      }
    }
    const startedAt = Date.now();
    const done = () => this.releaseBusy(startedAt, () => {
      this.clearSpinner();
      this.reposLoading.set(false);
      if (!retryOnly && withDiff) {
        this.paramsLoading.set(false);
      }
    });

    if (retryOnly) {
      this.loadReposBatch(retryOnly, done, withDiff);
      return;
    }

    const strategy = decideLoadStrategy({
      withDiff,
      forceCache: this.forceCache(),
      hasRepos: this.repos().length > 0,
      lastQueryKey: this.lastQueryKey,
      lastBaseKey: this.lastQueryBaseKey,
      lastBackendKey: this.lastBackendKey,
      queryKey: this.queryKey(),
      baseKey: this.queryBaseKey(),
      backendKey: this.queryBackendKey(),
      prefixes: this.prefixes(),
      loadedPrefixes: this.loadedPrefixes(),
    });

    if (strategy.kind === 'identical') {
      this.syncLoadedPrefixes();
      this.finalizing.set(false);
      done();
      return;
    }

    if (strategy.kind === 'removal') {
      this.applyRemovals();
      if (strategy.added.length > 0) {
        this.loadReposSelective(strategy.added, done, withDiff);
      } else {
        this.finalizing.set(false);
        this.syncLoadedPrefixes();
        done();
      }
      return;
    }

    if (strategy.kind === 'selective') {
      this.loadReposSelective(strategy.added, done, withDiff);
      return;
    }

    if (this.reposCache().length > 0) {
      this.displayFilteredRepos();
      this.loadReposHeavy(done, withDiff);
      return;
    }

    const projectPrefixesParam = this.projectPrefixParam();
    const excludeParam = this.blacklisted().join(',');

    this.http.get<any>('/api/repos-quick', {
      params: {
        origin: this.origin,
        destination: this.projectsDest(),
        project_prefixes: projectPrefixesParam,
        exclude: excludeParam,
      }
    }).subscribe({
      next: (r) => {
        if (r.repos) {
          this.reposCache.set(r.repos);
          this.displayFilteredRepos();
          this.clearSpinner();
          this.loadReposHeavy(done, withDiff);
        }
      },
      error: () => {
        this.loadReposHeavy(done, withDiff);
      }
    });
  }

  private displayFilteredRepos(): void {
    const projectPrefixesParam = this.projectPrefixParam();
    const blacklist = this.blacklisted();

    const filtered = this.reposCache().filter((r: QuickRepo) => {
      if ('branch_state' in r && r.branch_state === 'not_found') {
        return false;
      }
      if (projectPrefixesParam) {
        const prefixes = projectPrefixesParam.split(',').filter(p => p.trim());
        const slug = r.slug.toLowerCase();
        if (!prefixes.some(p => slug.startsWith(p.toLowerCase()))) {
          return false;
        }
      }
      if (blacklist.includes(r.slug.toLowerCase())) {
        return false;
      }
      return true;
    });

    const quickRepos: ScanRepo[] = filtered.map((repo: QuickRepo) => ({
      slug: repo.slug,
      name: repo.name,
      workspace: repo.workspace,
      default_branch: repo.default_branch,
      branch_url: '',
      commit: '',
      pr: { exists: false },
      tags: [],
      deploys: {},
      match_tag: {},
    }));

    this.repos.set(quickRepos);
  }

  private loadReposHeavy(done: () => void, withDiff = false): void {
    let batchFallbackScheduled = false;
    const scheduleFallback = () => {
      if (batchFallbackScheduled) return;
      batchFallbackScheduled = true;
      console.warn('SSE streaming unavailable, falling back to batch.');
      this.loadReposBatch(false, done, withDiff);
    };

    this.scanSse(
      withDiff,
      (item) => {
        this.repos.update((current) => {
          const existing = current.find((r) => r.slug === item.slug);
          if (existing) {
            return current.map((r) => (r.slug === item.slug ? item : r));
          } else {
            return [...current, item];
          }
        });
      },
      (stats) => {
        this.stats.set(stats as ScanStats);
      },
      (diff) => {
        if (!withDiff) return;
        this.params.set((diff.params ?? []) as unknown as SsmParam[]);
        this.removed.set((diff.removed ?? []) as unknown as RemovedParam[]);
        this.paramsLoaded.set(true);
      },
      () => {
        done();
        this.saveCurrentSession();
        this.syncLoadedPrefixes();
      },
      (msg) => {
        this.error.set(msg);
        done();
      },
      () => {
        scheduleFallback();
      },
      (slug) => {
        this.repos.update((current) => current.filter((r) => r.slug !== slug));
      },
      (slug, field, value) => {
        this.repos.update((current) => {
          const existing = current.find((r) => r.slug === slug);
          if (existing) {
            return current.map((r) => (r.slug === slug ? { ...r, [field]: value } : r));
          }
          return [...current, { slug, name: slug, workspace: '', branch_url: '', commit: '', tags: [], pr: { exists: false }, deploys: {}, match_tag: {}, [field]: value } as ScanRepo];
        });
      },
    );
  }

  private applyRemovals(): void {
    const projectPrefixesParam = this.projectPrefixParam();
    const prefixes = projectPrefixesParam.split(',').filter(p => p.trim());
    const blacklist = this.blacklisted();

    this.repos.update((current) => current.filter((r) => {
      if (projectPrefixesParam && prefixes.length > 0) {
        const slug = r.slug.toLowerCase();
        if (!prefixes.some(p => slug.startsWith(p.toLowerCase()))) {
          return false;
        }
      }
      if (blacklist.includes(r.slug.toLowerCase())) {
        return false;
      }
      return true;
    }));
  }

  private loadReposSelective(added: string[], done: () => void, withDiff = false): void {
    let batchFallbackScheduled = false;
    const scheduleFallback = () => {
      if (batchFallbackScheduled) return;
      batchFallbackScheduled = true;
      console.warn('SSE selective unavailable, falling back to full batch.');
      this.loadReposBatch(false, done, withDiff);
    };

    this.scanSse(
      withDiff,
      (item) => {
        this.repos.update((current) => {
          const idx = current.findIndex((r) => r.slug === item.slug);
          if (idx === -1) {
            return [...current, item];
          }
          const existing = current[idx];
          const mergedDeploys = { ...(existing.deploys ?? {}), ...(item.deploys ?? {}) };
          const mergedMatchTag = { ...(existing.match_tag ?? {}), ...(item.match_tag ?? {}) };
          return current.map((r, i) =>
            i === idx
              ? { ...r, deploys: mergedDeploys, match_tag: mergedMatchTag }
              : r,
          );
        });
      },
      (stats) => {
        this.stats.set(stats as ScanStats);
      },
      (diff) => {
        if (!withDiff) return;
        this.params.set((diff.params ?? []) as unknown as SsmParam[]);
        this.removed.set((diff.removed ?? []) as unknown as RemovedParam[]);
        this.paramsLoaded.set(true);
      },
      () => {
        done();
        this.saveCurrentSession();
        this.syncLoadedPrefixes();
      },
      (msg) => {
        this.error.set(msg);
        done();
      },
      () => {
        scheduleFallback();
      },
      (slug) => {
        this.repos.update((current) => current.filter((r) => r.slug !== slug));
      },
      (slug, field, value) => {
        this.repos.update((current) => {
          const existing = current.find((r) => r.slug === slug);
          if (existing) {
            return current.map((r) => (r.slug === slug ? { ...r, [field]: value } : r));
          }
          return [...current, { slug, name: slug, workspace: '', branch_url: '', commit: '', tags: [], pr: { exists: false }, deploys: {}, match_tag: {}, [field]: value } as ScanRepo];
        });
      },
      added,
    );
  }

  private loadReposBatch(retryOnly: boolean, done: () => void, withDiff = false): void {
    const dest = this.projectsDest();
    const prefixes = this.prefixes().join(',');
    const exclude = this.blacklisted().join(',');
    const force = retryOnly ? 0 : (this.forceCache() ? 1 : 0);
    let url = buildFlowUrl({
      origin: this.origin,
      dest,
      prefixes,
      projectPrefixes: this.projectPrefixParam(),
      exclude,
      scanMode: this.scanMode(),
      force,
      withTags: this.prefixes().length > 0,
      withDiff,
    });
    if (retryOnly) {
      const slugs = this.failedSlugs();
      if (!slugs.length) {
        done();
        return;
      }
      url += `&repos=${encodeURIComponent(slugs.join(','))}`;
    }

    this.http.get<any>(url).subscribe({
      next: (r) => {
        if (!retryOnly) {
          const projects: ScanProject[] = (r.projects ?? []).slice();
          projects.sort((a, b) => a.slug.localeCompare(b.slug));
          this.projects.set(projects);

          const scan = r.scan ?? {};
          this.ciConfigured.set(scan.ci_configured ?? true);
          this.ciError.set(scan.ci_error ?? null);
          this.stats.set(scan.stats ?? null);
          this.repos.set(scan.repos ?? []);
          if (!scan.repos?.length) {
            this.error.set(scan.error ?? `Ningún repo contiene la rama '${this.origin}'.`);
          }
        } else {
          const bySlug = new Map<string, ScanRepo>((r.scan?.repos ?? []).map((row: ScanRepo) => [row.slug, row]));
          this.repos.update((current) => current.map((repo) => bySlug.get(repo.slug) ?? repo));
        }

        const diff = r.diff ?? {};
        if (withDiff) {
          this.params.set(diff.params ?? []);
          this.removed.set(diff.removed ?? []);
          this.paramsLoaded.set(true);
        }
      },
      error: () => {
        this.error.set('Error al cargar la tabla.');
        done();
      },
      complete: () => {
        done();
        this.saveCurrentSession();
        if (!retryOnly) {
          this.syncLoadedPrefixes();
        }
      },
    });
  }

  loadParams() {
    this.loadRepos(false, true);
  }

  resolve() {
    this.loadRepos(false, this.paramsLoaded());
  }

  refreshRepo(slug: string) {
    if (!this.origin) return;
    this.reposLoading.set(true);
    const dest = this.projectsDest();
    const prefixes = this.prefixes().join(',');
    const exclude = this.blacklisted().join(',');
    const url = buildFlowUrl({
      origin: this.origin,
      dest,
      prefixes,
      projectPrefixes: this.projectPrefixParam(),
      exclude,
      scanMode: this.scanMode(),
      force: 0,
      withTags: this.prefixes().length > 0,
      withDiff: false,
      repos: [slug],
    });
    this.http.get<any>(url).subscribe({
      next: (r) => {
        const bySlug = new Map<string, ScanRepo>((r.scan?.repos ?? []).map((row: ScanRepo) => [row.slug, row]));
        this.repos.update((current) => current.map((repo) => bySlug.get(repo.slug) ?? repo));
      },
      error: () => {},
      complete: () => this.reposLoading.set(false),
    });
  }

  paramRows(): { param: string; estado: string; qaValue: string | null; awsStatus: string; type: string; envValues: Record<string, string> }[] {
    const rows: { param: string; estado: string; qaValue: string | null; awsStatus: string; type: string; envValues: Record<string, string> }[] = [];
    const seen = new Set<string>();
    for (const p of this.filteredParams()) {
      if (!this.repoInProjects(p.repos)) continue;
      seen.add(p.param);
      rows.push({
        param: p.param,
        estado: p.tipo,
        qaValue: p.qa_value ?? null,
        awsStatus: p.aws_status ?? 'skipped',
        type: p.type ?? 'ssm',
        envValues: p.env_values ?? {},
      });
    }
    for (const p of this.removed()) {
      if (!this.repoInProjects(p.repos)) continue;
      if (seen.has(p.param)) continue;
      const estado = 'solo destino';
      if (!this.estadoFilterActive(estado)) continue;
      rows.push({ param: p.param, estado, qaValue: null, awsStatus: 'skipped', type: 'ssm', envValues: {} });
    }
    return rows.sort((a, b) => a.param.localeCompare(b.param));
  }

  openSsmView(param: string): void {
    this.router.navigate(['/ssm', encodeURIComponent(param)]);
  }

  openConfig(): void {
    this.configOpen.set(true);
  }

  onBitbucketReauth(): void {
    this.http.delete<any>('/api/session').subscribe({
      complete: () => {
        this.connected.set(false);
        this.identity.set('');
        this.repoCount.set(0);
        this.repos.set([]);
        this.reposCache.set([]);
        this.projects.set([]);
        this.params.set([]);
        this.removed.set([]);
        this.stats.set(null);
        window.setTimeout(() => {
          this.configOpen.set(false);
          this.reuseSession(() => this.loadLatestSession());
        }, 500);
      },
    });
  }

  createPr(repo: ScanRepo) {
    this.creatingPr.set(repo.slug);
    const dest = this.destination || 'master';
    const title = `Release: ${this.origin} → ${dest}`;
    this.http.post<any>(`/api/pr?repo=${encodeURIComponent(repo.slug)}&origin=${encodeURIComponent(this.origin)}&destination=${encodeURIComponent(dest)}&title=${encodeURIComponent(title)}`, {})
      .subscribe({
        next: (r) => {
          if (r.ok && r.pr?.url) {
            repo.pr = { exists: true, url: r.pr.url, title: r.pr.title, state: r.pr.state };
            window.open(r.pr.url, '_blank');
          } else {
            this.error.set(r.error ?? 'No se pudo crear el PR.');
          }
        },
        error: (e) => this.error.set(e.error?.error ?? e.message ?? 'Error al crear el PR.'),
        complete: () => this.creatingPr.set(null),
      });
  }

  syncPrs(target: 'create' | 'update') {
    if (!this.origin) return;
    this.syncingPrs.set(true);
    this.error.set(null);
    const dest = this.destination || 'master';
    const title = `Release: ${this.origin} → ${dest}`;
    const action = target === 'create' ? 'create-missing' : 'update-titles';
    const filters = `&project_prefixes=${encodeURIComponent(this.projectPrefixParam())}&exclude=${encodeURIComponent(this.blacklisted().join(','))}`;
    this.http.post<any>(`/api/prs/${action}?origin=${encodeURIComponent(this.origin)}&destination=${encodeURIComponent(dest)}&title=${encodeURIComponent(title)}${filters}`, {})
      .subscribe({
        next: (r) => {
          if (r.ok) {
            const failed = r.failed?.length ? ` | fallaron: ${r.failed.map((f: any) => f.repo).join(', ')}` : '';
            const noChanges = r.no_changes?.length ? ` | sin cambios: ${r.no_changes.join(', ')}` : '';
            const msg = target === 'create'
              ? `PRs creados: ${r.created?.join(', ') || 'ninguno'}` + noChanges + failed
              : `PRs actualizados: ${r.updated?.join(', ') || 'ninguno'}` + (r.failed?.length ? failed : '');
            this.error.set(msg);
            if (target === 'update' || r.created?.length) {
              this.resolve();
            }
          } else {
            this.error.set(r.error ?? 'Error al sincronizar PRs.');
          }
        },
        error: (e) => this.error.set(e.error?.error ?? e.message ?? 'Error al sincronizar PRs.'),
        complete: () => this.syncingPrs.set(false),
      });
  }

  failedRepos(): ScanRepo[] {
    return this.repos().filter((r) => !!r.error);
  }

  failedSlugs(): string[] {
    return this.failedRepos().map((r) => r.slug);
  }

  retryFailed(): void {
    if (!this.failedSlugs().length) return;
    this.loadRepos(true);
  }

  generateTags(repo?: ScanRepo, prefix?: string) {
    if (!this.origin) return;
    const dest = this.destination || 'master';
    const prefixes = prefix ? prefix : this.prefixes().join(',');
    let url = `/api/tags?origin=${encodeURIComponent(this.origin)}&destination=${encodeURIComponent(dest)}&prefixes=${encodeURIComponent(prefixes)}&project_prefixes=${encodeURIComponent(this.projectPrefixParam())}&exclude=${encodeURIComponent(this.blacklisted().join(','))}`;
    if (repo) {
      url += `&repo=${encodeURIComponent(repo.slug)}`;
      this.taggingRepo.set(repo.slug);
    } else {
      this.tagging.set(true);
    }
    this.error.set(null);
    this.http.post<any>(url, {}).subscribe({
      next: (r) => {
        if (r.ok) {
          const created: string[] = r.items?.flatMap((i: any) => i.created) ?? [];
          const skipped: string[] = r.items?.flatMap((i: any) => i.skipped) ?? [];
          const errors: string[] = r.items?.flatMap((i: any) => i.errors) ?? [];
          const msg =
            `Tags creados: ${created.join(', ') || 'ninguno'}` +
            (skipped.length ? ` | ya existían: ${skipped.join(', ')}` : '') +
            (errors.length ? ` | errores: ${errors.join('; ')}` : '');
          this.error.set(msg);
          if (repo && created.length) {
            for (const tagName of created) {
              const env = tagName.split('-').slice(0, -1).join('-') || tagName;
              this.repos.update((list) => list.map((r) => {
                if (r.slug !== repo.slug) return r;
                const match_tag = { ...r.match_tag, [env]: tagName };
                const existingTags = r.tags.filter((t) => t.name !== tagName);
                const tags = [...existingTags, { name: tagName, deploy: null }];
                return { ...r, match_tag, tags };
              }));
            }
          }
        } else {
          this.error.set(r.error ?? 'Error al generar tags.');
        }
      },
      error: (e) => this.error.set(e.error?.error ?? e.message ?? 'Error al generar tags.'),
      complete: () => {
        this.tagging.set(false);
        this.taggingRepo.set(null);
      },
    });
  }

  // ─── NUEVOS: integración mockup ────────────────────────────────────────

  /** Filas para ReposBuscadosComponent: union repos() (ScanRepo) + reposCache (flow tags). */
  readonly reposRows = computed<ReposBuscarRow[]>(() => {
    const cache = new Map(this.reposCache().map((c) => [c.slug, c]));
    return this.repos().map((r) => {
      const c = cache.get(r.slug);
      return {
        slug: r.slug,
        name: r.name,
        workspace: r.workspace,
        default_branch: r.default_branch || c?.default_branch || '',
        tags: c?.tags ?? [],
        resolved_branch: r.resolved_branch ?? c?.resolved_branch,
        branch_state: (r.branch_state ?? c?.branch_state) as 'found' | 'not_found' | undefined,
        pr: r.pr?.exists && r.pr.url ? { url: r.pr.url, title: r.pr.title ?? '', id: undefined } : undefined,
      };
    });
  });

  /** Estados por repo para la tabla (found/not_found/pending/checking). */
  readonly reposStates = computed<Record<string, RepoBranchState>>(() => {
    const map: Record<string, RepoBranchState> = {};
    const isLoading = this.reposLoading();
    for (const r of this.reposCache()) {
      const bs = r.branch_state;
      if (bs === 'not_found') {
        map[r.slug] = 'not_found';
      } else if (bs === 'found') {
        map[r.slug] = 'found';
      } else {
        map[r.slug] = isLoading ? 'checking' : 'pending';
      }
    }
    return map;
  });

  /** Repos removidos por no tener la rama (solo la cuenta). */
  readonly reposRemovedCount = computed(() =>
    this.reposCache().filter((r) => r.branch_state === 'not_found').length,
  );

  /** Creando PR por slug (Record<slug, boolean>). */
  readonly prCreatingBySlug = computed<Record<string, boolean>>(() => {
    const slug = this.creatingPr();
    if (!slug) return {};
    return { [slug]: true };
  });

  // ─── Seleccion y acciones ───────────────────────────────────────────────

  private selSlugs = signal<string[]>([]);

  onToggleBranch(on: boolean): void {
    if (on && this.origin) {
      this.loadRepos();
    }
  }

  onOriginChange(value: string): void {
    this.origin = value;
  }

  onDestinationChange(value: string): void {
    this.destination = value;
  }

  onVerRama(): void {
    this.loadRepos();
  }

  onSeleccion(sel: ReposBuscarSelection): void {
    this.selSlugs.set(sel.slugs);
  }

  accionesOpen = signal(false);

  /** Repos seleccionados (ReposBuscarRow[]) para el modal acciones-masivas. */
  readonly accionesSelected = computed<ReposBuscarRow[]>(() => {
    const slugs = new Set(this.selSlugs());
    return this.reposRows().filter((r) => slugs.has(r.slug));
  });

  /** Repos seleccionados con rama found (aptos para crear PR). */
  readonly accionesReadyPr = computed(() =>
    this.accionesSelected().filter((r) => this.reposStates()[r.slug] === 'found').length,
  );

  /** Indicador de trabajo masivo (PRs/tags/flow). */
  massWorking = signal(false);

  onAcciones(slugs: string[]): void {
    this.selSlugs.set(slugs);
    this.accionesOpen.set(true);
  }

  // ─── Crear PR (modal) ──────────────────────────────────────────────────

  crearPrOpen = signal(false);
  crearPrSlug = signal('');

  onCrearPr(slug: string): void {
    this.crearPrSlug.set(slug);
    this.crearPrOpen.set(true);
  }

  onCrearPrConfirm(e: { slug: string; title: string }): void {
    this.crearPrOpen.set(false);
    this.creatingPr.set(e.slug);
    const dest = this.destination || 'master';
    this.http.post<any>(
      `/api/pr?repo=${encodeURIComponent(e.slug)}&origin=${encodeURIComponent(this.origin)}&destination=${encodeURIComponent(dest)}&title=${encodeURIComponent(e.title)}`,
      {},
    ).subscribe({
      next: (r) => {
        if (r.ok && r.pr?.url) {
          this.repos.update((list) => list.map((repo) =>
            repo.slug === e.slug
              ? { ...repo, pr: { exists: true, url: r.pr.url, title: r.pr.title, state: r.pr.state } }
              : repo,
          ));
        } else {
          this.error.set(r.error ?? 'No se pudo crear el PR.');
        }
      },
      error: (err) => this.error.set(err.error?.error ?? 'Error al crear el PR.'),
      complete: () => this.creatingPr.set(null),
    });
  }

  // ─── Acciones masivas ──────────────────────────────────────────────────

  onMassPr(): void {
    this.accionesOpen.set(false);
    this.massWorking.set(true);
    const dest = this.destination || 'master';
    const title = `Release: ${this.origin} → ${dest}`;
    const slugs = this.accionesSelected()
      .filter((r) => this.reposStates()[r.slug] === 'found')
      .map((r) => r.slug);
    if (!slugs.length) {
      this.massWorking.set(false);
      return;
    }

    let created = 0;
    let errors = 0;
    let done = 0;

    for (const slug of slugs) {
      this.http.post<any>(
        `/api/pr?repo=${encodeURIComponent(slug)}&origin=${encodeURIComponent(this.origin)}&destination=${encodeURIComponent(dest)}&title=${encodeURIComponent(title)}`,
        {},
      ).subscribe({
        next: (r) => {
          if (r.ok) {
            created++;
            if (r.pr?.url) {
              this.repos.update((list) => list.map((repo) =>
                repo.slug === slug
                  ? { ...repo, pr: { exists: true, url: r.pr.url, title: r.pr.title, state: r.pr.state } }
                  : repo,
              ));
            }
          } else {
            errors++;
          }
        },
        error: () => errors++,
        complete: () => {
          done++;
          if (done === slugs.length) {
            this.massWorking.set(false);
            this.error.set(
              `PRs creados: ${created} de ${slugs.length}` +
              (errors ? ` | errores: ${errors}` : ''),
            );
          }
        },
      });
    }
  }

  onMassTagsDeploy(req: DeployTagsRequest): void {
    this.accionesOpen.set(false);
    this.massWorking.set(true);
    const prefixes = req.prefixes.join(',');
    let done = 0;
    let created = 0;
    let skipped = 0;
    let errors = 0;
    const total = req.slugs.length;

    for (const slug of req.slugs) {
      this.http.post<any>(
        `/api/tags?origin=${encodeURIComponent(this.origin)}&destination=${encodeURIComponent(this.destination || 'master')}&prefixes=${encodeURIComponent(prefixes)}&repo=${encodeURIComponent(slug)}`,
        {},
      ).subscribe({
        next: (r) => {
          if (r.ok) {
            const c: string[] = r.items?.flatMap((i: any) => i.created) ?? [];
            const s: string[] = r.items?.flatMap((i: any) => i.skipped) ?? [];
            created += c.length;
            skipped += s.length;
            if (c.length) {
              this.repos.update((list) => list.map((repo) => {
                if (repo.slug !== slug) return repo;
                const match_tag = { ...repo.match_tag };
                for (const tagName of c) {
                  const env = tagName.split('-').slice(0, -1).join('-') || tagName;
                  match_tag[env] = tagName;
                }
                return { ...repo, match_tag };
              }));
            }
          } else {
            errors++;
          }
        },
        error: () => errors++,
        complete: () => {
          done++;
          if (done === total) {
            this.massWorking.set(false);
            this.error.set(
              `Tags creados: ${created} | sin cambios: ${skipped}` +
              (errors ? ` | errores: ${errors}` : ''),
            );
          }
        },
      });
    }
  }

  onFlowTags(req: FlowTagsRequest): void {
    this.accionesOpen.set(false);
    const slugsParam = req.slugs.join(',');
    const request$ = req.clear
      ? this.http.delete<any>(`/api/flow-tags?repos=${encodeURIComponent(slugsParam)}`)
      : this.http.patch<any>(`/api/flow-tags?repos=${encodeURIComponent(slugsParam)}&tags=${encodeURIComponent(req.apply.join(','))}`, {});

    request$.subscribe({
      next: (r) => {
        if (r.ok && r.repos) {
          const tagsBySlug: Record<string, string[]> = r.repos;
          this.reposCache.update((list) =>
            list.map((repo) => {
              const newTags = tagsBySlug[repo.slug];
              if (newTags !== undefined) {
                return { ...repo, tags: newTags };
              }
              return repo;
            }),
          );
        }
      },
      error: () => this.error.set('Error al aplicar tags de flujo.'),
    });
  }

  // ─── Detalle repo ──────────────────────────────────────────────────────

  detalleOpen = signal(false);
  detalleSlug = signal<string | null>(null);
  private detalleFlow = signal<{ pr: DetalleRepo['pr']; matchTag: Record<string, string | null> } | null>(null);
  private detalleParams = signal<DetalleParam[]>([]);
  detalleRegions = signal<string[]>([]);
  detalleGenerating = signal<Record<string, boolean>>({});
  prUpdating = signal(false);

  readonly detalleRepo = computed<DetalleRepo | null>(() => {
    const slug = this.detalleSlug();
    if (!slug) return null;
    const cache = this.reposCache().find((r) => r.slug === slug);
    const flow = this.detalleFlow();
    const params = this.detalleParams();
    return {
      slug,
      workspace: cache?.workspace ?? '',
      resolved_branch: cache?.resolved_branch,
      pr: flow?.pr ?? null,
      tags: (flow?.matchTag ?? {}) as Record<string, string>,
      params,
    };
  });

  onAbrirDetalle(slug: string): void {
    this.detalleSlug.set(slug);
    this.detalleOpen.set(true);
    this.detalleFlow.set(null);
    this.detalleParams.set([]);
    this.detalleRegions.set([]);

    const dest = this.projectsDest();
    const prefixes = this.prefixes().join(',');
    const exclude = this.blacklisted().join(',');

    this.http.get<any>(buildFlowUrl({
      origin: this.origin,
      dest,
      prefixes,
      projectPrefixes: this.projectPrefixParam(),
      exclude,
      scanMode: this.scanMode(),
      force: 0,
      withTags: this.prefixes().length > 0,
      withDiff: true,
      repos: [slug],
    })).subscribe({
      next: (r) => {
        const row = (r.scan?.repos ?? [])[0];
        if (row) {
          this.detalleFlow.set({
            pr: row.pr?.exists ? { id: row.pr.id, url: row.pr.url, title: row.pr.title, state: row.pr.state } : null,
            matchTag: row.match_tag ?? {},
          });
        }
        const diffParams = (r.diff?.params ?? []) as SsmParam[];
        const environmentSet = new Set<string>();
        const repoParams: DetalleParam[] = [];
        for (const p of diffParams) {
          if (!p.repos?.includes(slug)) continue;
          const envValues = p.env_values ?? {};
          Object.keys(envValues).forEach((k) => environmentSet.add(k));
          repoParams.push({
            name: p.param,
            type: p.type ?? p.tipo ?? 'ssm',
            values: envValues,
            aws_status: p.aws_status ?? 'skipped',
          });
        }
        this.detalleParams.set(repoParams);
        this.detalleRegions.set([...environmentSet].sort());
      },
      error: () => this.error.set('Error al cargar detalle del repositorio.'),
    });
  }

  onDetalleBack(): void {
    this.detalleOpen.set(false);
    this.detalleSlug.set(null);
  }

  onActualizarPr(title: string): void {
    const slug = this.detalleSlug();
    if (!slug) return;
    this.prUpdating.set(true);
    const dest = this.destination || 'master';
    this.http.post<any>(
      `/api/prs/update-titles?origin=${encodeURIComponent(this.origin)}&destination=${encodeURIComponent(dest)}&title=${encodeURIComponent(title)}&repos=${encodeURIComponent(slug)}`,
      {},
    ).subscribe({
      next: () => {
        const flow = this.detalleFlow();
        if (flow?.pr) {
          this.detalleFlow.set({ ...flow, pr: { ...flow.pr, title } });
        }
        this.repos.update((list) => list.map((r) =>
          r.slug === slug ? { ...r, pr: { ...r.pr, title } } : r,
        ));
        this.reposCache.update((list) => list); // force recomputación
      },
      error: () => this.error.set('Error al actualizar título del PR.'),
      complete: () => this.prUpdating.set(false),
    });
  }

  onDetalleTag(env: string): void {
    const slug = this.detalleSlug();
    if (!slug || !this.origin) return;
    this.detalleGenerating.update((g) => ({ ...g, [env]: true }));
    const dest = this.destination || 'master';
    this.http.post<any>(
      `/api/tags?origin=${encodeURIComponent(this.origin)}&destination=${encodeURIComponent(dest)}&repo=${encodeURIComponent(slug)}&prefixes=${encodeURIComponent(env)}`,
      {},
    ).subscribe({
      next: (r) => {
        if (r.ok) {
          const created: string[] = r.items?.flatMap((i: any) => i.created) ?? [];
          for (const tagName of created) {
            const flow = this.detalleFlow();
            if (flow) {
              this.detalleFlow.set({ ...flow, matchTag: { ...flow.matchTag, [env]: tagName } });
            }
          }
        }
      },
      error: () => this.error.set('Error al generar tag.'),
      complete: () => this.detalleGenerating.update((g) => ({ ...g, [env]: false })),
    });
  }
}