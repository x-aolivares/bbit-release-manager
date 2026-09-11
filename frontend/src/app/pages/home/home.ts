import { ActivatedRoute, Router } from '@angular/router';
import { Component, HostListener, inject, signal, effect, computed, OnInit } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { FormsModule } from '@angular/forms';
import { IonHeader } from '@ionic/angular/ion-header';
import { IonToolbar } from '@ionic/angular/ion-toolbar';
import { IonContent } from '@ionic/angular/ion-content';
import { IonSpinner } from '@ionic/angular/ion-spinner';
import { IonCard } from '@ionic/angular/ion-card';
import { IonCardContent } from '@ionic/angular/ion-card-content';
import { IonIcon } from '@ionic/angular';
import { SessionHistoryService, SessionConfig } from '../../services/session-history.service';
import { SessionSidebarComponent } from '../../components/session-sidebar/session-sidebar';
import { buildFlowUrl, processSseEvent, repoUrl as flowRepoUrl, sortRepos } from './flow-utils';
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
  no_changes?: boolean;
  error?: string | null;
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

interface ServiceState {
  stored: boolean;
  expires_at?: number | null;
  warning?: string | null;
}

interface ConfigField {
  key: string;
  label: string;
  secret: boolean;
  placeholder: string;
  checkbox?: boolean;
  dependsOn?: string;
}

interface AwsEnvironment {
  name: string;
  region: string;
  localstack: boolean;
  endpoint_url: string;
}

@Component({
  selector: 'app-home',
  templateUrl: './home.html',
  styleUrl: './home.scss',
  standalone: true,
  imports: [
    FormsModule,
    IonHeader, IonToolbar,
    IonContent, IonSpinner,
    IonCard, IonCardContent,
    IonIcon,
    SessionSidebarComponent,
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

  alias = '';
  workspace = 'my_org_web_dev';
  token = '';
  circleciToken = '';
  origin = '';
  destination = 'master';
  prTitle = '';
  prefixInput = '';
  prefixes = signal<string[]>(['uat', 'stgp', 'prod']);

  projectPrefixInput = '';
  projectPrefixes = signal<string[]>([]);
  addingProjectPrefix = signal(false);

  blacklistInput = '';
  blacklisted = signal<string[]>([]);
  addingBlacklist = signal(false);
  forceCache = signal(false);

  bbTokenUrl = 'https://id.atlassian.com/manage-profile/security/api-tokens';
  cciTokenUrl = 'https://app.circleci.com/settings/user/tokens';

  repos = signal<ScanRepo[]>([]);
  projects = signal<ScanProject[]>([]);
  reposCache = signal<{slug: string, name: string, workspace: string, default_branch: string}[]>([]);
  params = signal<SsmParam[]>([]);
  removed = signal<RemovedParam[]>([]);
  scanMode = 'diff';
  stats = signal<ScanStats | null>(null);
  addingPrefix = signal(false);
  ciConfigured = signal(true);
  ciError = signal<string | null>(null);
  storedCreds = signal(false);
  circleciOpen = false;
  askDisconnect = signal(false);
  syncingPrs = signal(false);

  userMenuOpen = signal(false);
  configOpen = signal(false);
  services = signal<Record<string, ServiceState>>({});
  clientAlias = signal('local');
  savingService = signal<string | null>(null);
  configMessage = signal<{ kind: 'ok' | 'error'; text: string } | null>(null);
  gitClonesDir = signal('');
  gitClonesEnabled = signal(false);
  gitCloning = signal(false);
  gitCloneStatus = signal<string | null>(null);
  userMenuPosition = signal<{ top: number; right: number }>({ top: 0, right: 0 });
  awsEnvironments = signal<AwsEnvironment[]>([]);
  awsEnvironmentLoading = signal(false);
  savingAwsEnvironments = signal(false);
  awsNewEnv = '';
  awsNewRegion = '';

  readonly serviceKeys = ['bitbucket', 'circleci', 'aws'];
  readonly serviceFields: Record<string, ConfigField[]> = {
    bitbucket: [
      { key: 'url', label: 'BITBUCKET_URL', secret: false, placeholder: 'https://bitbucket.org' },
      { key: 'workspace', label: 'BITBUCKET_WORKSPACE', secret: false, placeholder: 'my_org_web_dev' },
      { key: 'username', label: 'BITBUCKET_USERNAME', secret: false, placeholder: '@usuario' },
      { key: 'token', label: 'BITBUCKET_TOKEN', secret: true, placeholder: 'ATATT...' },
    ],
    circleci: [
      { key: 'token', label: 'CIRCLECI_TOKEN', secret: true, placeholder: 'CCIPAT...' },
      { key: 'vcs', label: 'CIRCLECI_VCS', secret: false, placeholder: 'bb' },
      { key: 'org', label: 'CIRCLECI_ORG', secret: false, placeholder: 'my_org_web_dev' },
    ],
    aws: [
      { key: 'profile', label: 'AWS_PROFILE', secret: false, placeholder: 'bbit-release' },
    ],
  };
  configForm: Record<string, Record<string, string>> = {
    bitbucket: { url: '', workspace: '', username: '', token: '' },
    circleci: { token: '', vcs: '', org: '' },
    aws: { profile: '' },
  };

  serviceLabel(svc: string): string {
    return { bitbucket: 'Bitbucket', circleci: 'CircleCI', aws: 'AWS' }[svc] ?? svc;
  }

  toggleUserMenu(): void {
    if (this.userMenuOpen()) {
      this.userMenuOpen.set(false);
      return;
    }
    const trigger = (document.querySelector('.bb-user-menu-trigger') as HTMLElement | null);
    if (trigger) {
      const rect = trigger.getBoundingClientRect();
      this.userMenuPosition.set({ top: rect.bottom + 6, right: Math.round(window.innerWidth - rect.right) });
    }
    this.userMenuOpen.set(true);
  }

  @HostListener('document:click', ['$event'])
  onDocClick(event: Event): void {
    const t = event.target as HTMLElement;
    if (this.userMenuOpen() && !t.closest('.bb-user-menu-wrap') && !t.closest('.bb-user-menu')) {
      this.userMenuOpen.set(false);
    }
  }

  @HostListener('window:scroll')
  onWindowScroll(): void {
    if (this.userMenuOpen()) {
      this.userMenuOpen.set(false);
    }
  }

  @HostListener('window:resize')
  onWindowResize(): void {
    if (this.userMenuOpen()) {
      this.userMenuOpen.set(false);
    }
  }

  @HostListener('document:keydown.escape')
  onEscape(): void {
    this.userMenuOpen.set(false);
  }

  closeUserMenuAndDisconnect(): void {
    this.userMenuOpen.set(false);
    this.askDisconnect.set(true);
  }

  openConfig(): void {
    this.userMenuOpen.set(false);
    this.configMessage.set(null);
    this.configOpen.set(true);
    this.refreshClient();
    this.loadAwsEnvironments();
  }

  awsEnvironmentRows = computed<{ name: string; region: string; localstack: boolean; endpoint_url: string }[]>(
    () => [...this.awsEnvironments()]
      .sort((a, b) => a.name.localeCompare(b.name)),
  );

  private loadAwsEnvironments(): void {
    this.awsEnvironmentLoading.set(true);
    this.http.get<any>('/api/ssm/environments').subscribe({
      next: (r) => this.awsEnvironments.set(r.environments ?? []),
      error: () => this.awsEnvironments.set([]),
      complete: () => this.awsEnvironmentLoading.set(false),
    });
  }

  setAwsRegion(name: string, value: string): void {
    this.awsEnvironments.update((list) =>
      list.map((e) => (e.name === name ? { ...e, region: value } : e)),
    );
  }

  setAwsLocalstack(name: string, checked: boolean): void {
    this.awsEnvironments.update((list) =>
      list.map((e) => (e.name === name ? { ...e, localstack: checked } : e)),
    );
  }

  setAwsEndpoint(name: string, value: string): void {
    this.awsEnvironments.update((list) =>
      list.map((e) => (e.name === name ? { ...e, endpoint_url: value } : e)),
    );
  }

  addAwsEnvironment(): void {
    const env = (this.awsNewEnv || '').trim();
    if (!env) return;
    const region = (this.awsNewRegion || '').trim();
    this.awsEnvironments.update((list) => {
      if (list.some((e) => e.name === env)) {
        return list.map((e) => (e.name === env ? { ...e, region } : e));
      }
      return [...list, { name: env, region, localstack: false, endpoint_url: '' }];
    });
    this.awsNewEnv = '';
    this.awsNewRegion = '';
  }

  saveAwsEnvironments(): void {
    this.savingAwsEnvironments.set(true);
    this.configMessage.set(null);
    const environments = this.awsEnvironmentRows()
      .filter((e) => (e.name || '').trim())
      .map((e) => ({
        name: e.name.trim(),
        region: (e.region || '').trim(),
        localstack: e.localstack,
        endpoint_url: (e.endpoint_url || '').trim(),
      }));
    this.http.patch<any>('/api/ssm/environments', { environments }).subscribe({
      next: () => {
        this.configMessage.set({ kind: 'ok', text: 'Ambientes y regiones guardados.' });
        this.loadAwsEnvironments();
      },
      error: (e) => this.configMessage.set({
        kind: 'error',
        text: e.error?.detail ?? e.error?.error ?? 'No se pudieron guardar los ambientes.',
      }),
      complete: () => this.savingAwsEnvironments.set(false),
    });
  }

  private refreshClient(): void {
    this.http.get<any>('/api/client').subscribe({
      next: (r) => {
        this.services.set(r.services ?? {});
        this.clientAlias.set(r.client?.alias ?? 'local');
        const auth = r.auth ?? {};
        for (const svc of this.serviceKeys) {
          const vals = auth[svc] ?? {};
          if (svc === 'aws') {
            // el rest de AWS (región/endpoint/LocalStack) vive por ambiente
            // en la tabla de ambientes; acá solo el profile.
            this.configForm[svc] = { ...this.configForm[svc], profile: (vals['profile'] ?? '').trim() };
          } else {
            this.configForm[svc] = { ...this.configForm[svc], ...vals };
          }
        }
      },
    });
  }

  saveServiceAuth(service: string): void {
    this.savingService.set(service);
    this.configMessage.set(null);
    const body: Record<string, string> = { ...this.configForm[service] };

    // Para AWS: enviar endpoint/region del primer ambiente docker para la
    // validación STS, ya que el form solo expone el profile.
    if (service === 'aws') {
      const dockerEnv = this.awsEnvironmentRows().find(
        (e) => e.localstack && e.endpoint_url,
      );
      if (dockerEnv) {
        body['endpoint_url'] = dockerEnv.endpoint_url;
        body['localstack'] = '1';
        body['region'] = dockerEnv.region;
      }
    }

    this.http.post<any>(`/api/auth/${service}`, body).subscribe({
      next: (r) => {
        if (r.ok) {
          this.configMessage.set({ kind: 'ok', text: `${this.serviceLabel(service)} guardado y validado.` });
          this.refreshClient();
        } else {
          this.configMessage.set({ kind: 'error', text: r.error ?? 'No se pudo guardar.' });
        }
      },
      error: (e) => {
        this.configMessage.set({
          kind: 'error',
          text: e.error?.error ?? e.error?.detail ?? 'Error de red al guardar.',
        });
        this.savingService.set(null);
      },
      complete: () => this.savingService.set(null),
    });
  }

  onFieldToggle(svc: string, key: string, checked: boolean): void {
    this.configForm[svc] = { ...this.configForm[svc], [key]: checked ? '1' : '' };
  }

  onFolderSelected(event: Event): void {
    const input = event.target as HTMLInputElement;
    if (!input.files || input.files.length === 0) return;
    
    // Obtener la ruta de la carpeta del primer archivo seleccionado
    // En navegadores modernos, webkitdirectory proporciona la ruta relativa
    const firstFile = input.files[0];
    const filePath = (firstFile as any).webkitRelativePath || '';
    
    if (filePath) {
      // Extraer la carpeta base (antes del primer archivo)
      const folderPath = filePath.split('/').slice(0, -1).join('/');
      if (folderPath) {
        this.gitClonesDir.set(folderPath);
      }
    }
    
    // Limpiar el input para permitir seleccionar la misma carpeta nuevamente
    input.value = '';
  }

  saveGitClonesDir(): void {
    this.configMessage.set(null);
    this.http.put<any>('/api/session/git', { clones_dir: this.gitClonesDir() }).subscribe({
      next: (r) => {
        if (r.ok) {
          this.gitClonesEnabled.set(!!r.git?.enabled);
          this.configMessage.set({
            kind: 'ok',
            text: 'Carpeta de clones guardada. Reconectá la sesión para activar el motor local-git.',
          });
        } else {
          this.configMessage.set({ kind: 'error', text: r.error ?? 'No se pudo guardar.' });
        }
      },
      error: (e) => this.configMessage.set({
        kind: 'error',
        text: e.error?.error ?? e.error?.detail ?? 'Error de red al guardar.',
      }),
    });
  }

  cloneRepos(): void {
    if (!this.gitClonesDir().trim()) return;
    this.gitCloning.set(true);
    this.gitCloneStatus.set(null);
    this.configMessage.set(null);
    this.http.post<any>('/api/session/clone', {}).subscribe({
      next: (r) => {
        if (r.ok) {
          const repos: Array<{ ok: boolean; slug: string }> = r.repos ?? [];
          const ok = repos.filter((x) => x.ok).length;
          const failed = repos.length - ok;
          this.gitCloneStatus.set(`Repos en carpeta: ${repos.length} (${ok} ok${failed ? `, ${failed} fallidos` : ''}).`);
        } else {
          this.configMessage.set({ kind: 'error', text: r.error ?? 'No se pudo clonar.' });
        }
      },
      error: (e) => this.configMessage.set({
        kind: 'error',
        text: e.error?.error ?? e.error?.detail ?? 'No se pudo clonar.',
      }),
      complete: () => this.gitCloning.set(false),
    });
  }

  reposLoading = signal(false);
  paramsLoading = signal(false);
  tableLoaded = signal(false);
  paramsLoaded = signal(false);

  /** Modal de "Procesando…": se cierra al pintar la primera fila o a los 2 s. */
  spinnerVisible = signal(false);
  private spinnerTimer: ReturnType<typeof setTimeout> | undefined;

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
  reportMode: 'branch' | 'pr' | 'tag' | 'params' = 'branch';
  reportEnv = signal(0);
  reportCopied = signal(false);

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
    return this.prefixes().map(() => ' 9.5rem').join('');
  }

  addPrefix() {
    const p = this.prefixInput.trim().toLowerCase();
    if (p && !this.prefixes().includes(p)) {
      this.prefixes.update((list) => [...list, p]);
    }
    this.prefixInput = '';
  }

  removePrefix(index: number) {
    this.prefixes.update((list) => list.filter((_, i) => i !== index));
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
      next: (h) => this.health.set(h),
      error: () => this.error.set('No se pudo contactar la API.'),
    });
    this.http.get<any>('/api/session').subscribe({
      next: (r) => {
        this.services.set(r.services ?? {});
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
    this.loadRepos();
  }

  reuseSession(onSuccess?: () => void) {
    this.loading.set(true);
    this.error.set(null);
    const body: Record<string, string> = {};
    if (this.alias.trim()) {
      body['alias'] = this.alias.trim();
    }
    
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
          this.services.set(r.services ?? {});
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

  connect() {
    this.loading.set(true);
    this.error.set(null);
    const body: Record<string, string> = {
      workspace: this.workspace,
      token: this.token,
      project_prefixes: this.projectPrefixParam(),
      exclude_repos: this.blacklisted().join(','),
    };
    if (this.alias.trim()) {
      body['alias'] = this.alias.trim();
    }
    if (this.circleciToken) {
      body['circleci_token'] = this.circleciToken;
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
            this.services.set(r.services ?? {});
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
        this.services.set({});
        this.clientAlias.set('local');
        this.userMenuOpen.set(false);
        this.configOpen.set(false);
        this.storedCreds.set(!deleteCredentials);
      },
    });
  }

  projectsDest(): string {
    return this.destination || 'master';
  }

  identityInitials(): string {
    const id = this.identity().trim();
    if (!id) return '';
    const parts = id.split(/\s+/);
    if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
    return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
  }

  /**
   * Attempt to load repos via SSE streaming for incremental rendering.
   * Returns true if SSE started successfully, false if fallback is needed.
   */
  private scanSse(
    onRepo: (item: ScanRepo) => void,
    onStats: (stats: ScanStats) => void,
    onDiff: (diff: DiffResponse) => void,
    onDone: () => void,
    onError: (msg: string) => void,
    onTimeout: () => void,
  ): boolean {
    const dest = this.projectsDest();
    const prefixes = this.prefixes().join(',');
    const exclude = this.blacklisted().join(',');
    const force = this.forceCache() ? 1 : 0;
    const url = `/api/flow/stream?origin=${encodeURIComponent(this.origin)}&destination=${encodeURIComponent(dest)}&prefixes=${encodeURIComponent(prefixes)}&project_prefixes=${encodeURIComponent(this.projectPrefixParam())}&exclude=${encodeURIComponent(exclude)}&mode=${this.scanMode}&force=${force}`;

    let firstEventReceived = false;
    const timeout = setTimeout(() => {
      if (!firstEventReceived) {
        es.close();
        onTimeout();
      }
    }, 3000);

    let repos: ScanRepo[] = [];
    const es = new EventSource(url);

    es.addEventListener('repo', (e: MessageEvent) => {
      firstEventReceived = true;
      const parsed = processSseEvent('repo', e.data, repos);
      if (parsed?.type === 'repo') {
        repos = parsed.repos;
        onRepo(parsed.repos[parsed.repos.length - 1]);
      }
    });

    es.addEventListener('stats', (e: MessageEvent) => {
      const parsed = processSseEvent('stats', e.data, repos);
      if (parsed?.type === 'stats') {
        onStats(parsed.stats as unknown as ScanStats);
      }
    });

    es.addEventListener('diff', (e: MessageEvent) => {
      const parsed = processSseEvent('diff', e.data, repos);
      if (parsed?.type === 'diff') {
        onDiff(parsed.diff as unknown as DiffResponse);
      }
    });

    es.addEventListener('done', () => {
      clearTimeout(timeout);
      es.close();
      onDone();
    });

    es.addEventListener('error', (e: MessageEvent) => {
      // Server-sent `event: error` carries data; network-level errors
      // dispatch an Event without data and are handled by es.onerror below.
      if (!e.data) return;
      clearTimeout(timeout);
      es.close();
      const parsed = processSseEvent('error', e.data, repos);
      if (parsed?.type === 'error') {
        onError(parsed.message);
      } else {
        onError('Stream error');
      }
    });

    es.onerror = () => {
      clearTimeout(timeout);
      es.close();
      onTimeout();
    };

    return true;
  }

  loadRepos(retryOnly = false) {
    if (!this.origin) return;
    this.reposLoading.set(true);
    this.error.set(null);
    this.creatingPr.set(null);
    this.scheduleSpinnerCap();
    if (!retryOnly) {
      this.paramsLoading.set(true);
      this.tableLoaded.set(true);
      this.params.set([]);
      this.removed.set([]);
      this.paramsLoaded.set(false);
    }
    const startedAt = Date.now();
    const done = () => this.releaseBusy(startedAt, () => {
      this.clearSpinner();
      this.reposLoading.set(false);
      if (!retryOnly) {
        this.paramsLoading.set(false);
      }
    });

    // Retry-only path always uses batch HTTP (single repo re-scan).
    if (retryOnly) {
      this.loadReposBatch(retryOnly, done);
      return;
    }

    // QUICK LOAD: Si ya tenemos repos en caché LOCAL, usarlos sin HTTP.
    // Si no, hacer llamada a /repos-quick primero.
    if (this.reposCache().length > 0) {
      // Ya tenemos repos: mostrar tabla filtrada, cargar datos pesados en background
      this.displayFilteredRepos();
      this.loadReposHeavy(done);
      return;
    }
    
    // Primera vez: cargar repos desde backend
    const projectPrefixesParam = this.projectPrefixParam();
    const excludeParam = this.blacklisted().join(',');
    
    this.http.get<any>('/api/repos-quick', {
      params: {
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
          this.loadReposHeavy(done);
        }
      },
      error: () => {
        // Si falla /repos-quick, fallback a SSE/batch
        this.loadReposHeavy(done);
      }
    });
  }

  private displayFilteredRepos(): void {
    // Filtrar repos cacheados localmente (cliente-side, sin HTTP)
    const projectPrefixesParam = this.projectPrefixParam();
    const blacklist = this.blacklisted();
    
    const filtered = this.reposCache().filter((r: any) => {
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

  private loadReposHeavy(done: () => void): void {
    // Normal load: try SSE streaming first, fallback to batch on failure.
    let batchFallbackScheduled = false;
    const scheduleFallback = () => {
      if (batchFallbackScheduled) return;
      batchFallbackScheduled = true;
      console.warn('SSE streaming unavailable, falling back to batch.');
      this.loadReposBatch(false, done);
    };

    const repos: ScanRepo[] = [];

    this.scanSse(
      (item) => {
        repos.push(item);
        this.repos.set([...repos]);
      },
      (stats) => {
        this.stats.set(stats as ScanStats);
      },
      (diff) => {
        this.params.set((diff.params ?? []) as unknown as SsmParam[]);
        this.removed.set((diff.removed ?? []) as unknown as RemovedParam[]);
        this.paramsLoaded.set(true);
      },
      () => {
        done();
        this.saveCurrentSession();
      },
      (msg) => {
        this.error.set(msg);
        done();
      },
      () => {
        // SSE failed — fallback to batch
        scheduleFallback();
      },
    );
  }

  private loadReposBatch(retryOnly: boolean, done: () => void): void {
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
      scanMode: this.scanMode,
      force,
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
        this.params.set(diff.params ?? []);
        this.removed.set(diff.removed ?? []);
        this.paramsLoaded.set(true);
      },
      error: () => {
        this.error.set('Error al cargar la tabla.');
        done();
      },
      complete: () => {
        done();
        this.saveCurrentSession();
      },
    });
  }

  loadParams() {
    this.loadRepos();
  }

  // Alias: tras crear/actualizar PRs o tags se recarga la tabla (no encadena params).
  resolve() {
    this.loadRepos();
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
      scanMode: this.scanMode,
      force: 0,
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

  estadoLabel(estado: string): string {
    switch (estado) {
      case 'reutilizado':
        return 'Reutilizado (productivo)';
      case 'solo destino':
        return 'Solo destino';
      default:
        return 'Nuevo';
    }
  }

  tipoClass(tipo: string): string {
    switch (tipo) {
      case 'reutilizado':
        return 'bb-badge--warn';
      case 'solo destino':
        return 'bb-badge--neutral';
      default:
        return 'bb-badge--ok';
    }
  }

  openReport() {
    this.reportMode = 'branch';
    this.reportEnv.set(0);
    this.reportCopied.set(false);
    this.reportOpen.set(true);
  }

  openParams() {
    this.reportMode = 'params';
    this.reportEnv.set(0);
    this.reportCopied.set(false);
    this.reportOpen.set(true);
  }

  selectReportMode(mode: 'branch' | 'pr' | 'tag' | 'params') {
    this.reportMode = mode;
    this.reportCopied.set(false);
  }

  reportEnvPrefix(): string {
    return this.prefixes()[this.reportEnv()] ?? '';
  }

  reportText(): string {
    if (this.reportMode === 'params') {
      return this.paramRows().map((r) => r.param).join('\n');
    }
    const repos = [...this.repos()].sort((a, b) => a.slug.localeCompare(b.slug));
    const lines: string[] = [];
    for (const repo of repos) {
      let url = '';
      if (this.reportMode === 'branch') {
        url = repo.branch_url ?? '';
      } else if (this.reportMode === 'pr') {
        url = repo.pr?.exists && repo.pr.url ? repo.pr.url : '';
      } else {
        const env = this.reportEnvPrefix().toLowerCase();
        const tag = env ? this.envTag(repo, env) : null;
        url = tag ? this.envTagHref(repo, env) : '';
      }
      lines.push(url || '-------------------');
    }
    return lines.join('\n');
  }

  async copyReport() {
    try {
      await navigator.clipboard.writeText(this.reportText());
      this.reportCopied.set(true);
    } catch {
      this.reportCopied.set(false);
    }
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

  repoUrl(repo: ScanRepo): string {
    return flowRepoUrl(repo);
  }

  envTagHref(repo: ScanRepo, prefix: string): string {
    const tag = this.envTag(repo, prefix);
    if (!tag) return this.repoUrl(repo);
    const deploy = repo.deploys[prefix];
    if (deploy?.url) {
      return deploy.url;
    }
    if (repo.ci_project) {
      const vcs = repo.ci_vcs || 'bb';
      return `https://app.circleci.com/pipelines/${vcs}/${repo.workspace}?useNewPipelines=true&project=${repo.ci_project}&filter=${encodeURIComponent(`git_tag:equals:${tag}`)}`;
    }
    return `${this.repoUrl(repo)}/src/${encodeURIComponent(tag)}`;
  }

  envTagTitle(repo: ScanRepo, prefix: string): string {
    const tag = this.envTag(repo, prefix);
    const deploy = repo.deploys[prefix];
    if (!deploy) {
      return `${tag} · sin deploy validado`;
    }
    const bits = [tag, deploy.workflow];
    if (deploy.job) bits.push(deploy.job);
    bits.push(deploy.status);
    return bits.join(' · ');
  }

  envTag(repo: ScanRepo, prefix: string): string | null {
    return repo.match_tag?.[prefix.toLowerCase()] ?? null;
  }

  envTagBadge(repo: ScanRepo, prefix: string): { label: string; cls: string } | null {
    const tag = this.envTag(repo, prefix);
    if (!tag) return null;
    const deploy = repo.deploys?.[prefix.toLowerCase()];
    if (!deploy) {
      return { label: `${tag} · pendiente de aprobación`, cls: 'bb-deploy--pending' };
    }
    const s = deploy.status;
    if (s === 'success') {
      return { label: `${tag} · ok`, cls: 'bb-deploy--ok' };
    }
    if (s === 'failed' || s === 'error') {
      return { label: `${tag} · falló`, cls: 'bb-deploy--danger' };
    }
    if (s === 'on_hold') {
      return { label: `${tag} · esperando aprobación`, cls: 'bb-deploy--pending' };
    }
    if (s === 'blocked' || s === 'canceled') {
      return { label: `${tag} · ${s}`, cls: 'bb-deploy--pending' };
    }
    if (s === 'running' || s === 'queued' || s === 'not_run') {
      return { label: `${tag} · ${s}`, cls: 'bb-deploy--pending' };
    }
    return { label: `${tag} · ${s}`, cls: 'bb-deploy--pending' };
  }

  isEnvTag(repo: ScanRepo, name: string): boolean {
    const n = name.toLowerCase();
    return this.prefixes().some((p) => new RegExp(`^${p.toLowerCase()}-\\d+$`).test(n));
  }

  missingTagFor(repo: ScanRepo): boolean {
    return this.prefixes().some((p) => !this.envTag(repo, p));
  }

  missingEnvs(repo: ScanRepo): string[] {
    return this.prefixes().filter((p) => !this.envTag(repo, p));
  }

  reposNeedingTags(): ScanRepo[] {
    if (!this.ciConfigured()) return [];
    return this.repos().filter((r) => this.missingEnvs(r).length > 0);
  }

  missingTooltip(): string {
    return this.reposNeedingTags()
      .map((r) => `${r.slug} → ${this.missingEnvs(r).join(', ')}`)
      .join('\n');
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