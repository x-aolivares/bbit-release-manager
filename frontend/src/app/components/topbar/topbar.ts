import {
  Component,
  HostListener,
  input,
  output,
  signal,
} from '@angular/core';
import { IonHeader } from '@ionic/angular/ion-header';
import { IonToolbar } from '@ionic/angular/ion-toolbar';
import { IonIcon } from '@ionic/angular';

export interface TopbarHealth {
  status: string;
  version: string;
  connected: boolean;
  workspace: string | null;
  identity: string | null;
  repo_count: number;
}

@Component({
  selector: 'app-topbar',
  templateUrl: './topbar.html',
  styleUrl: './topbar.scss',
  standalone: true,
  imports: [IonHeader, IonToolbar, IonIcon],
})
export class TopbarComponent {
  connected = input.required<boolean>();
  identity = input('');
  repoCount = input(0);
  health = input<TopbarHealth | null>(null);
  clientAlias = input('');

  openHistory = output<void>();
  requestConfig = output<void>();
  requestDisconnect = output<void>();

  protected open = signal(false);
  protected position = signal<{ top: number; right: number }>({ top: 0, right: 0 });

  protected initials(): string {
    const id = this.identity().trim();
    if (!id) return '';
    const parts = id.split(/\s+/);
    if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
    return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
  }

  protected toggle(): void {
    if (this.open()) {
      this.open.set(false);
      return;
    }
    const trigger = (document.querySelector('.bb-user-menu-trigger') as HTMLElement | null);
    if (trigger) {
      const rect = trigger.getBoundingClientRect();
      this.position.set({
        top: rect.bottom + 6,
        right: Math.round(window.innerWidth - rect.right),
      });
    }
    this.open.set(true);
  }

  protected onConfig(): void {
    this.open.set(false);
    this.requestConfig.emit();
  }

  protected onDisconnect(): void {
    this.open.set(false);
    this.requestDisconnect.emit();
  }

  @HostListener('document:click', ['$event'])
  protected onDocClick(event: Event): void {
    const t = event.target as HTMLElement;
    if (this.open() && !t.closest('.bb-user-menu-wrap') && !t.closest('.bb-user-menu')) {
      this.open.set(false);
    }
  }

  @HostListener('window:scroll')
  protected onWindowScroll(): void {
    if (this.open()) {
      this.open.set(false);
    }
  }

  @HostListener('window:resize')
  protected onWindowResize(): void {
    if (this.open()) {
      this.open.set(false);
    }
  }

  @HostListener('document:keydown.escape')
  protected onEscape(): void {
    this.open.set(false);
  }
}