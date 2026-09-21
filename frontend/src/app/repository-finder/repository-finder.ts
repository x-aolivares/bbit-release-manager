import { Component, inject } from '@angular/core';
import { ReleaseStore } from '../shared/release-store';

@Component({
  imports: [],
  selector: 'app-repository-finder',
  styleUrl: './repository-finder.css',
  templateUrl: './repository-finder.html',
})
export class RepositoryFinder {
  readonly store = inject(ReleaseStore);

  searching = false;

  async onSearch(): Promise<void> {
    if (this.searching) return;
    this.searching = true;
    try {
      await this.store.searchRepositories();
    } finally {
      this.searching = false;
    }
  }

  removePrefix(index: number): void {
    const next = [...this.store.prefixes()];
    next.splice(index, 1);
    this.store.onPrefixesChange(next);
  }

  removeBlacklist(index: number): void {
    const next = [...this.store.blacklist()];
    next.splice(index, 1);
    this.store.onBlacklistChange(next);
  }

  addPrefix(): void {
    const val = this.prompt('Nuevo prefijo de proyecto (ej: bbit-orders-):');
    if (val == null) return;
    const v = val.trim();
    if (!v) return;
    if (this.store.prefixes().includes(v)) {
      this.alert('Ya existe en la lista.');
      return;
    }
    this.store.onPrefixesChange([...this.store.prefixes(), v]);
  }

  addBlacklist(): void {
    const val = this.prompt('Slug a excluir (ej: bbit-trnxd-backend-qa):');
    if (val == null) return;
    const v = val.trim();
    if (!v) return;
    if (this.store.blacklist().includes(v)) {
      this.alert('Ya existe en la lista.');
      return;
    }
    this.store.onBlacklistChange([...this.store.blacklist(), v]);
  }

  private prompt(msg: string): string | null {
    return window.prompt(msg);
  }

  private alert(msg: string): void {
    window.alert(msg);
  }
}