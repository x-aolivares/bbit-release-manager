import { Component, input, output, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { IonButton, IonIcon, IonItem, IonList } from '@ionic/angular';
import { SessionConfig } from '../../services/session-history.service';

@Component({
  selector: 'app-session-sidebar',
  templateUrl: './session-sidebar.html',
  styleUrl: './session-sidebar.scss',
  standalone: true,
  imports: [
    CommonModule,
    IonButton, IonIcon, IonItem, IonList,
  ],
})
export class SessionSidebarComponent {
  sessions = input.required<SessionConfig[]>();
  currentSessionId = input<string | null>(null);
  sessionSelected = output<SessionConfig>();
  sessionDeleted = output<string>();
  historyCleared = output<void>();

  protected formatRelativeTime(ts: number): string {
    const diff = Date.now() - ts;
    const minutes = Math.floor(diff / 60000);
    const hours = Math.floor(diff / 3600000);
    const days = Math.floor(diff / 86400000);

    if (minutes < 1) return 'ahora';
    if (minutes < 60) return `hace ${minutes}m`;
    if (hours < 24) return `hace ${hours}h`;
    if (days < 7) return `hace ${days}d`;
    return new Date(ts).toLocaleDateString('es-AR', { day: '2-digit', month: '2-digit' });
  }

  protected onSelect(session: SessionConfig): void {
    this.sessionSelected.emit(session);
  }

  protected onDelete(event: Event, id: string): void {
    event.stopPropagation();
    this.sessionDeleted.emit(id);
  }

  protected onClear(): void {
    this.historyCleared.emit();
  }

  protected trackById(_: number, s: SessionConfig): string {
    return s.id;
  }
}