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
import { QueryPanelComponent } from '../../components/query-panel/query-panel';
import { ReposTableComponent, ReposRow, ReposSortEvent } from '../../components/repos-table/repos-table';
import { ParamsPanelComponent } from '../../components/params-panel/params-panel';
import { ConfigModalComponent } from '../../components/config-modal/config-modal';
import { ReportModalComponent } from '../../components/report-modal/report-modal';
import { ConfirmModalComponent } from '../../components/confirm-modal/confirm-modal';
import { LoadingModalComponent } from '../../components/loading-modal/loading-modal';
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
  branch_url: string;
  commit: string;
  error?: string | null;
  visible?: boolean;  // BBIT-33: false = no mostrar en tabla (sin rama)
  reason?: string;    // BBIT-33: "branch_not_found" u otro motivo
  branch_state?: 'found' | 'not_found';  // BBIT-35 P3: estado de rama persistido
  resolved_branch?: string;              // BBIT-35 P3: rama efectiva (o variante)
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
    QueryPanelComponent,
    ReposTableComponent,
    ParamsPanelComponent,
    ConfigModalComponent,
    ReportModalComponent,
    ConfirmModalComponent,
    LoadingModalComponent,
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
  prTitle = '';
  prefixInput = '';
  prefixes = signal<string[]>(['uat', 'stgp', 'prod']);
  // BBIT-56: prefijos que ya tienen datos cargados en la tabla (columnas
  // visibles). Un prefijo agregado a `prefixes` NO pinta columna hasta que
  // una consulta exitosa lo acredite (sin placeholder "generate tag/—").
  loadedPrefixes = signal<string[]>([]);
  // BBIT-56: firma de la última consulta exitosa. Una petición idéntica
  // + memoria caliente (repos() ya pintado) = cero trabajo de red.
  lastQueryKey: string | null = null;
  // BBIT-56: base (sin prefixes) de la última consulta exitosa — para
  // detectar recargas selectivas (solo se agregaron ambientes).
  lastQueryBaseKey: string | null = null;
  // BBIT-56: firma SOLO de campos que el backend necesita re-consultar
  // (origin, dest, prefixes, mode). Si no cambió, no hay nada nuevo de red.
  lastBackendKey: string | null = null;

  // BBIT-56: firma canónica de la consulta actual (lo que el cliente pide).
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

  // BBIT-56: base de la consulta SIN prefixes — sirve para detectar que el
  // único cambio son ambientes nuevos (recarga selectiva) y no filtros.
  private queryBaseKey(): string {
    return JSON.stringify({
      origin: this.origin,
      destination: this.projectsDest(),
      projectPrefixes: [...this.projectPrefixes()].sort(),
      exclude: [...this.blacklisted()].sort(),
      mode: this.scanMode(),
    });
  }

  // BBIT-56: firma SOLO de los campos que el backend necesita re-consultar
  // (origin, dest, prefixes, mode). Si esto no cambió, no hay nada nuevo que
  // traer del backend: blacklist y projectPrefixes son filtrado cliente-side.
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

  // BBIT-56: último query_diff recibido del backend (para UI / logging).
  lastQueryDiff = signal<{ identical: boolean; added: Record<string, unknown>; removed: Record<string, unknown>; changed: Record<string, unknown> } | null>(null);

  repos = signal<ScanRepo[]>([]);
  projects = signal<ScanProject[]>([]);
  // BBIT-58: repos-quick resuelve branch_state (found/not_found) cuando se
  // pasa origin+destination; el cache local filtra los sin la rama antes de
  // pintar (displayFilteredRepos).
  reposCache = signal<{slug: string, name: string, workspace: string, default_branch: string, resolved_branch?: string, branch_state?: string}[]>([]);
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

  /** Modal de "Procesando…": se cierra al pintar la primera fila o a los 2 s. */
  spinnerVisible = signal(false);
  private spinnerTimer: ReturnType<typeof setTimeout> | undefined;

  /** Fase final del stream: el backend ya emitió todos los repos y está
   *  cerrando (stats → diff → done). Muestra "Finalizando consultas…". */
  finalizing = signal(false);

  /** Orden de la tabla por headers clickeables. */
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

reportOpen = signal(false);
  // Modo inicial del modal de reporte según quién lo abre ("Ver parámetros"
  // vs "Previsualizar reporte"); el estado del modal vive en ReportModal.
  reportInitialMode: 'branch' | 'pr' | 'tag' | 'params' = 'branch';

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

  // Session history
  sidebarOpen = signal(false);
  currentSessionId = signal<string | null>(null);
  sessions = signal<SessionConfig[]>([]);

  prefixCols(): string {
    return this.loadedPrefixes().map(() => ' 9.5rem').join('');
  }

  // BBIT-56: tras una consulta exitosa, los prefijos con datos son todos los
  // que se pidieron y la firma de la última consulta queda registrada (para
  // que la siguiente petición idéntica haga cero trabajo). Solo se llama en
  // cargas completas (SSE/batch), nunca en retry de repos fallidos.
  private syncLoadedPrefixes(): void {
    this.loadedPrefixes.set(this.prefixes().slice());
    this.lastQueryKey = this.queryKey();
    this.lastQueryBaseKey = this.queryBaseKey();
    this.lastBackendKey = this.queryBackendKey();
  }

  addPrefix() {
    const p = this.prefixInput.trim().toLowerCase();
    if (p && !this.prefixes().includes(p)) {
      // BBIT-56: el prefijo queda "pendiente" (en prefixes pero no en
      // loadedPrefixes) → la columna NO se pinta hasta "obtener repositorios".
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

  // Un repo se considera dentro de un proyecto si su slug empieza con alguno
  // de los prefijos de proyecto configurados (vacío = todos).
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
          // Auto-reuse tras reiniciar el server: recupera la sesión guardada
          // ANTES de cargar la última sesión, para no disparar /scan sin sesión.
          this.reuseSession(() => this.loadLatestSession());
        } else if (r.needs_tokens) {
          // BBIT-33 Phase 7: Si falta token, abre Config automáticamente.
          // El modal refresca client + AWS al abrirse.
          this.configOpen.set(true);
          this.storedCreds.set(false);
        } else {
          this.storedCreds.set(false);
        }
      },
    });

    // Cargar historial de sesiones
    this.refreshSessions();
  }

  private loadLatestSession(): void {
    const latest = this.sessionHistory.getLatest();
    if (latest) {
      this.loadSession(latest);
    }
  }

  ngOnInit(): void {
    // Effect para mantener sessions signal sincronizado
    // (se actualiza via refreshSessions() en cada cambio)
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
    // "Forzar consultas" es un toggle transitorio de UNA consulta: no se
    // persiste en la sesión ni se restaura al recargar (ver BBIT-20), para
    // que un reload nunca dispare de nuevo el barrido completo a las APIs.
    this.forceCache.set(false);
    this.currentSessionId.set(session.id);
    // BBIT-53: la sección SSM pertenece a la sesión que la calculó; al
    // cambiar de sesión se limpia y solo reaparece al presionar "Cargar parámetros".
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
    }, 15000); // 15 segundos de timeout
    
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
        // Ya se maneja en next/error
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
    }, 15000); // 15 segundos de timeout
    
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
          // Ya se maneja en next/error
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
      },
    });
  }

  projectsDest(): string {
    return this.destination || 'master';
  }

  /**
   * Attempt to load repos via SSE streaming for incremental rendering.
   * Returns true if SSE started successfully, false if fallback is needed.
   */
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

    // BBIT-56: capturar el diff de la consulta vs la anterior. El backend
    // lo emite como primer evento; en un reload frío (repos vacíos) el front
    // aún necesita los datos, por lo que se almacena para referencia sin
    // cortar el flujo.
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
        // BBIT-35 P4: repos sin rama (visible=false / branch_state=not_found)
        // se REMUEVEN de la tabla en vivo (antes eran placeholders colgados).
        if (repo.visible !== false) {
          onRepo(repo);
        } else {
          console.log(`[SSE] Repo ${repo.slug} sin rama (visible=false), removiendo de tabla`);
          onRepoHidden(repo.slug);
        }
      }
    });

    // Nota: evento 'skip' ya no se usa; los repos sin rama vienen con visible=false en el evento 'repo'

    es.addEventListener('field', (e: MessageEvent) => {
      console.log(`[SSE] Received field event:`, e.data.substring(0, 100));
      firstEventReceived = true;
      const parsed = processSseEvent('field', e.data, repos);
      if (parsed?.type === 'field') {
        // BBIT-35 P5: merge parcial de un campo (commit/pr/tags/deploys) sin esperar el repo completo.
        onField(parsed.field.slug, parsed.field.field, parsed.field.value);
      }
    });

    es.addEventListener('stats', (e: MessageEvent) => {
      console.log(`[SSE] Received stats event`);
      const parsed = processSseEvent('stats', e.data, repos);
      if (parsed?.type === 'stats') {
        // Todos los repos escaneados: el backend está cerrando (diff + done).
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
      // Server-sent `event: error` carries data; network-level errors
      // dispatch an Event without data and are handled by es.onerror below.
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
      // BBIT-53: el diff SSM solo se procesa cuando el usuario presiona
      // "Cargar parámetros" (withDiff=true). Sin diffs, se preserva el
      // estado previo de la sección y no se prende el spinner del botón.
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

    // Retry-only path always uses batch HTTP (single repo re-scan).
    // BBIT-53: el retry no pide diff (with_diff=0); re-escanea solo los
    // repos fallidos y la sección SSM queda como estaba (no se re-procesa).
    if (retryOnly) {
      this.loadReposBatch(retryOnly, done, withDiff);
      return;
    }

    // BBIT-56: decidir la estrategia de carga en una función pura testeable:
    // - identical → la petición es idéntica a la última exitosa y la tabla ya
    //   está pintada → cero trabajo de red (ni SSE ni /repos-quick).
    // - selective → solo se agregaron ambientes → consultar columnas nuevas.
    // - removal → cambiaron filtros cliente-side (blacklist/project_prefixes)
    //   → refiltrar en el cliente sin re-consultar lo que ya está cargado.
    // - full → cualquier otro cambio, fuerza o withDiff ("Cargar parámetros").
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
      // Refresh de keys: si llegamos acá por "se quitaron ambientes" (ya
      // cargados), las keys de estrategia quedaron desactualizadas y la
      // próxima consulta idéntica las necesita frescas.
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

    // QUICK LOAD: Si ya tenemos repos en caché LOCAL, usarlos sin HTTP.
    // Si no, hacer llamada a /repos-quick primero.
    if (this.reposCache().length > 0) {
      // Ya tenemos repos: mostrar tabla filtrada, cargar datos pesados en background
      this.displayFilteredRepos();
      this.loadReposHeavy(done, withDiff);
      return;
    }
    
    // Primera vez: cargar repos desde backend
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
          // Guardar en caché LOCAL (cliente)
          this.reposCache.set(r.repos);
          // Mostrar repos filtrados inmediatamente
          this.displayFilteredRepos();
          this.clearSpinner();
          // Ahora cargar datos pesados en background
          this.loadReposHeavy(done, withDiff);
        }
      },
      error: () => {
        // Si falla /repos-quick, fallback a SSE/batch
        this.loadReposHeavy(done, withDiff);
      }
    });
  }

  private displayFilteredRepos(): void {
    // Filtrar repos cacheados localmente (cliente-side, sin HTTP)
    const projectPrefixesParam = this.projectPrefixParam();
    const blacklist = this.blacklisted();
    
    const filtered = this.reposCache().filter((r: any) => {
      // BBIT-58: repos sin la rama origen quedan fuera de la tabla. El campo
      // branch_state llega de repos-quick cuando se pasa origin+destination;
      // si no viene (retrocompat), se muestra el repo (lo decide el SSE).
      if ('branch_state' in r && r.branch_state === 'not_found') {
        return false;
      }
      // Aplicar filtros de prefijo de proyecto
      if (projectPrefixesParam) {
        const prefixes = projectPrefixesParam.split(',').filter(p => p.trim());
        const slug = r.slug.toLowerCase();
        if (!prefixes.some(p => slug.startsWith(p.toLowerCase()))) {
          return false;
        }
      }
      // Aplicar blacklist
      if (blacklist.includes(r.slug.toLowerCase())) {
        return false;
      }
      return true;
    });

    // Convertir a ScanRepo para mostrar en tabla (sin metadata pesada todavía)
    const quickRepos: ScanRepo[] = filtered.map((repo: any) => ({
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
    // Normal load: try SSE streaming first, fallback to batch on failure.
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
        // Actualizar el signal DIRECTAMENTE: agregar el nuevo repo a la tabla
        this.repos.update((current) => {
          // Buscar si el repo ya existe (por slug)
          const existing = current.find((r) => r.slug === item.slug);
          if (existing) {
            // Reemplazar: el repo fue re-escaneado, actualizar su info
            return current.map((r) => (r.slug === item.slug ? item : r));
          } else {
            // Nuevo: agregar a la tabla
            return [...current, item];
          }
        });
      },
      (stats) => {
        this.stats.set(stats as ScanStats);
      },
      (diff) => {
        // BBIT-53: sin diff solicitado no se pinta la sección SSM.
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
        // SSE failed — fallback to batch
        scheduleFallback();
      },
      (slug) => {
        // BBIT-35 P4: el repo no tiene la rama → remover la fila placeholder en vivo
        this.repos.update((current) => current.filter((r) => r.slug !== slug));
      },
      (slug, field, value) => {
        // BBIT-35 P5: pintado por campo async — merge parcial sin esperar el repo completo.
        // La fila existe (placeholder de repos-quick) o se crea con el campo resuelto.
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

  // BBIT-56: recarga SELECTIVA — solo los prefixes NUEVOS se consultan al
  // backend (prefixesOverride). El SSE devolverá items con deploys solo para
  // esos prefixes. El handler de merge por columna preserva los deploys
  // existentes de los otros prefixes y solo actualiza los nuevos.
  // BBIT-56: remoción sin re-consulta — cuando cambian blacklist o
  // project_prefixes (filtros cliente-side) con el mismo origin/dest/prefixes/
  // mode, la tabla ya pintada se refiltra en el cliente sin tocar red. Los
  // repos que dejan de matchear desaparecen; los que quedan preservan sus
  // datos (no se re-consultaron).
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
        // Merge por columna: los deploys que trae el item son SOLO los del
        // prefijo nuevo; los existentes se preservan intactos.
        this.repos.update((current) => {
          const idx = current.findIndex((r) => r.slug === item.slug);
          if (idx === -1) {
            // Repo nuevo: agregar fila completa
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
        // En recarga selectiva, si el repo desaparece (branch not found),
        // se remueve la fila como en carga normal.
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
          // Reintento: reempleza solo las filas de los repos re-consultados.
          const bySlug = new Map<string, ScanRepo>((r.scan?.repos ?? []).map((row: ScanRepo) => [row.slug, row]));
          this.repos.update((current) => current.map((repo) => bySlug.get(repo.slug) ?? repo));
        }

        const diff = r.diff ?? {};
        // BBIT-53: sin diff solicitado no se pinta la sección SSM.
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
    // BBIT-53: "Cargar parámetros" es el único camino que procesa el diff SSM.
    this.loadRepos(false, true);
  }

  // Alias: tras crear/actualizar PRs o tags se recarga la tabla (no encadena params).
  // Solo reprocesa el diff SSM si la sección ya estaba cargada (paramsLoaded).
  resolve() {
    this.loadRepos(false, this.paramsLoaded());
  }

  /** Re-escanea un solo repo (deploy status, tags, PRs) y hace merge. */
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

  onSortRepo(event: ReposSortEvent): void {
    this.toggleRepoSort(event.key, event.env);
  }

  onGenerateTag(event: { repo: ReposRow; env: string }): void {
    this.generateTags(event.repo, event.env);
  }

  /** El config-modal guardó credenciales de Bitbucket → recrear la sesión. */
  onBitbucketReauth(): void {
    // Destruir sesión anterior (con workspace/token viejo)
    this.http.delete<any>('/api/session').subscribe({
      complete: () => {
        // Desconectar UI localmente
        this.connected.set(false);
        this.identity.set('');
        this.repoCount.set(0);
        this.repos.set([]);
        this.reposCache.set([]);
        this.projects.set([]);
        this.params.set([]);
        this.removed.set([]);
        this.stats.set(null);

        // Ahora reconectar con nuevas credenciales
        window.setTimeout(() => {
          this.configOpen.set(false);
          this.reuseSession(() => this.loadLatestSession());
        }, 500);
      },
    });
  }

  openReport() {
    this.reportInitialMode = 'branch';
    this.reportOpen.set(true);
  }

  openParams() {
    this.reportInitialMode = 'params';
    this.reportOpen.set(true);
  }

  createPr(repo: ScanRepo) {
    this.creatingPr.set(repo.slug);
    const dest = this.destination || 'master';
    const title = encodeURIComponent(this.prTitle || `Release: ${this.origin} → ${dest}`);
    this.http.post<any>(`/api/pr?repo=${encodeURIComponent(repo.slug)}&origin=${encodeURIComponent(this.origin)}&destination=${encodeURIComponent(dest)}&title=${title}`, {})
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
    const title = encodeURIComponent(this.prTitle || `Release: ${this.origin} → ${dest}`);
    const action = target === 'create' ? 'create-missing' : 'update-titles';
    const filters = `&project_prefixes=${encodeURIComponent(this.projectPrefixParam())}&exclude=${encodeURIComponent(this.blacklisted().join(','))}`;
    this.http.post<any>(`/api/prs/${action}?origin=${encodeURIComponent(this.origin)}&destination=${encodeURIComponent(dest)}&title=${title}${filters}`, {})
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
}