import { Component, computed, input, output, WritableSignal } from '@angular/core';

export interface ParamRow {
  param: string;
  estado: string;
  qaValue: string | null;
  awsStatus: string;
  type: string;
  envValues: Record<string, string>;
}

export interface ParamsRow {
  param: string;
  tipo: string;
  qa_value: string | null;
  aws_status: string;
  type?: string;
  env_values?: Record<string, string>;
  repos: string[];
}

export interface RemovedRow {
  param: string;
  repos: string[];
}

@Component({
  selector: 'app-params-panel',
  templateUrl: './params-panel.html',
  styleUrl: './params-panel.scss',
  standalone: true,
})
export class ParamsPanelComponent {
  connected = input(false);
  paramsLoaded = input(false);
  origin = input('');
  destination = input('');
  hasRepos = input(false);

  scanMode = input.required<WritableSignal<string>>();
  paramsLoading = input.required<WritableSignal<boolean>>();
  params = input<ParamsRow[]>([]);
  removed = input<RemovedRow[]>([]);
  rows = input<ParamRow[]>([]);
  estadoOptions = input<string[]>([]);
  estadoFilter = input.required<WritableSignal<string[]>>();

  loadParams = output<void>();
  openParams = output<void>();
  toggleEstado = output<string>();
  openSsmView = output<string>();

  protected scanValue = computed<'diff' | 'all'>(() => this.scanMode()() as 'diff' | 'all');

  protected setScanMode(mode: 'diff' | 'all'): void {
    this.scanMode().set(mode);
  }

  protected estadoFilterActive(estado: string): boolean {
    const f = this.estadoFilter()();
    return f.length === 0 || f.includes(estado);
  }

  protected estadoLabel(estado: string): string {
    switch (estado) {
      case 'reutilizado':
        return 'Reutilizado (productivo)';
      case 'solo destino':
        return 'Solo destino';
      default:
        return 'Nuevo';
    }
  }

  protected tipoClass(tipo: string): string {
    switch (tipo) {
      case 'reutilizado':
        return 'bb-badge--warn';
      case 'solo destino':
        return 'bb-badge--neutral';
      default:
        return 'bb-badge--ok';
    }
  }

  protected chipLabel(estado: string): string {
    if (estado === 'solo destino') return 'Solo destino';
    if (estado === 'reutilizado') return 'Reutilizado';
    return 'Nuevo';
  }
}