import { Component, computed, inject, signal } from '@angular/core';
import { ReleaseStore } from '../shared/release-store';

type ActionsView = 'menu' | 'tags';

@Component({
  imports: [],
  selector: 'app-actions-modal',
  styleUrl: './actions-modal.css',
  templateUrl: './actions-modal.html',
})
export class ActionsModal {
  readonly store = inject(ReleaseStore);

  view = signal<ActionsView>('menu');

  readonly selectedRepos = computed(() => {
    const sel = this.store.selected();
    return this.store.repos().filter((r) => sel.has(r.slug));
  });

  readonly readyPr = computed(
    () => this.selectedRepos().filter((r) => this.store.states()[r.slug] === 'found').length,
  );

  readonly withFlow = computed(() => this.selectedRepos().filter((r) => (r.tags || []).length > 0).length);

  readonly existingTags = computed(() => {
    const tags = new Set<string>();
    this.selectedRepos().forEach((r) => (r.tags || []).forEach((t) => tags.add(t)));
    return [...tags].sort();
  });

  readonly tagChecks = signal<Record<string, boolean>>({});
  readonly newTag = signal('');
  readonly newTagError = signal('');

  open(): void {
    this.store.openActionsModal();
    this.view.set('menu');
  }

  close(): void {
    this.store.closeActionsModal();
  }

  onOverlayClick(event: Event): void {
    if (event.target === event.currentTarget) this.close();
  }

  allHave(tag: string): boolean {
    return this.selectedRepos().every((r) => (r.tags || []).includes(tag));
  }

  openTagsView(): void {
    const checks: Record<string, boolean> = {};
    for (const tag of this.existingTags()) checks[tag] = this.allHave(tag);
    this.tagChecks.set(checks);
    this.newTag.set('');
    this.newTagError.set('');
    this.view.set('tags');
  }

  toggleTagCheck(tag: string): void {
    this.tagChecks.update((c) => ({ ...c, [tag]: !c[tag] }));
  }

  addNewTag(): void {
    const val = this.newTag().trim().toLowerCase();
    if (!val) {
      this.newTagError.set('Ingresá un nombre.');
      return;
    }
    if (val.length < 2) {
      this.newTagError.set('Muy corto (mín. 2 chars).');
      return;
    }
    if (!/^[a-z0-9][a-z0-9_-]*$/.test(val)) {
      this.newTagError.set('Solo minúsculas, números, guiones o guión bajo.');
      return;
    }
    if (this.tagChecks()[val] !== undefined) {
      this.newTagError.set('Ese tag ya está en la lista.');
      return;
    }
    this.tagChecks.update((c) => ({ ...c, [val]: true }));
    this.newTag.set('');
    this.newTagError.set('');
  }

  applyTags(): void {
    const chosen = Object.entries(this.tagChecks())
      .filter(([, on]) => on)
      .map(([tag]) => tag);
    this.store.applyTagsToSelection(chosen);
    this.close();
  }

  clearTags(): void {
    this.store.clearTagsFromSelection();
    this.close();
  }

  async onMassPr(): Promise<void> {
    for (const slug of this.store.selected()) {
      if (this.store.states()[slug] !== 'found') continue;
      if (this.store.flowBySlug()[slug]?.pr?.url) continue;
      await this.store.createPr(slug);
    }
    this.close();
  }

  async onMassTagsDeploy(): Promise<void> {
    const origin = this.store.origin();
    const destination = this.store.destination();
    for (const slug of this.store.selected()) {
      if (this.store.states()[slug] !== 'found') continue;
      for (const env of ['uat', 'stgp']) {
        if (this.store.flowBySlug()[slug]?.tags?.[env]) continue;
        await this.store.generateTag(slug, env);
      }
    }
    this.close();
  }

  countAptos(): string {
    return `${this.readyPr()}/${this.selectedRepos().length} aptos`;
  }

  countTags(): string {
    return `${this.withFlow()}/${this.selectedRepos().length} con tags`;
  }

  hintMar(): string {
    const miss = this.selectedRepos().length - this.readyPr();
    return miss > 0 ? `${miss} sin rama resuelta` : '';
  }

  objectEntries(obj: Record<string, boolean>): Array<[string, boolean]> {
    return Object.entries(obj);
  }
}