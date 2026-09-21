export type BranchState = 'found' | 'not_found';
export type RepoCheckState = 'pending' | 'checking' | 'found' | 'not_found';
export type AwsStatus = 'ok' | 'missing';

export interface Pr {
  id: number;
  title: string;
  url: string;
  state?: string;
}

export interface Repo {
  slug: string;
  name: string;
  workspace: string;
  default_branch: string;
  resolved_branch: string;
  branch_state: BranchState;
  tags: string[];
  project?: string;
  pr?: Pr | null;
}

export interface FlowRepo {
  slug: string;
  pr: Pr | null;
  tags: Record<string, string | null>;
  has_diff: boolean;
}

export interface FlowResponse {
  origin: string;
  destination: string;
  repos: FlowRepo[];
  total: number;
}

export interface SsmParam {
  name: string;
  type: string;
  aws_status: AwsStatus;
  values: Record<string, string>;
}

export interface Environment {
  code: string;
  label: string;
}

export interface AppConfig {
  workspace: string;
  total_repos: number;
  regions: string[];
  deploy_envs: string[];
  default_region: string;
  default_filters: { project_prefixes: string[]; exclude: string[] };
  default_branches: { origin: string; destination: string };
  sim_latency_ms: number;
}