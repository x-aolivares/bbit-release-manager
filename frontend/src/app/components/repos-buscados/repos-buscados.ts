import { Component, computed, input, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';

export interface ReposBuscarRow {
  slug: string;
  name: string;
  workspace: string;
  default_branch: string;
  tags: string[];
  branch_url?: string;
  resolved_branch?: string;
  branch_state?: 'found' | 'not_found';
  no_changes?: boolean;
  pr?: { url: string; title: string; id?: number } | null;
}

export type RepoBranchState = 'pending' | 'checking' | 'found' | 'not_found';

export interface ReposBuscarSelection {
  slugs: string[];
  allVisible: boolean;
}


@Component({
  selector: 'app-repos-buscados',
  templateUrl: './repos-buscados.html',
  styleUrl: './repos-buscados.scss',
  standalone: true,
  imports: [FormsModule],
})
export class ReposBuscadosComponent {
  repos = input.required<ReposBuscarRow[]>();
  states = input<Record<string, RepoBranchState>>({});
  origin = input('');
  destination = input('master');
  creating = input<Record<string, boolean>>({});
  removed = input(0);

  originChange = output<string>();
  destinationChange = output<string>();
  verRama = output<void>();
  crearPr = output<string>();
  abrirDetalle = output<string>();
  seleccion = output<ReposBuscarSelection>();
  acciones = output<string[]>();

  // ---------- estado local de la vista ----------
  procesarSalida = signal(false);
  verRamaEjecutado = signal(false);
  search = signal('');
  activeTags = signal<Set<string>>(new Set());
  selectedSlugs = signal<Set<string>>(new Set());
  page = signal(1);
  pageSize = signal(10);

  readonly filteredRepos = computed(() => {
    const q = this.search().trim().toLowerCase();
    const tags = this.activeTags();
    return this.repos().filter((r) => {
      if (q && !r.slug.toLowerCase().includes(q) && !(r.name || '').toLowerCase().includes(q)) {
        return false;
      }
      if (tags.size && !(r.tags || []).some((t) => tags.has(t))) {
        return false;
      }
      return true;
    });
  });

  readonly availableTags = computed(() => {
    const all = new Set<string>();
    this.repos().forEach((r) => (r.tags || []).forEach((t) => all.add(t)));
    return [...all].sort();
  });

  readonly pages = computed(() => {
    const total = this.filteredRepos().length;
    return Math.max(1, Math.ceil(total / this.pageSize()));
  });

  readonly paginatedRows = computed(() => {
    const all = this.filteredRepos();
    const pages = this.pages();
    if (this.page() > pages) {
      this.page.set(pages);
    }
    const start = (this.page() - 1) * this.pageSize();
    return all.slice(start, start + this.pageSize());
  });

  readonly offset = computed(() => (this.page() - 1) * this.pageSize());

  readonly seleccionSlugs = computed(() => [...this.selectedSlugs()]);

  readonly totalEncontrados = computed(() => this.repos().length);
  readonly conRama = computed(() =>
    this.repos().filter((r) => this.states()[r.slug] === 'found').length,
  );
  readonly sinRama = computed(() => this.totalEncontrados() - this.conRama());

  readonly verificando = computed(() =>
    this.repos().some((r) => this.states()[r.slug] === 'checking'),
  );

  readonly verRamaLabel = computed(() =>
    this.verificando() ? 'Verificando…' : 'Ver rama en estos repos',
  );

  readonly puedeVerRama = computed(
    () =>
      !this.verificando() &&
      this.totalEncontrados() > 0 &&
      !!this.origin().trim() &&
      !!this.destination().trim(),
  );

  readonly mostrarPr = computed(() => this.procesarSalida() && this.verRamaEjecutado());

  readonly todosFiltradosSeleccionados = computed(() => {
    const slugs = this.filteredRepos().map((r) => r.slug);
    return slugs.length > 0 && slugs.every((s) => this.selectedSlugs().has(s));
  });

  readonly paginaSeleccionada = computed(() => {
    const rows = this.paginatedRows();
    return rows.length > 0 && rows.every((r) => this.selectedSlugs().has(r.slug));
  });

  readonly paginfo = computed(() => {
    const all = this.filteredRepos();
    const from = all.length ? this.offset() + 1 : 0;
    const to = Math.min(this.offset() + this.pageSize(), all.length);
    const filtrado = this.search().trim() || this.activeTags().size
      ? ` (filtrado sobre ${this.repos().length})`
      : '';
    return `Mostrando ${from}–${to} de ${all.length} repositorios${filtrado} · ${this.removed()} removidos por no tener la rama`;
  });

  // ---------- búsqueda y tags ----------
  onSearch(value: string): void {
    this.search.set(value);
    this.page.set(1);
    this.pruneSelection();
  }

  toggleTagFilter(tag: string): void {
    this.activeTags.update((cur) => {
      const next = new Set(cur);
      if (next.has(tag)) next.delete(tag);
      else next.add(tag);
      return next;
    });
    this.page.set(1);
    this.pruneSelection();
  }

  // ---------- ver rama en estos repos ----------
  onToggleProcesarSalida(on: boolean): void {
    this.procesarSalida.set(on);
    this.verRamaEjecutado.set(false);
  }

  onVerRama(): void {
    if (!this.puedeVerRama()) return;
    this.verRamaEjecutado.set(true);
    this.verRama.emit();
  }

  // ---------- selección ----------
  private pruneSelection(): void {
    const visible = new Set(this.filteredRepos().map((r) => r.slug));
    this.selectedSlugs.update((cur) => {
      const next = new Set(cur);
      for (const s of [...next]) {
        if (!visible.has(s)) next.delete(s);
      }
      return next;
    });
    this.emitSelection();
  }

  onToggleRepo(slug: string, on: boolean): void {
    this.selectedSlugs.update((cur) => {
      const next = new Set(cur);
      if (on) next.add(slug);
      else next.delete(slug);
      return next;
    });
    this.emitSelection();
  }

  onToggleTodo(on: boolean): void {
    const slugs = this.filteredRepos().map((r) => r.slug);
    this.selectedSlugs.set(on ? new Set(slugs) : new Set());
    this.emitSelection();
  }

  onTogglePagina(on: boolean): void {
    const slugs = this.paginatedRows().map((r) => r.slug);
    this.selectedSlugs.update((cur) => {
      const next = new Set(cur);
      if (on) slugs.forEach((s) => next.add(s));
      else slugs.forEach((s) => next.delete(s));
      return next;
    });
    this.emitSelection();
  }

  onLimpiarSelection(): void {
    this.selectedSlugs.set(new Set());
    this.emitSelection();
  }

  private emitSelection(): void {
    this.seleccion.emit({
      slugs: [...this.selectedSlugs()],
      allVisible: this.todosFiltradosSeleccionados(),
    });
  }

  // ---------- paginación ----------
  onPageSize(size: number): void {
    this.pageSize.set(Number(size));
    this.page.set(1);
  }

  prevPage(): void {
    if (this.page() <= 1) return;
    this.page.update((p) => p - 1);
  }

  nextPage(): void {
    if (this.page() >= this.pages()) return;
    this.page.update((p) => p + 1);
  }

  // ---------- helpers de la celda de PR ----------
  estaCreando(slug: string): boolean {
    return !!this.creating()[slug];
  }

  prTieneUrl(repo: ReposBuscarRow): boolean {
    return !!repo.pr?.url && this.states()[repo.slug] === 'found';
  }

  ramaResuelta(repo: ReposBuscarRow): boolean {
    return this.states()[repo.slug] === 'found';
  }

  /** La rama está resuelta pero no tiene commits por delante del destino:
   *  no hay nada que mergear, no se ofrece crear PR. */
  sinCambios(repo: ReposBuscarRow): boolean {
    return this.ramaResuelta(repo) && !!repo.no_changes;
  }

  puedeCrearPr(repo: ReposBuscarRow): boolean {
    return this.ramaResuelta(repo) && !this.sinCambios(repo);
  }

  /**
   * URL del enlace de la columna de repositorio:
   * - Con "Procesar salida" + "ver rama": la rama origen (`branch_url`).
   * - Sin procesar salida: la rama default del repo (main/master según
   *   Bitbucket), construida con workspace + default_branch.
   * - Sin datos de rama: cae al root del repo quitando el sufijo de `branch_url`.
   */
  repoUrl(repo: ReposBuscarRow): string {
    const branchUrl = (repo.branch_url || '').trim();
    if (this.mostrarPr() && branchUrl) return branchUrl;
    const ws = (repo.workspace || '').trim();
    const def = (repo.default_branch || '').trim();
    if (ws && repo.slug && def) {
      return `https://bitbucket.org/${ws}/${repo.slug}/branch/${def}`;
    }
    if (branchUrl) {
      for (const marker of ['/branch/', '/src/', '/commits/', '/pull-requests/']) {
        const i = branchUrl.indexOf(marker);
        if (i !== -1) return branchUrl.slice(0, i);
      }
    }
    return '';
  }
}