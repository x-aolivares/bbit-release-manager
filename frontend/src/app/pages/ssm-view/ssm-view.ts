import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router } from '@angular/router';
import { IonContent, IonHeader, IonToolbar, IonIcon } from '@ionic/angular';

interface EnvValue {
  environment: string;
  value: string | null;
  masked: boolean;
  is_secret: boolean;
  source: string;
  secret_arn: string | null;
  updated_at: number;
}

interface UpdateOp {
  op: 'insert' | 'update';
  path: string;
  value: unknown;
  json?: boolean;
}

interface CompareResponse {
  param: string;
  origin_env: string;
  dest_env: string;
  updates: UpdateOp[];
  warning: string | null;
}

interface LiveStatus {
  status: 'ok' | 'missing' | 'unavailable';
  changed: boolean;
  at: number | null;
}

interface PreviewState {
  op: UpdateOp;
  command: string;
  region: string;
  type: string;
  loading: boolean;
  running: boolean;
  copied: boolean;
  done: boolean;
  error: string | null;
}

@Component({
  selector: 'app-ssm-view',
  templateUrl: './ssm-view.html',
  styleUrl: './ssm-view.scss',
  standalone: true,
  imports: [
    FormsModule,
    IonContent, IonHeader, IonToolbar, IonIcon,
  ],
})
export class SsmView implements OnInit {
  private http = inject(HttpClient);
  private route = inject(ActivatedRoute);
  private router = inject(Router);

  param = signal('');
  envs = signal<EnvValue[]>([]);
  drafts = signal<Record<string, string>>({});
  loading = signal(true);
  error = signal<string | null>(null);
  saving = signal<string | null>(null);
  message = signal<{ kind: 'ok' | 'error'; text: string } | null>(null);
  toggling = signal<string | null>(null);

  // Config de ambientes → región (SSM_ENVIRONMENTS)
  regions = signal<Record<string, string>>({});
  readSecrets = signal(false);
  savingRegions = signal(false);

  // Comparador
  comparing = signal(false);
  compareResult = signal<CompareResponse | null>(null);
  checked = signal<string[]>([]);
  query = signal<{ command: string; region: string; type: string } | null>(null);
  queryLoading = signal(false);
  copied = signal(false);

  // Estado de la última lectura en vivo (por ambiente)
  live = signal<Record<string, LiveStatus>>({});
  liveEntries = computed<{ name: string; status: string; changed: boolean }[]>(
    () => Object.entries(this.live())
      .map(([name, s]) => ({ name, status: s.status, changed: s.changed }))
      .sort((a, b) => a.name.localeCompare(b.name)),
  );

  // Modal de previsualizar/ejecutar por línea
  preview = signal<PreviewState | null>(null);

  ngOnInit(): void {
    const raw = this.route.snapshot.paramMap.get('param') ?? '';
    this.param.set(this.decodeParam(raw));
    this.route.paramMap.subscribe((p) => this.param.set(this.decodeParam(p.get('param') ?? '')));
    this.loadEnvironments();
    this.loadValues();
  }

  private decodeParam(raw: string): string {
    try {
      return decodeURIComponent(raw);
    } catch {
      return raw;
    }
  }

  private loadEnvironments(): void {
    this.http.get<any>('/api/ssm/environments').subscribe({
      next: (r) => {
        const envs = r.ssm_environments ?? {};
        this.regions.set({ ...envs });
        this.readSecrets.set(!!r.ssm_read_secrets);
      },
    });
  }

  loadValues(): void {
    this.loading.set(true);
    this.error.set(null);
    this.http.get<any>('/api/ssm/values', { params: { param: this.param() } }).subscribe({
      next: (r) => {
        this.envs.set(r.environments ?? []);
        this.live.set(r.live ?? {});
        const drafts: Record<string, string> = {};
        for (const e of this.envs()) {
          if (!e.masked) drafts[e.environment] = e.value ?? '';
        }
        this.drafts.set(drafts);
      },
      error: (e) => this.error.set(e.error?.detail ?? e.error?.error ?? 'No se pudieron cargar los valores.'),
      complete: () => this.loading.set(false),
    });
  }

  /** "Leer en vivo": re-consulta SSM/LocalStack para cada ambiente y muestra
   *  qué leyó y qué cambió respecto a lo guardado (nunca miente ni pisa callado). */
  refresh(): void {
    this.loading.set(true);
    this.error.set(null);
    this.message.set(null);
    this.http.get<any>('/api/ssm/values', { params: { param: this.param(), fetch: '1' } }).subscribe({
      next: (r) => {
        this.envs.set(r.environments ?? []);
        this.live.set(r.live ?? {});
        const drafts: Record<string, string> = {};
        for (const e of this.envs()) {
          if (!e.masked) drafts[e.environment] = e.value ?? '';
        }
        this.drafts.set(drafts);
        this.resetDiff();
        this.message.set({ kind: 'ok', text: this.liveSummary(r.live ?? {}) });
      },
      error: (e) => this.error.set(e.error?.detail ?? e.error?.error ?? 'No se pudieron actualizar.'),
      complete: () => this.loading.set(false),
    });
  }

  private liveSummary(live: Record<string, LiveStatus>): string {
    const entries = Object.entries(live);
    if (entries.length === 0) return 'Sin ambientes configurados para leer en vivo.';
    const parts = entries.map(([name, s]) => {
      if (s.status === 'ok') return s.changed ? `${name}: cambió` : `${name}: sin cambios`;
      if (s.status === 'unavailable') return `${name}: sin credenciales`;
      return `${name}: no existe en SSM`;
    });
    return `En vivo · ${parts.join(' · ')}`;
  }

  liveTitle(entry: { name: string; status: string; changed: boolean }): string {
    if (entry.status === 'ok') {
      return entry.changed
        ? `${entry.name}: valor distinto al guardado (actualizado en vivo)`
        : `${entry.name}: mismo valor que el guardado`;
    }
    if (entry.status === 'unavailable') return `${entry.name}: sin credenciales AWS para leer`;
    return `${entry.name}: el parámetro no existe en SSM/LocalStack`;
  }

  // -- valores por ambiente ---------------------------------------------------

  envMasked(env: string): boolean {
    return !!this.envs().find((e) => e.environment === env)?.masked;
  }

  envSecret(env: string): boolean {
    return !!this.envs().find((e) => e.environment === env)?.is_secret;
  }

  setDraft(env: string, value: string): void {
    this.drafts.update((d) => ({ ...d, [env]: value }));
    this.resetDiff();
  }

  /** Descarta el panel de diferencias/query cuando cambia el dato que lo generó:
   *  nunca mostrar un diff viejo contra lo que ya se guardó o editó. */
  private resetDiff(): void {
    this.compareResult.set(null);
    this.checked.set([]);
    this.query.set(null);
    this.preview.set(null);
  }

  saveEnv(env: string, isSecret: boolean): void {
    this.saving.set(env);
    this.message.set(null);
    const body = {
      param: this.param(),
      environment: env,
      value: this.drafts()[env] ?? '',
      is_secret: isSecret,
      source: isSecret ? 'secretsmanager' : 'ssm',
      secret_arn: null,
    };
    this.http.patch<any>('/api/ssm/values', body).subscribe({
      next: () => {
        this.message.set({ kind: 'ok', text: `${env}: valor guardado.` });
        this.resetDiff();
        this.loadValues();
      },
      error: (e) => this.message.set({
        kind: 'error',
        text: e.error?.detail ?? e.error?.error ?? 'No se pudo guardar.',
      }),
      complete: () => this.saving.set(null),
    });
  }

  switchEnv(cardIndex: number, newEnv: string): void {
    if (!newEnv || this.envs()[cardIndex]?.environment === newEnv) return;
    const dup = this.envs().some((e, i) => i !== cardIndex && e.environment === newEnv);
    if (dup) {
      this.message.set({ kind: 'error', text: `${newEnv} ya está visible en otra card.` });
      return;
    }
    this.http.get<any>('/api/ssm/values', {
      params: { param: this.param(), fetch: '1' },
    }).subscribe({
      next: (r) => {
        const found = (r.environments ?? []).find((e: any) => e.environment === newEnv);
        const envData: EnvValue = found ?? {
          environment: newEnv, value: null, masked: false,
          is_secret: false, source: 'ssm', secret_arn: null, updated_at: 0,
        };
        this.envs.update(envs => {
          const updated = [...envs];
          updated[cardIndex] = envData;
          return updated;
        });
        if (!envData.masked) {
          this.drafts.update(d => ({ ...d, [newEnv]: envData.value ?? '' }));
        }
      },
    });
  }

  addCard(envName: string): void {
    const env = (envName || '').trim();
    if (!env) return;
    if (this.envs().some(e => e.environment === env)) {
      this.message.set({ kind: 'error', text: `${env} ya está visible.` });
      return;
    }
    this.http.get<any>('/api/ssm/values', {
      params: { param: this.param(), fetch: '1' },
    }).subscribe({
      next: (r) => {
        const found = (r.environments ?? []).find((e: any) => e.environment === env);
        const envData: EnvValue = found ?? {
          environment: env, value: null, masked: false,
          is_secret: false, source: 'ssm', secret_arn: null, updated_at: 0,
        };
        this.envs.update(envs => [...envs, envData]);
        if (!envData.masked) {
          this.drafts.update(d => ({ ...d, [env]: envData.value ?? '' }));
        }
      },
    });
  }

  toggleSecret(env: string, checked: boolean): void {
    this.toggling.set(env);
    this.message.set(null);
    this.http.patch<any>('/api/ssm/values', {
      param: this.param(),
      environment: env,
      is_secret: checked,
    }).subscribe({
      next: () => {
        this.message.set({
          kind: 'ok',
          text: checked
            ? `${env}: marcado como secreto (valor preservado).`
            : `${env}: desmarcado como secreto (valor preservado).`,
        });
        this.resetDiff();
        this.loadValues();
      },
      error: (e) => this.message.set({
        kind: 'error',
        text: e.error?.detail ?? e.error?.error ?? 'No se pudo actualizar el secreto.',
      }),
      complete: () => this.toggling.set(null),
    });
  }

  // -- regiones ---------------------------------------------------------------

  regionEntries = computed<{ name: string; region: string }[]>(
    () => Object.entries(this.regions())
      .map(([name, region]) => ({ name, region }))
      .sort((a, b) => a.name.localeCompare(b.name)),
  );

  setRegion(env: string, value: string): void {
    this.regions.update((m) => ({ ...m, [env]: value }));
  }

  addRegion(name: string, region: string): void {
    const env = (name || '').trim();
    if (!env) return;
    this.setRegion(env, (region || '').trim());
  }

  saveRegions(): void {
    this.savingRegions.set(true);
    this.message.set(null);
    const ssm_environments: Record<string, string> = {};
    for (const entry of this.regionEntries()) {
      const region = entry.region.trim();
      if (region) ssm_environments[entry.name] = region;
    }
    this.http.patch<any>('/api/ssm/environments', { ssm_environments, ssm_read_secrets: this.readSecrets() })
      .subscribe({
        next: () => {
          this.message.set({ kind: 'ok', text: 'Config de ambientes guardada.' });
          this.loadEnvironments();
        },
        error: (e) => this.message.set({
          kind: 'error',
          text: e.error?.detail ?? e.error?.error ?? 'No se pudo guardar la config.',
        }),
        complete: () => this.savingRegions.set(false),
      });
  }

  // -- comparador -------------------------------------------------------------

  compare(): void {
    const envs = this.envs();
    if (envs.length < 2) {
      this.message.set({ kind: 'error', text: 'Necesitás al menos 2 ambientes para comparar.' });
      return;
    }
    const origin = envs[0].environment;
    const dest = envs[1].environment;
    this.comparing.set(true);
    this.error.set(null);
    this.compareResult.set(null);
    this.query.set(null);
    this.checked.set([]);
    this.http.post<CompareResponse>('/api/ssm/compare', {
      param: this.param(),
      origin_env: origin,
      dest_env: dest,
    }).subscribe({
      next: (r) => {
        r.updates ??= [];
        this.compareResult.set(r);
        this.checked.set(r.updates.map((u) => u.path));
      },
      error: (e) => this.message.set({
        kind: 'error',
        text: e.error?.detail ?? e.error?.error ?? 'No se pudo comparar.',
      }),
      complete: () => this.comparing.set(false),
    });
  }

  toggleOp(path: string): void {
    const cur = this.checked();
    this.checked.set(cur.includes(path) ? cur.filter((c) => c !== path) : [...cur, path]);
  }

  opChecked(path: string): boolean {
    return this.checked().includes(path);
  }

  opLabel(op: UpdateOp): string {
    return op.op === 'insert' ? 'insertar' : 'reemplazar';
  }

  stringify(value: unknown): string {
    if (typeof value === 'string') return value;
    try {
      return JSON.stringify(value);
    } catch {
      return String(value);
    }
  }

  isJson(value: string | null | undefined): boolean {
    if (!value) return false;
    const t = value.trim();
    if (!t) return false;
    return (t.startsWith('{') && t.endsWith('}')) || (t.startsWith('[') && t.endsWith(']'));
  }

  isAnyJson(): boolean {
    return this.envs().some(e => !e.masked && this.isJson(this.drafts()[e.environment]));
  }

  exportQuery(): void {
    const c = this.compareResult();
    const env = c?.dest_env ?? this.envs()[1]?.environment ?? '';
    const origin = c?.origin_env ?? this.envs()[0]?.environment ?? '';
    if (!env) {
      this.message.set({ kind: 'error', text: 'Falta el ambiente destino: elegí el origen y el destino antes de exportar.' });
      return;
    }
    this.queryLoading.set(true);
    this.query.set(null);
    this.copied.set(false);
    this.http.get<any>('/api/ssm/update-query', {
      params: {
        param: this.param(),
        env,
        origin,
        changes: this.checked().join(','),
      },
    }).subscribe({
      next: (r) => this.query.set({ command: r.command, region: r.region, type: r.type }),
      error: (e) => this.message.set({
        kind: 'error',
        text: e.error?.detail ?? e.error?.error ?? 'No se pudo generar el query.',
      }),
      complete: () => this.queryLoading.set(false),
    });
  }

  async copyQuery(): Promise<void> {
    const q = this.query();
    if (!q) return;
    try {
      await navigator.clipboard.writeText(q.command);
      this.copied.set(true);
    } catch {
      this.copied.set(false);
    }
  }

  goBack(): void {
    this.router.navigate(['/']);
  }

  // -- modal previsualizar / ejecutar por línea ------------------------------

  openPreview(op: UpdateOp): void {
    const c = this.compareResult();
    if (!c) return;
    this.preview.set({
      op, command: '', region: '', type: '',
      loading: true, running: false, copied: false, done: false, error: null,
    });
    this.http.get<any>('/api/ssm/update-query', {
      params: {
        param: this.param(),
        env: c.dest_env,
        origin: c.origin_env,
        changes: op.path,
      },
    }).subscribe({
      next: (r) => this.preview.update(p => p && ({
        ...p, command: r.command, region: r.region, type: r.type, loading: false,
      })),
      error: (e) => this.preview.update(p => p && ({
        ...p, loading: false, error: e.error?.detail ?? 'No se pudo generar el query.',
      })),
    });
  }

  closePreview(): void {
    this.preview.set(null);
  }

  async copyPreview(): Promise<void> {
    const p = this.preview();
    if (!p || !p.command) return;
    try {
      await navigator.clipboard.writeText(p.command);
      this.preview.update(pv => pv && ({ ...pv, copied: true }));
    } catch {
      this.preview.update(pv => pv && ({ ...pv, copied: false }));
    }
  }

  runPreview(): void {
    const p = this.preview();
    const c = this.compareResult();
    if (!p || !c || !p.command || p.running) return;
    this.preview.update(pv => pv && ({ ...pv, running: true, error: null, done: false }));
    this.http.post<any>('/api/ssm/apply', {
      param: this.param(),
      env: c.dest_env,
      origin: c.origin_env,
      changes: p.op.path,
    }).subscribe({
      next: (r) => {
        this.preview.update(pv => pv && ({ ...pv, running: false, done: true, command: r.command }));
        this.message.set({ kind: 'ok', text: `${c.dest_env}: query ejecutada (${p.op.path}).` });
        this.refresh();
      },
      error: (e) => this.preview.update(pv => pv && ({
        ...pv, running: false, error: e.error?.detail ?? 'No se pudo ejecutar la query.',
      })),
    });
  }
}