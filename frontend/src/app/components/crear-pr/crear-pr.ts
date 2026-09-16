import { Component, effect, input, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';

/**
 * Modal de creación de un PR individual: muestra repo, ruta de API y título
 * editable. Emite `confirm` con el slug y el título elegido.
 */
@Component({
  selector: 'app-crear-pr',
  templateUrl: './crear-pr.html',
  styleUrl: './crear-pr.scss',
  standalone: true,
  imports: [FormsModule],
})
export class CrearPrComponent {
  open = input(false);
  slug = input('');
  origin = input('');
  destination = input('master');

  title = signal('');

  confirm = output<{ slug: string; title: string }>();
  close = output<void>();

  constructor() {
    // Al abrir el modal (open pasa a true) se resetea el título al default.
    effect(() => {
      if (this.open()) {
        this.title.set(`Release: ${this.origin()} → ${this.destination() || 'master'}`);
      }
    });
  }

  get route(): string {
    if (!this.slug()) return '';
    const o = encodeURIComponent(this.origin());
    const d = encodeURIComponent(this.destination() || 'master');
    return `POST /api/pr?repo=${this.slug()}&origin=${o}&destination=${d}`;
  }

  confirmar(): void {
    const t = this.title().trim();
    if (!this.slug()) return;
    this.confirm.emit({ slug: this.slug(), title: t });
  }
}