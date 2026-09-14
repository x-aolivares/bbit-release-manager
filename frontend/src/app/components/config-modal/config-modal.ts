import { Component, computed, effect, inject, input, model, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { HttpClient } from '@angular/common/http';

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
  selector: 'app-config-modal',
  templateUrl: './config-modal.html',
  styleUrl: './config-modal.scss',
  standalone: true,
  imports: [FormsModule],
})
export class ConfigModalComponent {
  private http = inject(HttpClient);

  open = input(false);
  clientAlias = model('');
  gitClonesDir = model('');
  gitClonesEnabled = model(false);

  close = output<void>();
  bitbucketReauth = output<void>();

  services = signal<Record<string, ServiceState>>({});
  savingService = signal<string | null>(null);
  configMessage = signal<{ kind: 'ok' | 'error'; text: string } | null>(null);
  gitCloning = signal(false);
  gitCloneStatus = signal<string | null>(null);
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

  constructor() {
    // Al abrir el modal se refrescan client + ambientes (equivale al openConfig).
    effect(() => {
      if (this.open()) {
        this.configMessage.set(null);
        this.refreshClient();
        this.loadAwsEnvironments();
      }
    });
  }

  serviceLabel(svc: string): string {
    return { bitbucket: 'Bitbucket', circleci: 'CircleCI', aws: 'AWS' }[svc] ?? svc;
  }

  readonly awsEnvironmentRows = computed<{ name: string; region: string; localstack: boolean; endpoint_url: string }[]>(
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

          // BBIT-33 Phase 7: Si guardó Bitbucket, destruye sesión antigua e
          // intenta conectar con nueva. La destrucción y reconexión viven en
          // Home (estado global), acá solo se avisa.
          if (service === 'bitbucket') {
            this.bitbucketReauth.emit();
          }
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

    const firstFile = input.files[0];
    const filePath = (firstFile as any).webkitRelativePath || '';

    if (filePath) {
      const folderPath = filePath.split('/').slice(0, -1).join('/');
      if (folderPath) {
        this.gitClonesDir.set(folderPath);
      }
    }

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
}