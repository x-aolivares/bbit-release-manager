import { Component, OnInit, OnDestroy, computed, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { BB_CONFIG, MOCK_CACHED_PRS, MOCK_REPO_SOURCES, MOCK_REGIONS, MOCK_TAGS_SEED, prFor, ssmParamsFor, tagFor, MOCK_FLOW_PR_BY_SHORT, MOCK_FLOW_TAGS_BY_SHORT, FLOW_ENVS } from './mock-data';
import type { MockedPr, MockedSsmParam } from './mock-data';

type RepoState = 'pending' | 'checking' | 'found' | 'not_found';

interface RepoRow {
  slug: string;
  name: string;
  project: string;
  branch_state: 'found' | 'not_found';
  resolved_branch: string;
  tags: string[];
  pr: MockedPr | null;
}

interface FlowEntry {
  slug: string;
  pr: MockedPr | null;
  tags: Record<string, string | null>;
}

interface ModalOption {
  action: string;
  title: string;
  desc: string;
  hint: string;
  count: string;
}

function flowDefault(slug: string): FlowEntry {
  return { slug, pr: null, tags: {} };
}

@Component({
  selector: 'app-repos-rediseno',
  templateUrl: './repos-rediseno.html',
  styleUrl: './repos-rediseno.scss',
  standalone: true,
  imports: [FormsModule],
})
export class ReposRediseno implements OnInit, OnDestroy {
  readonly workspace = BB_CONFIG.workspace;
  readonly totalRepos = BB_CONFIG.total_repos;
  readonly deployEnvs = BB_CONFIG.deploy_envs;

  ngOnInit(): void {
    this.onBuscar();
  }

  // ---- buscador -----------------------------------------------------------
  prefixes = signal<string[]>(BB_CONFIG.default_filters.project_prefixes.slice());
  blacklist = signal<string[]>(BB_CONFIG.default_filters.exclude.slice());
  origin = signal<string>(BB_CONFIG.default_branches.origin);
  destination = signal<string>(BB_CONFIG.default_branches.destination);

  // ---- listado ------------------------------------------------------------
  tableVisible = signal(false);
  repos = signal<RepoRow[]>([]);
  states = signal<Record<string, RepoState>>({});
  removed = signal<string[]>([]);
  creating = signal<Record<string, boolean>>({});
  selected = signal<Record<string, boolean>>({});
  search = signal('');
  tagFilters = signal<string[]>([]);
  page = signal(1);
  pageSize = signal(10);
  prActive = signal(false);
  branchSummaryVisible = signal(false);
  branchBusy = signal(false);
  verRamaDone = signal(false);

  // ---- flujo por repo (pr + tags de deploy) -------------------------------
  flowBySlug = signal<Record<string, FlowEntry>>({});

  // ---- acciones masivas / modal -------------------------------------------
  actionsOpen = signal(false);
  flowStep = signal(false);
  flowTagChecks = signal<Record<string, boolean>>({});
  newTag = signal('');
  newTagError = signal('');

  // ---- modal crear PR individual -------------------------------------------
  prOpen = signal(false);
  prModalSlug = signal<string | null>(null);
  prModalTitle = signal('');

  // ---- detalle --------------------------------------------------------------
  detailSlug = signal<string | null>(null);
  detailPrTitle = signal('');
  paramsBySlug = signal<Record<string, MockedSsmParam[]>>({});
  regions = signal<string[]>([]);
  selectedRegions = signal<Record<string, boolean>>({});
  generating = signal<Record<string, boolean>>({});

  // ---- excel ----------------------------------------------------------------
  excelPct = signal<number | null>(null);
  excelStatusText = signal('');
  private excelTimer: ReturnType<typeof setInterval> | null = null;

  private timers = new Set<ReturnType<typeof setTimeout>>();

  private schedule(fn: () => void, ms: number): void {
    const id = setTimeout(() => {
      this.timers.delete(id);
      fn();
    }, ms);
    this.timers.add(id);
  }

  ngOnDestroy(): void {
    if (this.excelTimer) clearInterval(this.excelTimer);
    this.timers.forEach((t) => clearTimeout(t));
    this.timers.clear();
  }

  private shortSlug(slug: string): string {
    return slug.split('-').slice(2).join('-');
  }

  private tagKey(slug: string, env: string): string {
    return slug + '|tag:' + env;
  }

  // ===================== BUSCADOR =====================

  addChip(kind: 'prefix' | 'blacklist'): void {
    const promptMsg = kind === 'prefix'
      ? 'Nuevo prefijo de proyecto (ej: bbit-orders-):'
      : 'Slug a excluir (ej: bbit-trnxd-backend-qa):';
    const val = window.prompt(promptMsg);
    const v = (val || '').trim();
    if (!v) return;
    if (kind === 'prefix') {
      if (this.prefixes().includes(v)) { window.alert('Ya existe en la lista.'); return; }
      this.prefixes.update((l) => [...l, v]);
    } else {
      if (this.blacklist().includes(v)) { window.alert('Ya existe en la lista.'); return; }
      this.blacklist.update((l) => [...l, v]);
    }
  }

  removeChip(kind: 'prefix' | 'blacklist', index: number): void {
    if (kind === 'prefix') this.prefixes.update((l) => l.filter((_, i) => i !== index));
    else this.blacklist.update((l) => l.filter((_, i) => i !== index));
  }

  onBuscar(): void {
    // Replica de GET /api/repos-quick + GET /api/flow + merge de PRs cacheados.
    const prefixes = this.prefixes();
    const exclude = new Set(this.blacklist());
    const origin = this.origin().trim();

    const sources = MOCK_REPO_SOURCES
      .filter((r) => !exclude.has(r.slug))
      .filter((r) => !prefixes.length || prefixes.some((p) => r.slug.startsWith(p)))
      .sort((a, b) => a.slug.localeCompare(b.slug));

    const flow: Record<string, FlowEntry> = {};
    for (const slug of Object.keys({ ...MOCK_FLOW_PR_BY_SHORT, ...MOCK_FLOW_TAGS_BY_SHORT })) {
      const tags: Record<string, string | null> = {};
      for (const env of FLOW_ENVS) tags[env] = (MOCK_FLOW_TAGS_BY_SHORT[slug] ?? {})[env] ?? null;
      const entry = MOCK_FLOW_PR_BY_SHORT[slug];
      flow[slug] = { slug, pr: entry ? { ...entry } : null, tags };
    }

    const rows: RepoRow[] = sources.map((r) => {
      const tags = MOCK_TAGS_SEED[r.slug]?.slice() ?? [];
      const cached = MOCK_CACHED_PRS[r.slug];
      const pr = cached ? { ...cached } : null;
      return {
        slug: r.slug,
        name: r.name,
        project: r.project ?? '',
        branch_state: r.branch_state,
        resolved_branch: r.branch_state === 'found' ? origin : '',
        tags,
        pr,
      };
    });

    // PR cacheado de `repositories` (TTL 24h) se muestra ya en la primera consulta.
    const state = { ...flow };
    for (const r of rows) {
      if (r.pr) {
        if (!state[r.slug]) state[r.slug] = flowDefault(r.slug);
        state[r.slug].pr = r.pr;
      }
    }

    const states: Record<string, RepoState> = {};
    rows.forEach((r) => { states[r.slug] = 'pending'; });

    this.repos.set(rows);
    this.flowBySlug.set(state);
    this.states.set(states);
    this.removed.set([]);
    this.search.set('');
    this.tagFilters.set([]);
    this.page.set(1);
    this.branchSummaryVisible.set(false);
    this.branchBusy.set(false);
    this.verRamaDone.set(false);
    this.tableVisible.set(true);
    this.schedule(() => {
      const el = document.getElementById('tablePanel');
      if (el) window.scrollTo({ top: el.offsetTop - 20, behavior: 'smooth' });
    }, 50);
  }

  // ===================== PROCESAR SALIDA =====================

  onToggleBranch(on: boolean): void {
    this.prActive.set(on);
    this.branchSummaryVisible.set(false);
    if (!on) {
      this.removed.set([]);
      const states: Record<string, RepoState> = {};
      this.repos().forEach((r) => { states[r.slug] = 'pending'; });
      this.states.set(states);
      this.verRamaDone.set(false);
      this.branchBusy.set(false);
    }
  }

  onVerRama(): void {
    if (this.branchBusy()) return;
    const repos = this.repos();
    if (!repos.length) return;
    this.branchBusy.set(true);
    this.verRamaDone.set(false);
    this.removed.set([]);
    this.branchSummaryVisible.set(true);

    const states: Record<string, RepoState> = {};
    repos.forEach((r) => { states[r.slug] = 'checking'; });
    this.states.set(states);

    repos.forEach((repo, i) => {
      this.schedule(() => {
        const found = repo.branch_state === 'found';
        this.states.update((m) => ({ ...m, [repo.slug]: found ? 'found' : 'not_found' }));
        if (!found) {
          this.removed.update((list) => [...list, repo.slug]);
          this.selected.update((m) => {
            const n = { ...m };
            delete n[repo.slug];
            return n;
          });
        }
        if (repos.every((r) => this.states()[r.slug] !== 'checking')) {
          this.branchBusy.set(false);
          this.verRamaDone.set(true);
        }
      }, 300 + i * 120);
    });
  }

  private createPr(slug: string, title: string | null): void {
    if (this.creating()[slug]) return;
    this.creating.update((m) => ({ ...m, [slug]: true }));
    const origin = this.origin().trim();
    const dest = this.destination().trim() || 'master';
    const t = (title ?? '').trim() || `Release: ${origin} → ${dest}`;
    this.schedule(() => {
      const pr = prFor(slug, t);
      this.flowBySlug.update((f) => {
        const e = f[slug] ?? flowDefault(slug);
        return { ...f, [slug]: { ...e, pr } };
      });
      this.repos.update((list) => list.map((r) => (r.slug === slug ? { ...r, pr } : r)));
      this.creating.update((m) => {
        const n = { ...m };
        delete n[slug];
        return n;
      });
    }, 350);
  }

  // ===================== MODAL CREAR PR INDIVIDUAL =====================

  openPrModal(slug: string | null): void {
    if (!slug) return;
    this.prModalSlug.set(slug);
    const origin = this.origin().trim();
    const dest = this.destination().trim() || 'master';
    this.prModalTitle.set(`Release: ${origin} → ${dest}`);
    this.prOpen.set(true);
  }

  closePrModal(): void {
    this.prOpen.set(false);
    this.prModalSlug.set(null);
  }

  onPrConfirm(): void {
    const slug = this.prModalSlug();
    if (!slug) return;
    const title = this.prModalTitle().trim();
    this.closePrModal();
    this.createPr(slug, title);
  }

  prModalRoute(): string {
    const slug = this.prModalSlug();
    if (!slug) return '';
    const origin = this.origin().trim();
    const dest = this.destination().trim() || 'master';
    return `POST /api/pr?repo=${slug}&origin=${origin}&destination=${dest}`;
  }

  prApiHint(slug: string): string {
    const origin = this.origin().trim();
    const dest = this.destination().trim() || 'master';
    const hint = `/api/pr?repo=${slug}&origin=${encodeURIComponent(origin)}&destination=${encodeURIComponent(dest)}&title=${encodeURIComponent(`Release: ${origin} → ${dest}`)}`;
    return `POST ${hint}`;
  }

  destLabel(): string {
    return this.destination().trim() || 'master';
  }

  detailNoPrHint(slug: string | null): string {
    if (!slug) return '';
    const origin = this.origin().trim();
    const dest = this.destination().trim() || 'master';
    return `POST /api/pr?repo=${slug}&origin=${encodeURIComponent(origin)}&destination=${encodeURIComponent(dest)}`;
  }

  // ===================== LISTADO =====================

  readonly statTotal = computed(() => this.repos().length);
  readonly statFound = computed(() => this.repos().filter((r) => this.stateOf(r.slug) === 'found').length);
  readonly statMissing = computed(() => this.statTotal() - this.statFound());

  readonly visibleRepos = computed(() => {
    const rem = new Set(this.removed());
    return this.repos().filter((r) => !rem.has(r.slug));
  });

  readonly filteredRepos = computed(() => {
    const q = this.search().trim().toLowerCase();
    const tags = new Set(this.tagFilters());
    return this.visibleRepos().filter((r) => {
      if (q && !r.slug.toLowerCase().includes(q) && !r.name.toLowerCase().includes(q)) return false;
      if (tags.size && !(r.tags || []).some((t) => tags.has(t))) return false;
      return true;
    });
  });

  readonly availableTags = computed(() => {
    const all = new Set<string>();
    this.visibleRepos().forEach((r) => (r.tags || []).forEach((t) => all.add(t)));
    return [...all].sort();
  });

  readonly pages = computed(() => Math.max(1, Math.ceil(this.filteredRepos().length / this.pageSize())));
  readonly effectivePage = computed(() => Math.min(this.page(), this.pages()));
  readonly totalDigits = computed(() => Math.max(1, String(this.filteredRepos().length).length));
  readonly offset = computed(() => (this.effectivePage() - 1) * this.pageSize());

  padNum(n: number): string {
    return String(n).padStart(this.totalDigits(), '0');
  }

  readonly pageRows = computed(() => {
    const all = this.filteredRepos();
    return all.slice(this.offset(), this.offset() + this.pageSize());
  });

  readonly pagInfo = computed(() => {
    const all = this.filteredRepos();
    const from = all.length ? this.offset() + 1 : 0;
    const to = Math.min(this.offset() + this.pageSize(), all.length);
    const filtrado = this.search().trim() || this.tagFilters().length
      ? ` (filtrado sobre ${this.visibleRepos().length})`
      : '';
    return `Mostrando ${from}–${to} de ${all.length} repositorios${filtrado} · ${this.removed().length} removidos por no tener la rama`;
  });

  readonly pageLabel = computed(() => `${this.effectivePage()} / ${this.pages()}`);

  // ---- columna PR / badges ----

  stateOf(slug: string): RepoState {
    return this.states()[slug] ?? 'pending';
  }

  prOf(slug: string): MockedPr | null {
    return this.flowBySlug()[slug]?.pr ?? null;
  }

  isCreating(slug: string): boolean {
    return !!this.creating()[slug];
  }

  // ---- selección masiva ----

  readonly selectedSlugs = computed(() =>
    Object.keys(this.selected()).filter((k) => this.selected()[k]));

  readonly selCount = computed(() => this.selectedSlugs().length);

  readonly allFilteredSelected = computed(() => {
    const slugs = this.filteredRepos().map((r) => r.slug);
    return slugs.length > 0 && slugs.every((s) => this.selected()[s]);
  });

  readonly pageAllSelected = computed(() => {
    const slugs = this.pageRows().map((r) => r.slug);
    return slugs.length > 0 && slugs.every((s) => this.selected()[s]);
  });

  isSelected(slug: string): boolean {
    return !!this.selected()[slug];
  }

  toggleRepo(slug: string, on: boolean): void {
    this.selected.update((m) => ({ ...m, [slug]: on }));
  }

  togglePage(on: boolean): void {
    const slugs = this.pageRows().map((r) => r.slug);
    this.selected.update((m) => {
      const n = { ...m };
      if (on) slugs.forEach((s) => (n[s] = true));
      else slugs.forEach((s) => delete n[s]);
      return n;
    });
  }

  toggleAll(on: boolean): void {
    const slugs = this.filteredRepos().map((r) => r.slug);
    this.selected.update((m) => {
      if (on) {
        const n: Record<string, boolean> = {};
        slugs.forEach((s) => (n[s] = true));
        return n;
      }
      const n = { ...m };
      slugs.forEach((s) => delete n[s]);
      return n;
    });
  }

  clearSelection(): void {
    this.selected.set({});
  }

  private pruneSelection(): void {
    const visible = new Set(this.filteredRepos().map((r) => r.slug));
    this.selected.update((m) => {
      const n = { ...m };
      for (const k of Object.keys(n)) if (!visible.has(k)) delete n[k];
      return n;
    });
  }

  onSearch(value: string): void {
    this.search.set(value);
    this.page.set(1);
    this.pruneSelection();
  }

  toggleTagFilter(tag: string): void {
    this.tagFilters.update((list) => (list.includes(tag) ? list.filter((t) => t !== tag) : [...list, tag]));
    this.page.set(1);
    this.pruneSelection();
  }

  tagActive(tag: string): boolean {
    return this.tagFilters().includes(tag);
  }

  onPageSize(value: string): void {
    this.pageSize.set(Number(value));
    this.page.set(1);
  }

  prevPage(): void {
    if (this.effectivePage() <= 1) return;
    this.page.set(this.effectivePage() - 1);
  }

  nextPage(): void {
    if (this.effectivePage() >= this.pages()) return;
    this.page.set(this.effectivePage() + 1);
  }

  // ===================== ACCIONES MASIVAS =====================

  readonly readyPr = computed(() =>
    this.selectedSlugs().filter((s) => this.stateOf(s) === 'found').length);

  readonly withFlow = computed(() =>
    this.selectedSlugs().filter((s) => (this.repos().find((r) => r.slug === s)?.tags ?? []).length > 0).length);

  readonly modalOptions = computed<ModalOption[]>(() => {
    const total = this.selectedSlugs().length;
    const ready = this.readyPr();
    const f = this.withFlow();
    return [
      {
        action: 'mass-pr',
        title: 'Crear PRs',
        desc: 'Para los repos con rama resuelta, PR de la rama origen → destino.',
        hint: ready >= total ? '' : `${total - ready} sin rama resuelta`,
        count: `${ready}/${total} aptos`,
      },
      {
        action: 'mass-tags-deploy',
        title: 'Crear tags Circle (deploy)',
        desc: 'Genera el tag de deploy (uat/stgp) para los repos seleccionados.',
        hint: '',
        count: `${ready}/${total} aptos`,
      },
      {
        action: 'mass-tag-flow',
        title: 'Crear tag de repo (flujo)',
        desc: 'Etiqueta repos con su flujo: fargate, step-function, workflow, batch.',
        hint: '',
        count: `${f}/${total} con tags`,
      },
    ];
  });

  openActions(): void {
    this.flowStep.set(false);
    this.flowTagChecks.set({});
    this.newTagError.set('');
    this.actionsOpen.set(true);
  }

  closeActions(): void {
    this.actionsOpen.set(false);
    this.flowStep.set(false);
  }

  onModalOption(action: string): void {
    if (action === 'mass-tag-flow') {
      this.enterFlowStep();
      return;
    }
    if (action === 'mass-pr') {
      for (const slug of this.selectedSlugs()) {
        if (this.stateOf(slug) !== 'found') continue;
        if (this.flowBySlug()[slug]?.pr?.url) continue;
        this.createPr(slug, null);
      }
      this.closeActions();
      return;
    }
    if (action === 'mass-tags-deploy') {
      const origin = this.origin().trim();
      for (const slug of this.selectedSlugs()) {
        if (this.stateOf(slug) !== 'found') continue;
        for (const env of this.deployEnvs) {
          const key = this.tagKey(slug, env);
          if (this.generating()[key] || this.flowBySlug()[slug]?.tags?.[env]) continue;
          this.generating.update((m) => ({ ...m, [key]: true }));
          this.schedule(() => {
            const value = tagFor(slug, origin, env);
            this.flowBySlug.update((f) => {
              const e = f[slug] ?? flowDefault(slug);
              return { ...f, [slug]: { ...e, tags: { ...e.tags, [env]: value } } };
            });
            this.generating.update((m) => {
              const n = { ...m };
              delete n[key];
              return n;
            });
          }, 350);
        }
      }
      this.closeActions();
      return;
    }
  }

  // ---- sub-paso: tags de flujo ----

  readonly flowTagChecksList = computed(() =>
    Object.keys(this.flowTagChecks()).sort().map((name) => ({ name, checked: !!this.flowTagChecks()[name] })));

  private enterFlowStep(): void {
    const slugs = this.selectedSlugs();
    const existing = new Set<string>();
    for (const slug of slugs) {
      (this.repos().find((r) => r.slug === slug)?.tags ?? []).forEach((t) => existing.add(t));
    }
    const checks: Record<string, boolean> = {};
    for (const t of [...existing].sort()) {
      checks[t] = slugs.every((s) => (this.repos().find((r) => r.slug === s)?.tags ?? []).includes(t));
    }
    this.flowTagChecks.set(checks);
    this.newTag.set('');
    this.newTagError.set('');
    this.flowStep.set(true);
  }

  toggleFlowTag(name: string): void {
    this.flowTagChecks.update((m) => ({ ...m, [name]: !m[name] }));
  }

  addNewTag(): void {
    const val = this.newTag().trim().toLowerCase();
    if (!val) { this.newTagError.set('Ingresá un nombre.'); return; }
    if (val.length < 2) { this.newTagError.set('Muy corto (mín. 2 chars).'); return; }
    if (!/^[a-z0-9][a-z0-9_-]*$/.test(val)) { this.newTagError.set('Solo minúsculas, números, guiones o guión bajo.'); return; }
    if (this.flowTagChecks()[val] !== undefined) { this.newTagError.set('Ese tag ya está en la lista.'); return; }
    this.flowTagChecks.update((m) => ({ ...m, [val]: true }));
    this.newTag.set('');
    this.newTagError.set('');
  }

  applyFlowTags(): void {
    const chosen = Object.keys(this.flowTagChecks()).filter((k) => this.flowTagChecks()[k]);
    const slugs = this.selectedSlugs();
    this.repos.update((list) => list.map((r) =>
      slugs.includes(r.slug) ? { ...r, tags: [...new Set([...(r.tags ?? []), ...chosen])] } : r));
    this.closeActions();
  }

  clearFlowTags(): void {
    const slugs = this.selectedSlugs();
    this.repos.update((list) => list.map((r) => (slugs.includes(r.slug) ? { ...r, tags: [] } : r)));
    this.closeActions();
  }

  backToOptions(): void {
    this.flowStep.set(false);
  }

  // ===================== DETALLE =====================

  openDetail(slug: string): void {
    // Réplica de GET /api/flow + /api/ssm/values + /api/ssm/environments.
    // El flow del mockup matchea por slug corto; con el slug completo cae al
    // fallback (sin PR, sin tags de deploy) — se reproduce ese comportamiento.
    this.flowBySlug.update((f) => ({ ...f, [slug]: f[slug] ?? flowDefault(slug) }));
    this.paramsBySlug.update((m) => ({ ...m, [slug]: ssmParamsFor(slug) }));
    this.regions.set(MOCK_REGIONS.slice());
    const sel: Record<string, boolean> = {};
    MOCK_REGIONS.forEach((r) => (sel[r] = true));
    this.selectedRegions.set(sel);
    const pr = this.flowBySlug()[slug]?.pr;
    this.detailPrTitle.set(pr?.title ?? `Release: ${this.origin()} → ${this.destination()}`);
    this.detailSlug.set(slug);
    this.detailScrollTop();
  }

  private detailScrollTop(): void {
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  closeDetail(): void {
    this.detailSlug.set(null);
    this.detailScrollTop();
  }

  readonly detailFlow = computed(() => {
    const slug = this.detailSlug();
    return slug ? this.flowBySlug()[slug] ?? null : null;
  });

  detailRepo(slug: string | null): RepoRow | undefined {
    return slug ? this.repos().find((r) => r.slug === slug) : undefined;
  }

  resolvedOf(slug: string | null): string {
    return this.detailRepo(slug)?.resolved_branch || '—';
  }

  projectOf(slug: string | null): string {
    return this.detailRepo(slug)?.project ?? '';
  }

  bbUrl(slug: string): string {
    return `https://bitbucket.org/${this.workspace}/${slug}/browse`;
  }

  deployedTag(env: string): string | null {
    return this.detailFlow()?.tags?.[env] ?? null;
  }

  generatingKey(key: string): boolean {
    return !!this.generating()[key];
  }

  generateTag(env: string): void {
    const slug = this.detailSlug();
    if (!slug) return;
    const key = this.tagKey(slug, env);
    if (this.generating()[key]) return;
    this.generating.update((m) => ({ ...m, [key]: true }));
    this.schedule(() => {
      const value = tagFor(slug, this.origin().trim(), env);
      this.flowBySlug.update((f) => {
        const e = f[slug] ?? flowDefault(slug);
        return { ...f, [slug]: { ...e, tags: { ...e.tags, [env]: value } } };
      });
      this.generating.update((m) => {
        const n = { ...m };
        delete n[key];
        return n;
      });
    }, 350);
  }

  updatePrTitle(): void {
    const slug = this.detailSlug();
    if (!slug) return;
    const title = this.detailPrTitle().trim();
    if (!title) return;
    const key = slug + '|pr';
    if (this.generating()[key]) return;
    this.generating.update((m) => ({ ...m, [key]: true }));
    this.schedule(() => {
      const pr = prFor(slug, title);
      this.flowBySlug.update((f) => {
        const e = f[slug] ?? flowDefault(slug);
        return { ...f, [slug]: { ...e, pr } };
      });
      this.generating.update((m) => {
        const n = { ...m };
        delete n[key];
        return n;
      });
    }, 350);
  }

  // ---- SSM params del detalle ----

  readonly activeRegions = computed(() =>
    this.regions().filter((r) => this.selectedRegions()[r]));

  readonly paramsCols = computed(() => {
    const cols = this.activeRegions();
    return `2fr 1fr ${cols.map(() => '1.2fr').join(' ')} 0.8fr`;
  });

  readonly detailParams = computed(() => {
    const slug = this.detailSlug();
    return slug ? (this.paramsBySlug()[slug] ?? []) : [];
  });

  regionOn(region: string): boolean {
    return !!this.selectedRegions()[region];
  }

  toggleRegion(region: string): void {
    this.selectedRegions.update((m) => ({ ...m, [region]: !m[region] }));
  }

  paramValue(p: MockedSsmParam, region: string): string {
    return (p.values ?? {})[region] || '';
  }

  excelStatus(): string {
    return this.excelStatusText();
  }

  onExcel(): void {
    if (this.excelPct() !== null) return;
    let pct = 0;
    if (this.excelTimer) clearInterval(this.excelTimer);
    this.excelTimer = setInterval(() => {
      pct = Math.min(100, pct + 3);
      this.excelPct.set(pct);
      this.excelStatusText.set(
        pct >= 100
          ? 'Excel listo: ssm-params-workspace-2026-09-15.xlsx'
          : `Consulta completa del workspace… ${pct}%`,
      );
      if (pct >= 100) {
        clearInterval(this.excelTimer!);
        this.excelTimer = null;
      }
    }, 250);
  }
}