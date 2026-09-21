import { Component, inject } from '@angular/core';
import { ReleaseStore } from '../shared/release-store';

@Component({
  imports: [],
  selector: 'app-create-pr-modal',
  styleUrl: './create-pr-modal.css',
  templateUrl: './create-pr-modal.html',
})
export class CreatePrModal {
  readonly store = inject(ReleaseStore);

  get open(): boolean {
    return this.store.prModalSlug() != null;
  }

  close(): void {
    this.store.closePrModal();
  }

  onOverlayClick(event: Event): void {
    if (event.target === event.currentTarget) this.close();
  }

  confirm(): void {
    const slug = this.store.prModalSlug();
    if (!slug) return;
    const title = this.store.prTitle().trim();
    this.close();
    void this.store.createPr(slug, title || undefined);
  }

  route(): string {
    const slug = this.store.prModalSlug() ?? '';
    return `POST /api/pr?repo=${slug}&origin=${encodeURIComponent(this.store.origin())}&destination=${this.store.destination()}`;
  }
}