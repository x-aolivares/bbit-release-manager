import { Component, computed, inject, signal } from '@angular/core';
import { ReleaseStore } from '../shared/release-store';
import { APP_CONFIG } from '../shared/app-config';

@Component({
  imports: [],
  selector: 'app-repository-detail',
  templateUrl: './repository-detail.html',
})
export class RepositoryDetail {
  readonly store = inject(ReleaseStore);

  readonly workspace = APP_CONFIG.workspace;
  readonly totalRepos = APP_CONFIG.total_repos;
  readonly envs = APP_CONFIG.deploy_envs;

  excelPct = signal(0);
  excelStatus = signal('');
  excelTimer: ReturnType<typeof setInterval> | null = null;

  readonly repo = computed(() => this.store.repos().find((r) => r.slug === this.store.detailSlug()));

  readonly gridColumns = computed(() => {
    const cols = this.store.detailSelectedRegions();
    return `2fr 1fr ${cols.map(() => '1.2fr').join(' ')} 0.8fr`;
  });

  isGeneratingPr(slug: string | null): boolean {
    return slug != null && this.store.generating().has(slug + '|pr');
  }

  isGeneratingTag(slug: string | null, env: string): boolean {
    return slug != null && this.store.generating().has(slug + '|tag:' + env);
  }

  async onUpdatePrTitle(slug: string, title: string): Promise<void> {
    void (await this.store.updatePrTitle(slug, title));
  }

  onGenerateTag(slug: string, env: string): void {
    void this.store.generateTag(slug, env);
  }

  onToggleRegion(code: string): void {
    this.store.toggleRegion(code, !this.store.selectedRegions().has(code));
  }

  onExcel(): void {
    const btn = document.getElementById('excelButton') as HTMLButtonElement | null;
    if (btn?.disabled) return;
    if (btn) btn.disabled = true;
    const progress = document.getElementById('excelProgress');
    if (progress) progress.style.display = 'block';
    let pct = 0;
    if (this.excelTimer) clearInterval(this.excelTimer);
    this.excelTimer = setInterval(() => {
      pct = Math.min(100, pct + 3);
      this.excelPct.set(pct);
      this.excelStatus.set(
        pct >= 100 ? 'Excel listo: ssm-params-workspace-2026-09-15.xlsx' : `Consulta completa del workspace… ${pct}%`,
      );
      if (pct >= 100) {
        if (this.excelTimer) clearInterval(this.excelTimer);
      }
    }, 250);
  }
}