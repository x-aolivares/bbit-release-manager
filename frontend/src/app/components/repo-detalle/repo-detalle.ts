import { Component, computed, effect, input, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';

export interface DetallePr {
  id?: number;
  url?: string;
  title?: string;
  state?: string;
}

export interface DetalleParam {
  name: string;
  type: string;
  values: Record<string, string>;
  aws_status: 'ok' | 'missing' | 'skipped';
}

export interface DetalleRepo {
  slug: string;
  project?: string;
  workspace: string;
  default_branch?: string;
  resolved_branch?: string;
  commit?: string;
  ci_project?: string | null;
  ci_vcs?: string | null;
  pr: DetallePr | null;
  tags: Record<string, string>;
  params: DetalleParam[];
}

export interface DetalleEnlace {
  label: string;
  url: string;
  external?: boolean;
}

/**
 * Panel de detalle de un repositorio: PR (crear/actualizar título), tags de
 * deploy por ambiente y los parámetros SSM del repo filtrados por región.
 * Reemplaza a la tabla principal mientras está abierto.
 */
@Component({
  selector: 'app-repo-detalle',
  templateUrl: './repo-detalle.html',
  styleUrl: './repo-detalle.scss',
  standalone: true,
  imports: [FormsModule],
})
export class RepoDetalleComponent {
  repo = input<DetalleRepo | null>(null);
  origin = input('');
  destination = input('master');
  deployEnv = input<string[]>([]);
  regions = input<string[]>([]);
  updating = input(false);
  generating = input<Record<string, boolean>>({});

  back = output<void>();
  crearPr = output<void>();
  actualizarPr = output<string>();
  generateTag = output<string>();

  selectedRegions = signal<Set<string>>(new Set());
  newPrTitle = signal('');

  constructor() {
    // Al cambiar el repo mostrado se resetean regiones y título del PR.
    effect(() => {
      const r = this.repo();
      if (r) {
        this.syncRegions();
        this.newPrTitle.set(r.pr?.title ?? '');
      }
    });
  }

  private syncRegions(): void {
    this.selectedRegions.set(new Set(this.regions()));
  }

  readonly visibleRegions = computed(() =>
    this.regions().filter((r) => this.selectedRegions().has(r)),
  );

  toggleRegion(region: string): void {
    this.selectedRegions.update((cur) => {
      const next = new Set(cur);
      if (next.has(region)) next.delete(region);
      else next.add(region);
      return next;
    });
  }

  regionChecked(region: string): boolean {
    return this.selectedRegions().has(region);
  }

  tagFor(env: string): string | undefined {
    const t = this.repo()?.tags?.[env];
    return t;
  }

  generando(env: string): boolean {
    return !!this.generating()[env];
  }

  paramValue(p: DetalleParam, region: string): string | undefined {
    return p.values?.[region];
  }

  awsBadge(p: DetalleParam): string {
    return p.aws_status === 'missing' ? 'missing' : 'ok';
  }

  /** Enlaces importantes del repositorio: Bitbucket (repo/rama/commit) y CircleCI. */
  readonly enlaces = computed<DetalleEnlace[]>(() => {
    const r = this.repo();
    if (!r) return [];
    const ws = (r.workspace || '').trim();
    const slug = r.slug;
    const links: DetalleEnlace[] = [];
    if (ws && slug) {
      links.push({ label: 'Bitbucket — repo', url: `https://bitbucket.org/${ws}/${slug}/browse`, external: true });
      const branch = (r.resolved_branch || '').trim();
      if (branch) {
        links.push({ label: `Bitbucket — rama ${branch}`, url: `https://bitbucket.org/${ws}/${slug}/branch/${encodeURIComponent(branch)}`, external: true });
      }
      const commit = (r.commit || '').trim();
      if (commit) {
        links.push({ label: `Bitbucket — commit ${commit.slice(0, 12)}`, url: `https://bitbucket.org/${ws}/${slug}/commits/${commit}`, external: true });
      }
    }
    if (r.ci_project) {
      const vcs = r.ci_vcs || 'bb';
      const base = `https://app.circleci.com/pipelines/${vcs}/${ws}?useNewPipelines=true&project=${r.ci_project}`;
      links.push({ label: 'CircleCI — pipelines', url: base, external: true });
      const branch = (r.resolved_branch || '').trim();
      if (branch) {
        links.push({ label: `CircleCI — rama ${branch}`, url: `${base}&branch=${encodeURIComponent(branch)}`, external: true });
      }
    }
    return links;
  });
}