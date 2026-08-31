import { Component, inject, signal } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { FormsModule } from '@angular/forms';
import { IonHeader } from '@ionic/angular/ion-header';
import { IonToolbar } from '@ionic/angular/ion-toolbar';
import { IonTitle } from '@ionic/angular/ion-title';
import { IonButtons } from '@ionic/angular/ion-buttons';
import { IonContent } from '@ionic/angular/ion-content';
import { IonChip } from '@ionic/angular/ion-chip';
import { IonLabel } from '@ionic/angular/ion-label';
import { IonSpinner } from '@ionic/angular/ion-spinner';
import { IonCard } from '@ionic/angular/ion-card';
import { IonCardHeader } from '@ionic/angular/ion-card-header';
import { IonCardTitle } from '@ionic/angular/ion-card-title';
import { IonCardContent } from '@ionic/angular/ion-card-content';
import { IonInput } from '@ionic/angular/ion-input';
import { IonButton } from '@ionic/angular/ion-button';
import { IonItem } from '@ionic/angular/ion-item';

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
  tags: TagRow[];
  pr: PrInfo;
  deploys: Record<string, DeployInfo | null>;
  match_tag: Record<string, string | null>;
  ci_project?: string | null;
  ci_vcs?: string | null;
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
  repos: string[];
}

interface DiffResponse {
  origin: string;
  destination: string;
  prefixes: string[];
  params: SsmParam[];
}

@Component({
  selector: 'app-home',
  templateUrl: './home.html',
  styleUrl: './home.scss',
  standalone: true,
  imports: [
    FormsModule,
    IonHeader, IonToolbar, IonTitle, IonButtons,
    IonContent, IonChip, IonLabel, IonSpinner,
    IonCard, IonCardHeader, IonCardTitle, IonCardContent,
    IonInput, IonButton, IonItem,
  ],
})
export class Home {
  private http = inject(HttpClient);

  health = signal<Health | null>(null);
  connected = signal(false);
  identity = signal('');
  repoCount = signal(0);
  loading = signal(false);
  error = signal<string | null>(null);

  workspace = 'my_org_web_dev';
  token = '';
  circleciToken = '';
  origin = '';
  destination = 'master';
  prTitle = '';
  prefixInput = '';
  prefixes = signal<string[]>(['uat', 'stgp', 'prod']);

  bbTokenUrl = 'https://id.atlassian.com/manage-profile/security/api-tokens';
  cciTokenUrl = 'https://app.circleci.com/settings/user/tokens';

  repos = signal<ScanRepo[]>([]);
  params = signal<SsmParam[]>([]);
  stats = signal<ScanStats | null>(null);
  addingPrefix = signal(false);
  ciConfigured = signal(true);
  ciError = signal<string | null>(null);
  storedCreds = signal(false);
  askDisconnect = signal(false);
  syncingPrs = signal(false);

  creatingPr = signal<string | null>(null);
  tagging = signal(false);
  taggingRepo = signal<string | null>(null);
  workflowCfg = signal(false);
  workflowCfgRepo = signal<string | null>(null);

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
        } else if (r.stored) {
          this.storedCreds.set(true);
        }
      },
    });
  }

  reuseSession() {
    this.loading.set(true);
    this.error.set(null);
    this.http.post<any>('/api/session/reuse', {}).subscribe({
      next: (r) => {
        if (r.ok) {
          this.connected.set(true);
          this.identity.set(r.identity ?? '');
          this.repoCount.set(r.repo_count ?? 0);
          this.storedCreds.set(false);
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
        this.params.set([]);
        this.stats.set(null);
        this.storedCreds.set(!deleteCredentials);
      },
    });
  }

  resolve() {
    if (!this.origin) return;
    this.loading.set(true);
    this.error.set(null);
    this.params.set([]);
    this.creatingPr.set(null);
    const dest = this.destination || 'master';
    const prefixes = this.prefixes().join(',');
    const base = `/api/scan?origin=${encodeURIComponent(this.origin)}&destination=${encodeURIComponent(dest)}&prefixes=${encodeURIComponent(prefixes)}`;

    this.http.get<any>(base).subscribe({
      next: (r) => {
        this.ciConfigured.set(r.ci_configured ?? true);
        this.ciError.set(r.ci_error ?? null);
        this.stats.set(r.stats ?? null);
        this.repos.set(r.repos ?? []);
        if (!r.repos?.length) {
          this.error.set(r.error ?? `Ningún repo contiene la rama '${this.origin}'.`);
        } else {
          this.loadParams(dest);
        }
      },
      error: () => this.error.set('Error al resolver repos.'),
      complete: () => this.loading.set(false),
    });
  }

  private loadParams(dest: string) {
    this.http.get<DiffResponse>(`/api/diff?origin=${encodeURIComponent(this.origin)}&destination=${encodeURIComponent(dest)}`)
      .subscribe({
        next: (r) => this.params.set(r.params ?? []),
        error: () => this.error.set('Error al obtener parámetros nuevos.'),
      });
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
    this.http.post<any>(`/api/prs/${action}?origin=${encodeURIComponent(this.origin)}&destination=${encodeURIComponent(dest)}&title=${title}`, {})
      .subscribe({
        next: (r) => {
          if (r.ok) {
            const msg = target === 'create'
              ? `PRs creados: ${r.created?.join(', ') || 'ninguno'}` + (r.failed?.length ? ` | fallaron: ${r.failed.map((f: any) => f.repo).join(', ')}` : '')
              : `PRs actualizados: ${r.updated?.join(', ') || 'ninguno'}` + (r.failed?.length ? ` | fallaron: ${r.failed.map((f: any) => f.repo).join(', ')}` : '');
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
    if (repo.ci_project) {
      const vcs = repo.ci_vcs || 'bb';
      return `https://app.circleci.com/pipelines/${vcs}/${repo.workspace}?useNewPipelines=true&project=${repo.ci_project}&filter=${encodeURIComponent(`git_tag:equals:${tag}`)}`;
    }
    return `${this.repoUrl(repo)}/src/${encodeURIComponent(tag)}`;
  }

  envTagTitle(repo: ScanRepo, prefix: string): string {
    const tag = this.envTag(repo, prefix);
    const deploy = repo.deploys[prefix];
    return deploy
      ? `${tag} · ${deploy.workflow} · ${deploy.status}`
      : `${tag} · sin deploy validado`;
  }

  envTag(repo: ScanRepo, prefix: string): string | null {
    return repo.match_tag?.[prefix.toLowerCase()] ?? null;
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
    let url = `/api/tags?origin=${encodeURIComponent(this.origin)}&destination=${encodeURIComponent(dest)}&prefixes=${encodeURIComponent(prefixes)}`;
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

  generateWorkflows(repo?: ScanRepo) {
    if (!this.origin || this.prefixes().length === 0) return;
    const prefixes = this.prefixes().join(',');
    let url = `/api/circleci-config?origin=${encodeURIComponent(this.origin)}&prefixes=${encodeURIComponent(prefixes)}`;
    if (repo) {
      url += `&repo=${encodeURIComponent(repo.slug)}`;
      this.workflowCfgRepo.set(repo.slug);
    } else {
      this.workflowCfg.set(true);
    }
    this.error.set(null);
    this.http.post<any>(url, {}).subscribe({
      next: (r) => {
        if (r.ok) {
          const written = r.items?.filter((i: any) => !i.skipped) ?? [];
          const envs = r.items?.flatMap((i: any) => i.envs) ?? [];
          const errors = r.items?.flatMap((i: any) => i.errors) ?? [];
          const msg =
            `config.yml escritos: ${written.length}` +
            (envs.length ? ` (envs: ${envs.join(', ')})` : '') +
            (errors.length ? ` | errores: ${errors.join('; ')}` : '');
          this.error.set(msg);
        } else {
          this.error.set(r.error ?? 'Error al generar workflows.');
        }
      },
      error: (e) => this.error.set(e.error?.error ?? e.message ?? 'Error al generar workflows.'),
      complete: () => {
        this.workflowCfg.set(false);
        this.workflowCfgRepo.set(null);
      },
    });
  }
}