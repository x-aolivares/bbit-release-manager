import { AppConfig } from './types';

export const APP_CONFIG: AppConfig = {
  workspace: 'acme-workspace',
  total_repos: 900,
  regions: ['qa', 'uat', 'stgp'],
  deploy_envs: ['uat', 'stgp'],
  default_region: 'uat',
  default_filters: {
    project_prefixes: ['bbit-trnxd-', 'bbit-accts-'],
    exclude: ['bbit-trnxd-backend-qa', 'bbit-trnxd-backend-db-scripts'],
  },
  default_branches: {
    origin: 'release/REP-325073',
    destination: 'master',
  },
  sim_latency_ms: 350,
};