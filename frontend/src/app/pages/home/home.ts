import { Component, inject, signal, effect, OnInit } from '@angular/core';
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
  behind: number;
  no_changes?: boolean;
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
  synced: number;
  prod: number;
}

interface SsmParam {
  param: string;
  arn: string;
  tipo: 'nuevo' | 'reutilizado' | string;
  qa_value: string | null;
  repos: string[];
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
  params = signal<SsmParam[]>([]);
  removed = signal<RemovedParam[]>([]);
  scanMode = 'diff';
  stats = signal<ScanStats | null>(null);
  addingPrefix = signal(false);
  ciConfigured = signal(true);
  ciError = signal<string | null>(null);
  storedCreds = signal(false);
  askDisconnect = signal(false);
  syncingPrs = signal(false);

  reposLoading = signal(false);
  paramsLoading = signal(false);
  tableLoaded = signal(false);
  paramsLoaded = signal(false);

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
      forceCache: this.forceCache(),
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
    this.forceCache.set(session.forceCache);
    this.currentSessionId.set(session.id);
    this.loadRepos();
  }

  reuseSession(onSuccess?: () => void) {
    this.loading.set(true);
    this.error.set(null);
    this.http.post<any>('/api/session/reuse', {}).subscribe({
      next: (r) => {
        if (r.ok) {
          this.connected.set(true);
          this.identity.set(r.identity ?? '');
          this.repoCount.set(r.repo_count ?? 0);
          this.storedCreds.set(false);
          if (onSuccess) {
            onSuccess();
          }
        } else {
          this.error.set(r.error ?? 'Error al reutilizar la sesión.');
          this.storedCreds.set(false);
        }
      },
      error: (e) => {
        this.error.set(e.error?.error ?? 'Las credenciales guardadas dejaron de funcionar. Generá de nuevo.');
        this.storedCreds.set(false);
      },
      complete: () => this.loading.set(false),
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
    if (this.circleciToken) {
      body['circleci_token'] = this.circleciToken;
    }
    this.http.post<any>('/api/session', body).subscribe({
        next: (r) => {
          if (r.ok) {
            this.connected.set(true);
            this.identity.set(r.identity);
            this.repoCount.set(r.repo_count);
            this.storedCreds.set(false);
          } else {
            this.error.set(r.error ?? 'Error de conexión');
          }
        },
        error: () => this.error.set('Error de red al conectar.'),
        complete: () => this.loading.set(false),
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
        this.projects.set([]);
        this.params.set([]);
        this.removed.set([]);
        this.stats.set(null);
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

  loadRepos() {
    if (!this.origin) return;
    this.reposLoading.set(true);
    this.error.set(null);
    this.creatingPr.set(null);
    this.tableLoaded.set(true);
    const startedAt = Date.now();
    const done = () => this.releaseBusy(startedAt, () => this.reposLoading.set(false));
    // Limpiar params al cambiar de rama origen (nueva consulta = nueva cache)
    this.params.set([]);
    this.removed.set([]);
    this.paramsLoaded.set(false);
    const dest = this.projectsDest();
    const prefixes = this.prefixes().join(',');
    const exclude = this.blacklisted().join(',');
    const force = this.forceCache() ? 1 : 0;

    // Endpoint de proyectos (alimenta la blacklist)
    this.http.get<any>(`/api/repos?origin=${encodeURIComponent(this.origin)}&project_prefixes=${encodeURIComponent(this.projectPrefixParam())}&force=${force}&exclude=${encodeURIComponent(exclude)}`).subscribe({
      next: (r) => {
        const items: ScanProject[] = (r.items ?? []).slice();
        items.sort((a, b) => a.slug.localeCompare(b.slug));
        this.projects.set(items);
      },
      error: () => this.error.set('Error al obtener los proyectos.'),
    });

    // Endpoint de la tabla de repos
    const base = `/api/scan?origin=${encodeURIComponent(this.origin)}&destination=${encodeURIComponent(dest)}&prefixes=${encodeURIComponent(prefixes)}&project_prefixes=${encodeURIComponent(this.projectPrefixParam())}&exclude=${encodeURIComponent(exclude)}&force=${force}`;
    this.http.get<any>(base).subscribe({
      next: (r) => {
        this.ciConfigured.set(r.ci_configured ?? true);
        this.ciError.set(r.ci_error ?? null);
        this.stats.set(r.stats ?? null);
        this.repos.set(r.repos ?? []);
        if (!r.repos?.length) {
          this.error.set(r.error ?? `Ningún repo contiene la rama '${this.origin}'.`);
        }
      },
      error: () => {
        this.error.set('Error al cargar la tabla.');
        done();
      },
      complete: () => {
        done();
        // Auto-guardar sesión tras carga exitosa
        this.saveCurrentSession();
      },
    });
  }

  loadParams() {
    if (!this.origin) return;
    this.paramsLoading.set(true);
    this.error.set(null);
    this.paramsLoaded.set(true);
    const startedAt = Date.now();
    const done = () => this.releaseBusy(startedAt, () => this.paramsLoading.set(false));
    const dest = this.projectsDest();
    const exclude = this.blacklisted().join(',');
    const force = this.forceCache() ? 1 : 0;
    this.http.get<DiffResponse>(`/api/diff?origin=${encodeURIComponent(this.origin)}&destination=${encodeURIComponent(dest)}&mode=${this.scanMode}&project_prefixes=${encodeURIComponent(this.projectPrefixParam())}&exclude=${encodeURIComponent(exclude)}&force=${force}`)
      .subscribe({
        next: (r) => {
          this.params.set(r.params ?? []);
          this.removed.set(r.removed ?? []);
        },
        error: () => {
          this.error.set('Error al obtener parámetros SSM.');
          done();
        },
        complete: () => done(),
      });
  }

  // Alias: tras crear/actualizar PRs o tags se recarga la tabla (no encadena params).
  resolve() {
    this.loadRepos();
  }

  paramRows(): { param: string; estado: string }[] {
    const rows: { param: string; estado: string }[] = [];
    for (const p of this.filteredParams()) {
      if (!this.repoInProjects(p.repos)) continue;
      rows.push({ param: p.param, estado: p.tipo });
    }
    for (const p of this.removed()) {
      if (!this.repoInProjects(p.repos)) continue;
      const estado = 'solo destino';
      if (!this.estadoFilterActive(estado)) continue;
      rows.push({ param: p.param, estado });
    }
    return rows.sort((a, b) => a.param.localeCompare(b.param));
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
      return this.filteredParams().map((p) => p.param).join('\n');
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

  syncInfo(behind: number): { label: string; cls: string } {
    if (behind <= 0) return { label: 'al día', cls: 'bb-sync--ok' };
    if (behind <= 4) return { label: `${behind} atrás`, cls: 'bb-sync--warn' };
    return { label: `${behind} atrás`, cls: 'bb-sync--danger' };
  }

  syncDot(cls: string): string {
    if (cls === 'bb-sync--warn') return 'bb-dot--pending';
    if (cls === 'bb-sync--danger') return 'bb-dot--fail';
    return 'bb-dot--ok';
  }

  repoUrl(repo: ScanRepo): string {
    return `https://bitbucket.org/${repo.workspace}/${repo.slug}`;
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
      return { label: `${tag} · pendiente`, cls: 'bb-deploy--pending' };
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
          const created = r.items?.flatMap((i: any) => i.created) ?? [];
          const skipped = r.items?.flatMap((i: any) => i.skipped) ?? [];
          const errors = r.items?.flatMap((i: any) => i.errors) ?? [];
          const msg =
            `Tags creados: ${created.join(', ') || 'ninguno'}` +
            (skipped.length ? ` | ya existían: ${skipped.join(', ')}` : '') +
            (errors.length ? ` | errores: ${errors.join('; ')}` : '');
          this.error.set(msg);
          this.resolve();
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