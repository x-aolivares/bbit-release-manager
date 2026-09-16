// ============================================================================
// ENDPOINT: GET /api/ssm/environments
// ----------------------------------------------------------------------------
// bbit_release/web/api/ssm.py:381 — regiones/ambientes contra los que se
// comparan los parámetros SSM.
//
// Query (mock): {}
// Respuesta:    { environments: [{ code, label }], deploy: [..],
//                 default_region: "uat" }
// ============================================================================
window.BB = window.BB || {};

BB.api.on("GET", "/api/ssm/environments", function () {
  const regions = BB.config.regions;
  const labels = { qa: "QA", uat: "UAT", stgp: "STGP" };
  return {
    environments: regions.map((code) => ({ code, label: labels[code] || code.toUpperCase() })),
    deploy: BB.config.deploy_envs,
    default_region: BB.config.default_region,
  };
});