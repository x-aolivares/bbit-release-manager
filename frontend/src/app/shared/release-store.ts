import { Injectable, computed, signal } from '@angular/core';
import { APP_CONFIG } from './app-config';
import { MockApi } from './mock-api';
import { RepoStore } from './repo-store';
import { Repo, RepoCheckState, FlowRepo, SsmParam } from './types';

@Injectable({ providedIn: 'root' })
export class ReleaseStore {
  // ---- listado ----
  readonly repos = signal<Repo[]>([]);
  readonly removed = signal<ReadonlySet<string>>(new Set());
  readonly states = signal<Record<string, RepoCheckState>>({});
  readonly flowBySlug = signal<Record<string, FlowRepo>>({});
  readonly paramsBySlug = signal<Record<string, SsmParam[]>>({});
  readonly creating = signal<ReadonlySet<string>>(new Set());
  readonly generating = signal<ReadonlySet<string>>(new Set());

  // ---- buscador ----
  readonly prefixes = signal<string[]>([...APP_CONFIG.default_filters.project_prefixes]);
  readonly blacklist = signal<string[]>([...APP_CONFIG.default_filters.exclude]);
  readonly origin = signal(APP_CONFIG.default_branches.origin);
  readonly destination = signal(APP_CONFIG.default_branches.destination);

  // ---- tabla ----
  readonly processOutput = signal(false);
  readonly search = signal('');
  readonly tagFilters = signal<ReadonlySet<string>>(new Set());
  readonly page = signal(1);
  readonly pageSize = signal(10);
  readonly tableVisible = signal(false);
  readonly checking = signal(false);
  readonly branchesChecked = signal(false);

  // ---- selección ----
  readonly selected = signal<ReadonlySet<string>>(new Set());

  // ---- regiones / detalle ----
  readonly regions = signal<string[]>(APP_CONFIG.regions);
  readonly selectedRegions = signal<ReadonlySet<string>>(new Set());
  readonly detailSlug = signal<string | null>(null);

  // ---- modales ----
  readonly actionsModalOpen = signal(false);
  readonly prModalSlug = signal<string | null>(null);
  readonly prTitle = signal(`Release: ${APP_CONFIG.default_branches.origin} → ${APP_CONFIG.default_branches.destination}`);

  // ---- computados ----
  readonly visibleRepos = computed(() =>
    this.repos()
      .filter((r) => !this.removed().has(r.slug))
      .slice()
      .sort((a, b) => a.slug.localeCompare(b.slug)),
  );

  readonly filteredRepos = computed(() => {
    const q = this.search().trim().toLowerCase();
    const tags = this.tagFilters();
    return this.visibleRepos().filter((r) => {
      if (q && !r.slug.toLowerCase().includes(q) && !(r.name || '').toLowerCase().includes(q)) return false;
      if (tags.size && !(r.tags || []).some((t) => tags.has(t))) return false;
      return true;
    });
  });

  readonly pages = computed(() => Math.max(1, Math.ceil(this.filteredRepos().length / this.pageSize())));
  readonly pageRows = computed(() => {
    const all = this.filteredRepos();
    const rows = all.slice((this.page() - 1) * this.pageSize(), this.page() * this.pageSize());
    return rows;
  });

  readonly allTags = computed(() => {
    const tags = new Set<string>();
    this.visibleRepos().forEach((r) => (r.tags || []).forEach((t) => tags.add(t)));
    return [...tags].sort();
  });

  readonly statFound = computed(() => this.visibleRepos().filter((r) => this.states()[r.slug] === 'found').length);

  readonly statTotal = computed(() => this.repos().length);

  readonly statMissing = computed(() => this.statTotal() - this.statFound());

  readonly selectedCount = computed(() => this.selected().size);

  readonly allFilteredSelected = computed(() => {
    const visible = this.filteredRepos().map((r) => r.slug);
    return visible.length > 0 && visible.every((s) => this.selected().has(s));
  });

  readonly pageSelected = computed(() => {
    const pageSlugs = this.pageRows().map((r) => r.slug);
    return pageSlugs.length > 0 && pageSlugs.every((s) => this.selected().has(s));
  });

  readonly detailFlow = computed(() => {
    const slug = this.detailSlug();
    return slug ? this.flowBySlug()[slug] : null;
  });

  readonly detailParams = computed(() => {
    const slug = this.detailSlug();
    return slug ? this.paramsBySlug()[slug] || [] : [];
  });

  readonly detailSelectedRegions = computed(() => {
    const cols = this.regions().filter((r) => this.selectedRegions().has(r));
    return cols;
  });

  // ---- acciones: buscador ----
  onPrefixesChange(next: string[]): void {
    this.prefixes.set([...next]);
  }

  onBlacklistChange(next: string[]): void {
    this.blacklist.set([...next]);
  }

  async searchRepositories(): Promise<void> {
    const res = await this.api.getReposQuick({
      origin: this.origin(),
      destination: this.destination(),
      project_prefixes: this.prefixes().join(',') || APP_CONFIG.default_filters.project_prefixes.join(','),
      exclude: this.blacklist().join(','),
    });
    const flow = await this.api.getFlow({
      origin: APP_CONFIG.default_branches.origin,
      destination: APP_CONFIG.default_branches.destination,
      with_diff: true,
      with_tags: true,
    });

    const flowBySlug: Record<string, FlowRepo> = {};
    for (const r of flow.repos) flowBySlug[r.slug] = r;
    for (const r of res.repos) {
      if (r.pr) {
        if (!flowBySlug[r.slug]) flowBySlug[r.slug] = { slug: r.slug, pr: null, tags: {}, has_diff: true };
        flowBySlug[r.slug].pr = r.pr;
      }
    }

    const states: Record<string, RepoCheckState> = {};
    res.repos.forEach((r) => {
      states[r.slug] = 'pending';
    });

    this.repos.set(res.repos);
    this.flowBySlug.set(flowBySlug);
    this.states.set(states);
    this.removed.set(new Set());
    this.search.set('');
    this.tagFilters.set(new Set());
    this.tableVisible.set(true);
    this.checking.set(false);
    this.branchesChecked.set(false);
    this.selected.set(new Set());
  }

  // ---- acciones: procesar salida ----
  setProcessOutput(on: boolean): void {
    this.processOutput.set(on);
    if (!on) {
      this.removed.set(new Set());
      const states: Record<string, RepoCheckState> = {};
      this.repos().forEach((r) => {
        states[r.slug] = 'pending';
      });
      this.states.set(states);
      this.checking.set(false);
      this.branchesChecked.set(false);
    }
  }

  checkBranches(): void {
    if (this.checking()) return;
    this.checking.set(true);
    const removed = new Set(this.removed());
    removed.clear();
    const states: Record<string, RepoCheckState> = { ...this.states() };
    this.repos().forEach((r) => {
      states[r.slug] = 'checking';
    });
    this.states.set(states);

    this.repos().forEach((repo, i) => {
      setTimeout(() => {
        const found = repo.branch_state === 'found';
        const next: Record<string, RepoCheckState> = { ...this.states() };
        next[repo.slug] = found ? 'found' : 'not_found';
        this.states.set(next);
        if (!found) {
          const rm = new Set(this.removed());
          rm.add(repo.slug);
          this.removed.set(rm);
          const sel = new Set(this.selected());
          sel.delete(repo.slug);
          this.selected.set(sel);
        }
        if (this.repos().every((r) => next[r.slug] !== 'checking')) {
          this.checking.set(false);
          this.branchesChecked.set(true);
        }
      }, 300 + i * 120);
    });
  }

  // ---- acciones: PR ----
  async createPr(slug: string, title?: string): Promise<void> {
    if (this.creating().has(slug)) return;
    const creating = new Set(this.creating());
    creating.add(slug);
    this.creating.set(creating);

    const res = await this.api.createPr({
      repo: slug,
      origin: this.origin(),
      destination: this.destination(),
      title: title || `Release: ${this.origin()} → ${this.destination()}`,
    });

    const flowBySlug = { ...this.flowBySlug() };
    if (!flowBySlug[slug]) flowBySlug[slug] = { slug, pr: null, tags: {}, has_diff: true };
    flowBySlug[slug].pr = res.pr;
    this.flowBySlug.set(flowBySlug);

    this.repos.update((repos) => repos.map((r) => (r.slug === slug ? { ...r, pr: res.pr } : r)));

    const next = new Set(this.creating());
    next.delete(slug);
    this.creating.set(next);
  }

  async updatePrTitle(slug: string, title: string): Promise<void> {
    const key = slug + '|pr';
    if (this.generating().has(key)) return;
    const gen = new Set(this.generating());
    gen.add(key);
    this.generating.set(gen);

    const res = await this.api.updatePrTitles({
      repos: [slug],
      origin: this.origin(),
      destination: this.destination(),
      title,
    });
    const row = res.repos[0];
    if (row) {
      const flowBySlug = { ...this.flowBySlug() };
      flowBySlug[slug] = { slug, pr: { id: row.id, title: row.title, url: row.url }, tags: flowBySlug[slug]?.tags || {}, has_diff: true };
      this.flowBySlug.set(flowBySlug);
    }
    const next = new Set(this.generating());
    next.delete(key);
    this.generating.set(next);
  }

  async generateTag(slug: string, env: string): Promise<void> {
    const key = slug + '|tag:' + env;
    if (this.generating().has(key)) return;
    const gen = new Set(this.generating());
    gen.add(key);
    this.generating.set(gen);

    const res = await this.api.createTags({
      repo: slug,
      origin: this.origin(),
      destination: this.destination(),
      prefixes: [env],
    });

    const flowBySlug = { ...this.flowBySlug() };
    if (!flowBySlug[slug]) flowBySlug[slug] = { slug, pr: null, tags: {}, has_diff: true };
    flowBySlug[slug].tags = { ...flowBySlug[slug].tags, ...res.tags };
    this.flowBySlug.set(flowBySlug);

    const next = new Set(this.generating());
    next.delete(key);
    this.generating.set(next);
  }

  // ---- acciones: tabla (filtros/paginación) ----
  setSearch(value: string): void {
    this.search.set(value);
    this.page.set(1);
    this.pruneSelection();
  }

  toggleTagFilter(tag: string): void {
    const next = new Set(this.tagFilters());
    if (next.has(tag)) next.delete(tag);
    else next.add(tag);
    this.tagFilters.set(next);
    this.page.set(1);
    this.pruneSelection();
  }

  setPageSize(size: number): void {
    this.pageSize.set(size);
    this.page.set(1);
  }

  setPage(page: number): void {
    const clamped = Math.min(Math.max(1, page), this.pages());
    this.page.set(clamped);
  }

  prevPage(): void {
    if (this.page() <= 1) return;
    this.page.update((p) => p - 1);
  }

  nextPage(): void {
    this.page.update((p) => p + 1);
  }

  // ---- acciones: selección ----
  toggleSlug(slug: string, on: boolean): void {
    const sel = new Set(this.selected());
    if (on) sel.add(slug);
    else sel.delete(slug);
    this.selected.set(sel);
  }

  togglePage(on: boolean): void {
    const sel = new Set(this.selected());
    this.pageRows()
      .map((r) => r.slug)
      .forEach((s) => (on ? sel.add(s) : sel.delete(s)));
    this.selected.set(sel);
  }

  toggleAll(on: boolean): void {
    if (on) {
      this.selected.set(new Set(this.filteredRepos().map((r) => r.slug)));
    } else {
      const next = new Set(this.selected());
      this.filteredRepos()
        .map((r) => r.slug)
        .forEach((s) => next.delete(s));
      this.selected.set(next);
    }
  }

  clearSelection(): void {
    this.selected.set(new Set());
  }

  pruneSelection(): void {
    const visible = new Set(this.filteredRepos().map((r) => r.slug));
    const next = new Set([...this.selected()].filter((s) => visible.has(s)));
    this.selected.set(next);
  }

  // ---- acciones: tags de flujo masivos ----
  applyTagsToSelection(tags: string[]): void {
    const chosen = [...new Set(tags.map((t) => String(t).toLowerCase()))];
    this.repos.update((repos) =>
      repos.map((r) => {
        if (!this.selected().has(r.slug)) return r;
        const merged = [...new Set([...(r.tags || []), ...chosen])];
        this.repoStore.setRepoTags(r.slug, merged);
        return { ...r, tags: this.repoStore.getRepoTags(r.slug) };
      }),
    );
  }

  clearTagsFromSelection(): void {
    this.repos.update((repos) =>
      repos.map((r) => {
        if (!this.selected().has(r.slug)) return r;
        this.repoStore.setRepoTags(r.slug, []);
        return { ...r, tags: [] };
      }),
    );
    const flowBySlug = { ...this.flowBySlug() };
    for (const s of this.selected()) {
      if (flowBySlug[s]) {
        flowBySlug[s] = { ...flowBySlug[s], tags: {} };
      }
    }
    this.flowBySlug.set(flowBySlug);
  }

  // ---- acciones: modal de acciones ----
  openActionsModal(): void {
    this.actionsModalOpen.set(true);
  }

  closeActionsModal(): void {
    this.actionsModalOpen.set(false);
  }

  // ---- acciones: modal PR ----
  openPrModal(slug: string): void {
    this.prModalSlug.set(slug);
    this.prTitle.set(`Release: ${this.origin()} → ${this.destination()}`);
  }

  closePrModal(): void {
    this.prModalSlug.set(null);
  }

  // ---- acciones: detalle ----
  async openDetail(slug: string): Promise<void> {
    const [flow, ssm, envs] = await Promise.all([
      this.api.getFlow({
        origin: this.origin(),
        destination: this.destination(),
        with_diff: true,
        with_tags: true,
      }),
      this.api.getSsmValues(slug),
      this.api.getSsmEnvironments(),
    ]);

    const flowBySlug = { ...this.flowBySlug() };
    const found = flow.repos.find((r) => r.slug === slug);
    flowBySlug[slug] = found ?? { slug, pr: null, tags: {}, has_diff: true };
    this.flowBySlug.set(flowBySlug);

    const paramsBySlug = { ...this.paramsBySlug() };
    paramsBySlug[slug] = ssm.params;
    this.paramsBySlug.set(paramsBySlug);

    this.regions.set(envs.environments.map((e) => e.code));
    this.selectedRegions.set(new Set(envs.environments.map((e) => e.code)));
    this.detailSlug.set(slug);
  }

  closeDetail(): void {
    this.detailSlug.set(null);
  }

  // ---- acciones: regiones ----
  toggleRegion(code: string, on: boolean): void {
    const next = new Set(this.selectedRegions());
    if (on) next.add(code);
    else next.delete(code);
    this.selectedRegions.set(next);
  }

  constructor(
    private readonly api: MockApi,
    private readonly repoStore: RepoStore,
  ) {
    this.selectedRegions.set(new Set(APP_CONFIG.regions));
  }
}