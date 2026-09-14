import { Component, computed, effect, input, output, signal } from '@angular/core';
import { repoUrl as flowRepoUrl } from '../../pages/home/flow-utils';
import { envTag, envTagHref } from '../../pages/home/env-utils';
import type { ReposRow } from '../repos-table/repos-table';
import type { ParamRow } from '../params-panel/params-panel';

export type ReportMode = 'branch' | 'pr' | 'tag' | 'params';

@Component({
  selector: 'app-report-modal',
  templateUrl: './report-modal.html',
  styleUrl: './report-modal.scss',
  standalone: true,
})
export class ReportModalComponent {
  open = input(false);
  initialMode = input<ReportMode>('branch');
  origin = input('');
  prefixes = input.required<string[]>();
  repos = input<ReposRow[]>([]);
  rows = input<ParamRow[]>([]);

  close = output<void>();

  protected mode = signal<ReportMode>('branch');
  protected envIndex = signal(0);
  protected copied = signal(false);

  constructor() {
    effect(() => {
      if (this.open()) {
        this.mode.set(this.initialMode());
        this.envIndex.set(0);
        this.copied.set(false);
      }
    });
  }

  protected envPrefix(): string {
    return this.prefixes()[this.envIndex()] ?? '';
  }

  protected reportText = computed(() => {
    if (this.mode() === 'params') {
      return this.rows().map((r) => r.param).join('\n');
    }
    const repos = [...this.repos()].sort((a, b) => a.slug.localeCompare(b.slug));
    const lines: string[] = [];
    for (const repo of repos) {
      let url = '';
      if (this.mode() === 'branch') {
        url = repo.branch_url ?? '';
      } else if (this.mode() === 'pr') {
        url = repo.pr?.exists && repo.pr.url ? repo.pr.url : '';
      } else {
        const env = this.envPrefix().toLowerCase();
        const tag = env ? envTag(repo, env) : null;
        url = tag ? envTagHref(repo, env, flowRepoUrl) : '';
      }
      lines.push(url || '-------------------');
    }
    return lines.join('\n');
  });

  protected selectMode(mode: ReportMode): void {
    this.mode.set(mode);
    this.copied.set(false);
  }

  protected async copy(): Promise<void> {
    try {
      await navigator.clipboard.writeText(this.reportText());
      this.copied.set(true);
    } catch {
      this.copied.set(false);
    }
  }
}