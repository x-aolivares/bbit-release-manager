// ============================================================================
// ENDPOINT: GET /api/ssm/values
// ----------------------------------------------------------------------------
// bbit_release/web/api/ssm.py:49 — valores de parámetros SSM de un repo.
//
// Query (mock): { repo, environments: [..] }
// Respuesta:    {
//   repo,
//   params: [{ name, type, aws_status, values: { env: valor } }],
// }
//   aws_status: "ok" | "missing"
// ============================================================================
window.BB = window.BB || {};

const __ssm_params = {
  "orders-api": [
    { name: "/config/orders/api/DB_URL", type: "nuevo", aws_status: "ok", values: { qa: "db-qa", uat: "db-uat", stgp: "db-stgp", } },
    { name: "/config/orders/api/API_KEY", type: "reutilizado", aws_status: "ok", values: { qa: "ak-qa", uat: "ak-uat", stgp: "ak-stgp", } },
    { name: "/common/orders/JWT_SECRET", type: "nuevo", aws_status: "missing", values: { qa: "jwt-qa", uat: "", stgp: "", } },
  ],
  "orders-web": [
    { name: "/config/orders/web/APP_PORT", type: "nuevo", aws_status: "ok", values: { qa: "8080", uat: "8080", stgp: "8080", prod: "8080" } },
    { name: "/common/orders/JWT_SECRET", type: "reutilizado", aws_status: "missing", values: { qa: "jwt-qa", uat: "", stgp: "", prod: "" } },
  ],
};

function __ssm_default_params(slug) {
  return [
    { name: "/config/" + slug + "/DB_HOST", type: "nuevo", aws_status: "ok", values: { qa: "host-qa", uat: "host-uat", stgp: "host-stgp" } },
    { name: "/config/" + slug + "/API_TIMEOUT", type: "nuevo", aws_status: "ok", values: { qa: "3000", uat: "3000", stgp: "3000" } },
    { name: "/common/" + slug + "/JWT_SECRET", type: "reutilizado", aws_status: "missing", values: { qa: "jwt-qa", uat: "", stgp: "" } },
  ];
}

BB.api.on("GET", "/api/ssm/values", function (query) {
  const slug = query.repo;
  const params = __ssm_params[slug] || __ssm_default_params(slug);

  return {
    repo: slug,
    params,
  };
});