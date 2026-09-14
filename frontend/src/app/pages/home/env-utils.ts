/**
 * Pure helpers for repo deploy/env columns.
 *
 * Extracted from Home to be reused by ReposTableComponent and
 * ReportModalComponent without duplicating formatting logic.
 */

export interface EnvDeploy {
  workflow: string;
  status: string;
  url: string;
  job?: string;
}

export interface EnvRepo {
  slug: string;
  workspace: string;
  branch_url: string;
  ci_project?: string | null;
  ci_vcs?: string | null;
  deploys?: Record<string, EnvDeploy | null>;
  match_tag?: Record<string, string | null>;
}

/** El tag efectivo de un repo para un ambiente (match_tag) o null. */
export function envTag(repo: EnvRepo, prefix: string): string | null {
  return repo.match_tag?.[prefix.toLowerCase()] ?? null;
}

/** URL a la que enlaza el badge de un ambiente (deploy, pipeline o tag). */
export function envTagHref(repo: EnvRepo, prefix: string, repoUrl: (r: EnvRepo) => string): string {
  const tag = envTag(repo, prefix);
  if (!tag) return repoUrl(repo);
  const deploy = repo.deploys?.[prefix.toLowerCase()];
  if (deploy?.url) {
    return deploy.url;
  }
  if (repo.ci_project) {
    const vcs = repo.ci_vcs || 'bb';
    return `https://app.circleci.com/pipelines/${vcs}/${repo.workspace}?useNewPipelines=true&project=${repo.ci_project}&filter=${encodeURIComponent(`git_tag:equals:${tag}`)}`;
  }
  return `${repoUrl(repo)}/src/${encodeURIComponent(tag)}`;
}

/** Tooltip del badge de ambiente. */
export function envTagTitle(repo: EnvRepo, prefix: string): string {
  const tag = envTag(repo, prefix) ?? '';
  const deploy = repo.deploys?.[prefix.toLowerCase()];
  if (!deploy) {
    return `${tag} · sin deploy validado`;
  }
  const bits = [tag, deploy.workflow];
  if (deploy.job) bits.push(deploy.job);
  bits.push(deploy.status);
  return bits.join(' · ');
}

/**
 * Badge visual de un ambiente: label + clase CSS según el estado del deploy.
 * `null` = no hay tag → el render se encarga (botón "generate tag").
 */
export function envTagBadge(repo: EnvRepo, prefix: string): { label: string; cls: string } | null {
  const tag = envTag(repo, prefix);
  if (!tag) return null;
  const deploy = repo.deploys?.[prefix.toLowerCase()];
  if (!deploy) {
    return { label: `${tag} · pendiente de aprobación`, cls: 'bb-deploy--pending' };
  }
  const s = deploy.status;
  if (s === 'success') {
    return { label: `${tag} · ok`, cls: 'bb-deploy--ok' };
  }
  if (s === 'failed' || s === 'error') {
    return { label: `${tag} · falló`, cls: 'bb-deploy--danger' };
  }
  if (s === 'on_hold') {
    return { label: `${tag} · esperando aprobación`, cls: 'bb-deploy--pending' };
  }
  if (s === 'blocked' || s === 'canceled') {
    return { label: `${tag} · ${s}`, cls: 'bb-deploy--pending' };
  }
  if (s === 'running' || s === 'queued' || s === 'not_run') {
    return { label: `${tag} · ${s}`, cls: 'bb-deploy--pending' };
  }
  return { label: `${tag} · ${s}`, cls: 'bb-deploy--pending' };
}

/** `true` si el nombre del tag parece `{env}-{id}` para algún ambiente. */
export function isEnvTag(name: string, prefixes: string[]): boolean {
  const n = name.toLowerCase();
  return prefixes.some((p) => new RegExp(`^${p.toLowerCase()}-\\d+$`).test(n));
}

/** Ambientes del listado que todavía no tienen tag para ese repo. */
export function missingEnvs(repo: EnvRepo, prefixes: string[]): string[] {
  return prefixes.filter((p) => !envTag(repo, p));
}

/** `true` si al repo le falta al menos un tag de ambiente. */
export function missingTagFor(repo: EnvRepo, prefixes: string[]): boolean {
  return prefixes.some((p) => !envTag(repo, p));
}