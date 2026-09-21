// ============================================================================
// CONFIG GLOBAL DEL MOCK (fixture del estado de sesión en la app real).
// ============================================================================
window.BB = window.BB || {};

BB.config = {
  workspace: "acme-workspace",
  total_repos: 900,

  // Regiones/ambientes: fuente única para componentes y endpoints.
  regions: ["qa", "uat", "stgp"],
  deploy_envs: ["uat", "stgp"],
  default_region: "uat",

  // Filtros por defecto del buscador (los únicos filtros del paradigma).
  default_filters: {
    project_prefixes: ["bbit-trnxd-", "bbit-accts-"],
    exclude: ["bbit-trnxd-backend-qa", "bbit-trnxd-backend-db-scripts"],
  },

  default_branches: {
    origin: "release/REP-325073",
    destination: "master",
  },

  // Latencia simulada de la capa api (ms).
  sim_latency_ms: 350,
};