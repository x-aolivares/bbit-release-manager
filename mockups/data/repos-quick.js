// ============================================================================
// ENDPOINT: GET /api/repos-quick
// ----------------------------------------------------------------------------
// bbit_release/web/api/repos.py:1296 — devuelve repos del workspace con
// filtros aplicados (project_prefixes + exclude). Si se pasan origin +
// destination, resuelve `branch_state` ("found" | "not_found") por repo contra
// la cache de branch_refs (BBIT-58/46).
//
// Query (mock): { origin, destination, project_prefixes, exclude }
// Respuesta:    { repos: [{ slug, name, workspace, default_branch,
//                          resolved_branch, branch_state, tags }], count }
//
// tags: flujo al que pertenece el repo (fargate, step-function, workflow,
// batch…). Siempre en minúsculas. En la app real vive en repositories.r_details
// y se parsea a minúsculas al guardar. Acá la fuente de verdad es el almacén
// BB.__repo_store (setRepoTags/getRepoTags); el seed lo siembra repositories.js.
// ============================================================================
window.BB = window.BB || {};

const __repos_repo_table = [
  { slug: "bbit-trnxd-orders-api", name: "Orders API", branch_state: "found", project: "Orders Platform" },
  { slug: "bbit-trnxd-orders-web", name: "Orders Web", branch_state: "found" },
  { slug: "bbit-trnxd-orders-batch", name: "Orders Batch", branch_state: "not_found" },
  { slug: "bbit-trnxd-orders-ingest", name: "Orders Ingest", branch_state: "found" },
  { slug: "bbit-trnxd-orders-reporting", name: "Orders Reporting", branch_state: "not_found" },
  { slug: "bbit-accts-catalog-search", name: "Catalog Search", branch_state: "found" },
  { slug: "bbit-accts-catalog-ingest", name: "Catalog Ingest", branch_state: "found" },
  { slug: "bbit-accts-catalog-admin", name: "Catalog Admin", branch_state: "not_found" },
  { slug: "bbit-accts-catalog-images", name: "Catalog Images", branch_state: "found" },
  { slug: "bbit-accts-payments-core", name: "Payments Core", branch_state: "found" },
  { slug: "bbit-accts-payments-gateway", name: "Payments Gateway", branch_state: "found" },
  { slug: "bbit-accts-payments-refunds", name: "Payments Refunds", branch_state: "not_found" },
  { slug: "bbit-accts-shipping-tracker", name: "Shipping Tracker", branch_state: "found" },
  { slug: "bbit-accts-identity-auth", name: "Identity Auth", branch_state: "found" },
  { slug: "bbit-accts-notifications", name: "Notifications", branch_state: "found" },
  { slug: "bbit-trnxd-backend-db-scripts", name: "Backend DB Scripts", branch_state: "found" },
];

BB.api.on("GET", "/api/repos-quick", function (query) {
  const origin = (query.origin || "").trim();
  const destination = (query.destination || "").trim();
  const prefixes = (query.project_prefixes || "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
  const exclude = (query.exclude || "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);

  const repos = __repos_repo_table
    .filter((r) => !exclude.includes(r.slug))
    .filter((r) => !prefixes.length || prefixes.some((p) => r.slug.startsWith(p)))
    .map((r) => {
      const base = {
        slug: r.slug,
        name: r.name,
        workspace: BB.config.workspace,
        default_branch: "master",
        resolved_branch: r.branch_state === "found" ? origin : "",
        branch_state: origin && destination ? r.branch_state : "found",
        tags: BB.__repo_store ? BB.__repo_store.getRepoTags(r.slug) : [],
      };
      // PR cacheado en `repositories` para esta combinación de ramas (TTL 24h):
      // la primera consulta lo devuelve sin llamar a Bitbucket. Si expiró o no
      // existe, `pr` queda null y el front vuelve a ofrecer "Crear PR".
      base.pr = (BB.__repo_store && origin && destination)
        ? BB.__repo_store.getPr(r.slug, origin, destination)
        : null;
      return base;
    });

  return { repos, count: repos.length };
});