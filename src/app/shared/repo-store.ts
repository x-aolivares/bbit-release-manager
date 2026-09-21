import { Injectable } from '@angular/core';
import { Pr } from './types';

interface CachedPr {
  pr: Pr;
  created_at: number;
}

@Injectable({ providedIn: 'root' })
export class RepoStore {
  readonly TTL_MS = 24 * 60 * 60 * 1000;
  private prs = new Map<string, CachedPr>();
  private repoTags = new Map<string, string[]>();

  constructor() {
    this.seed();
  }

  private key(slug: string, origin: string, destination: string): string {
    return slug + '|' + origin + '|' + destination;
  }

  getPr(slug: string, origin: string, destination: string): Pr | null {
    const key = this.key(slug, origin, destination);
    const entry = this.prs.get(key);
    if (!entry) return null;
    if (Date.now() - entry.created_at > this.TTL_MS) {
      this.prs.delete(key);
      return null;
    }
    return structuredClone(entry.pr);
  }

  setPr(slug: string, origin: string, destination: string, pr: Pr): void {
    this.prs.set(this.key(slug, origin, destination), {
      pr: structuredClone(pr),
      created_at: Date.now(),
    });
  }

  getRepoTags(slug: string): string[] {
    return structuredClone(this.repoTags.get(slug) ?? []);
  }

  setRepoTags(slug: string, tags: string[]): void {
    const clean = (tags ?? []).map((t) => String(t).toLowerCase());
    if (clean.length) this.repoTags.set(slug, structuredClone(clean));
    else this.repoTags.delete(slug);
  }

  private seed(): void {
    const seedTags: Record<string, string[]> = {
      'bbit-trnxd-orders-api': ['fargate', 'batch'],
      'bbit-trnxd-orders-web': ['fargate'],
      'bbit-trnxd-orders-batch': ['batch'],
      'bbit-trnxd-orders-ingest': ['step-function'],
      'bbit-trnxd-orders-reporting': ['batch'],
      'bbit-accts-catalog-search': ['fargate'],
      'bbit-accts-catalog-ingest': ['step-function'],
      'bbit-accts-catalog-admin': ['fargate'],
      'bbit-accts-catalog-images': ['workflow'],
      'bbit-accts-payments-core': ['fargate'],
      'bbit-accts-payments-gateway': ['fargate', 'workflow'],
      'bbit-accts-payments-refunds': ['step-function'],
      'bbit-accts-shipping-tracker': ['fargate'],
      'bbit-accts-identity-auth': ['workflow'],
      'bbit-accts-notifications': ['step-function', 'fargate'],
      'bbit-trnxd-backend-db-scripts': ['batch'],
    };
    for (const [slug, tags] of Object.entries(seedTags)) {
      this.setRepoTags(slug, tags);
    }

    this.setPr('bbit-trnxd-orders-api', 'release/REP-325073', 'master', {
      id: 42,
      title: 'Fix order processing bug',
      url: 'https://bitbucket.org/acme-workspace/bbit-trnxd-orders-api/pull-requests/42',
      state: 'OPEN',
    });
    this.setPr('bbit-accts-catalog-search', 'release/REP-325073', 'master', {
      id: 87,
      title: 'Catalog: release REP-325073',
      url: 'https://bitbucket.org/acme-workspace/bbit-accts-catalog-search/pull-requests/87',
      state: 'OPEN',
    });
  }
}