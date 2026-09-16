import { Component, computed, input, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import type { ReposBuscarRow } from '../repos-buscados/repos-buscados';

export interface AccionesMasivasState {
  readyPr: number;
  withFlow: number;
  selected: ReposBuscarRow[];
}

export interface FlowTagsRequest {
  slugs: string[];
  apply: string[];
  clear: boolean;
}

export interface DeployTagsRequest {
  slugs: string[];
  prefixes: string[];
}

/**
 * Modal de acciones masivas sobre la selección: crear PRs, generar tags de
 * deploy (CircleCI) o etiquetar repos con su flujo (fargate, step-function,
 * workflow, batch). El sub-paso de tags de flujo vive aquí (chips + nuevo
 * tag), siguiendo el lenguaje del buscador.
 */
@Component({
  selector: 'app-acciones-masivas',
  templateUrl: './acciones-masivas.html',
  styleUrl: './acciones-masivas.scss',
  standalone: true,
  imports: [FormsModule],
})
export class AccionesMasivasComponent {
  open = input(false);
  selected = input<ReposBuscarRow[]>([]);
  totalSelected = input(0);
  readyPr = input(0);
  processing = input(false);

  massPr = output<void>();
  massTagsDeploy = output<DeployTagsRequest>();
  flowTags = output<FlowTagsRequest>();
  close = output<void>();

  pasoFlow = signal(false);
  newTag = signal('');
  newTagError = signal('');
  selectedTags = signal<Set<string>>(new Set());

  readonly existingTags = computed(() => {
    const all = new Set<string>();
    this.selected().forEach((r) => (r.tags || []).forEach((t) => all.add(t)));
    return [...all].sort();
  });

  readonly todosConTag = computed(() => {
    const t = this.selectedTags();
    const selected = this.selected();
    if (!t.size || !selected.length) return false;
    for (const tag of t) {
      if (!selected.every((r) => (r.tags || []).includes(tag))) {
        return false;
      }
    }
    return true;
  });

  private resetSubPaso(): void {
    this.pasoFlow.set(false);
    this.newTag.set('');
    this.newTagError.set('');
    this.selectedTags.set(new Set(
      this.existingTags().filter((t) =>
        this.selected().length > 0 &&
        this.selected().every((r) => (r.tags || []).includes(t)),
      ),
    ));
  }

  abrir(): void {
    this.resetSubPaso();
  }

  get sinError(): boolean {
    return this.newTagError() === '';
  }

  toggleTag(tag: string, checked: boolean): void {
    this.selectedTags.update((cur) => {
      const next = new Set(cur);
      if (checked) next.add(tag);
      else next.delete(tag);
      return next;
    });
  }

  agregarNuevoTag(): void {
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
    if (this.selectedTags().has(val)) {
      this.newTagError.set('Ese tag ya está en la lista.');
      return;
    }
    this.selectedTags.update((cur) => {
      const next = new Set(cur);
      next.add(val);
      return next;
    });
    this.newTag.set('');
    this.newTagError.set('');
  }

  aplicarFlow(): void {
    const slugs = this.selected().map((r) => r.slug);
    this.flowTags.emit({ slugs, apply: [...this.selectedTags()], clear: false });
  }

  limpiarFlow(): void {
    const slugs = this.selected().map((r) => r.slug);
    this.flowTags.emit({ slugs, apply: [], clear: true });
  }

  isChecked(tag: string): boolean {
    return this.selectedTags().has(tag);
  }

  chipClass(tag: string): string {
    return this.isChecked(tag) ? 'bb-tag-chip bb-tag-chip--active' : 'bb-tag-chip';
  }
}