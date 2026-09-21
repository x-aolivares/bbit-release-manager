import { Component, computed, inject } from '@angular/core';
import { ReleaseStore } from '../shared/release-store';
import { Repo } from '../shared/types';

@Component({
  imports: [],
  selector: 'app-table-of-filtered-repositories',
  styleUrl: './table-of-filtered-repositories.css',
  templateUrl: './table-of-filtered-repositories.html',
})
export class TableOfFilteredRepositories {
  readonly store = inject(ReleaseStore);

  readonly gridColumns = computed(() =>
    this.store.processOutput() ? '2.2rem 2rem 1.3fr 1.6fr 1.2fr' : '2.2rem 2rem 1.3fr 1.2fr',
  );

  readonly offset = computed(() => (this.store.page() - 1) * this.store.pageSize());
  readonly pageInfo = computed(() => {
    const all = this.store.filteredRepos().length;
    const visible = this.store.visibleRepos().length;
    const from = all ? this.offset() + 1 : 0;
    const to = Math.min(this.offset() + this.store.pageSize(), all);
    const filtrado = this.store.search() || this.store.tagFilters().size ? ` (filtrado sobre ${visible})` : '';
    return `Mostrando ${from}–${to} de ${all} repositorios${filtrado} · ${this.store.removed().size} removidos por no tener la rama`;
  });

  readonly padWidth = computed(() => Math.max(1, String(this.store.filteredRepos().length).length));

  rowNumber(i: number): string {
    return String(this.offset() + i + 1).padStart(this.padWidth(), '0');
  }

  isSelected(slug: string): boolean {
    return this.store.selected().has(slug);
  }

  prKind(repo: Repo): 'creating' | 'none' | 'link' | 'create' {
    if (this.store.creating().has(repo.slug)) return 'creating';
    if (this.store.states()[repo.slug] !== 'found') return 'none';
    const pr = this.store.flowBySlug()[repo.slug]?.pr;
    if (pr && pr.url) return 'link';
    return 'create';
  }

  prUrl(repo: Repo): string {
    return this.store.flowBySlug()[repo.slug]?.pr?.url ?? '';
  }

  prTitle(repo: Repo): string {
    return this.store.flowBySlug()[repo.slug]?.pr?.title ?? '';
  }

  onProcessOutputChange(): void {
    this.store.setProcessOutput(!this.store.processOutput());
  }

  onCheckBranches(): void {
    this.store.checkBranches();
  }
}