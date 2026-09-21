// ============================================================================
// ENDPOINT: GET /api/flow
// ----------------------------------------------------------------------------
// bbit_release/web/api/repos.py:1369 — análisis completo del workspace para
// (origin, destination): por repo incluye PR existente y tags de deploy.
// (El diff de archivos y el escaneo SSM se resuelven en endpoints aparte.)
//
// Query (mock): { origin, destination, with_diff, with_tags }
// Respuesta:    { origin, destination, repos: [{ slug, pr, tags }] }
// ============================================================================
window.BB = window.BB || {};

const __flow_prs = {
  "orders-api": { id: 42, title: "Fix order processing bug", url: "https://bitbucket.org/acme-workspace/orders-api/pull-requests/42" },
};

const __flow_tags = {
  "orders-api": { uat: "uat-1201", stgp: "stgp-1188", prod: null },
};

BB.api.on("GET", "/api/flow", function (query) {
  const origin = (query.origin || "").trim();
  const destination = (query.destination || "").trim();
  const envs = (BB.config && BB.config.environments && BB.config.environments.deploy) || ["uat", "stgp", "prod"];

  // Simula que el leak del flujo real es lento para los que no tienen PR.
  const repos = Object.keys({ ...__flow_prs, ...__flow_tags }).map((slug) => ({
    slug,
    pr: __flow_prs[slug] || null,
    tags: envs.reduce((acc, env) => {
      acc[env] = (__flow_tags[slug] && __flow_tags[slug][env]) || null;
      return acc;
    }, {}),
    has_diff: query.with_diff !== false,
  }));

  return {
    origin,
    destination,
    repos,
    total: BB.config.total_repos,
  };
});