import { Component, EventEmitter, Input, input, output, Output } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { IonSpinner } from '@ionic/angular/ion-spinner';
import { repoUrl as flowRepoUrl } from '../../pages/home/flow-utils';
import type { RepoSortKey, RepoSortDir } from '../../pages/home/flow-utils';
import {
  envTagBadge,
  envTagHref,
  envTagTitle,
} from '../../pages/home/env-utils';

export interface ReposPrInfo {
  exists: boolean;
  url?: string;
  title?: string;
  state?: string;
}

export interface ReposDeploy {
  workflow: string;
  status: string;
  created_at: string;
  url: string;
  job?: string;
  approval?: string;
}

export interface ReposRow {
  slug: string;
  name: string;
  workspace: string;
  branch_url: string;
  commit: string;
  pr: ReposPrInfo;
  tags: { name: string; deploy: ReposDeploy | null }[];
  deploys: Record<string, ReposDeploy | null>;
  match_tag: Record<string, string | null>;
  error?: string | null;
  ci_project?: string | null;
  ci_vcs?: string | null;
}

export interface ReposSortEvent {
  key: RepoSortKey;
  env?: string;
}

@Component({
  selector: 'app-repos-table',
  templateUrl: './repos-table.html',
  styleUrl: './repos-table.scss',
  standalone: true,
  imports: [FormsModule, IonSpinner],
})
export class ReposTableComponent {
  repos = input.required<ReposRow[]>();
  loadedPrefixes = input.required<string[]>();
  prefixCols = input('');
  destination = input('');
  origin = input('');

  ciConfigured = input(true);
  ciError = input<string | null>(null);
  creatingPr = input<string | null>(null);
  tagging = input(false);
  taggingRepo = input<string | null>(null);
  reposLoading = input(false);
  syncingPrs = input(false);
  failedRepos = input<ReposRow[]>([]);

  sortKey = input<RepoSortKey>('name');
  sortEnv = input<string | null>(null);
  sortDir = input<RepoSortDir>('asc');

  @Input() prTitle = '';
  @Output() prTitleChange = new EventEmitter<string>();

  sortEvent = output<ReposSortEvent>();
  createPr = output<ReposRow>();
  generateTag = output<{ repo: ReposRow; env: string }>();
  generateTagsAll = output<void>();
  syncPrs = output<'create' | 'update'>();
  reportRequested = output<void>();
  refresh = output<void>();
  retryFailed = output<void>();

  protected badge(repo: ReposRow, env: string): { label: string; cls: string } | null {
    return envTagBadge(repo, env);
  }

  protected href(repo: ReposRow, env: string): string {
    return envTagHref(repo, env, flowRepoUrl);
  }

  protected title(repo: ReposRow, env: string): string {
    return envTagTitle(repo, env);
  }

  protected arrow(key: RepoSortKey, env?: string): string {
    if (this.sortKey() !== key || this.sortEnv() !== (env ?? null)) {
      return '';
    }
    return this.sortDir() === 'asc' ? '↑' : '↓';
  }

  protected onSort(key: RepoSortKey, env?: string): void {
    this.sortEvent.emit({ key, env });
  }

  protected prNumber(url: string): string {
    return url.split('pull-requests/')[1]?.split('/')[0] ?? 'abierto';
  }

  protected onGenerateTag(repo: ReposRow, env: string): void {
    this.generateTag.emit({ repo, env });
  }
}