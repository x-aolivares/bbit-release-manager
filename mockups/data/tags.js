// ============================================================================
// ENDPOINT: POST /api/tags
// ----------------------------------------------------------------------------
// bbit_release/web/api/repos.py:1879 — genera tags de deploy en el repo para
// los prefijos indicados (se corre CircleCI para que existan).
//
// Query (mock): { repo, origin, destination, prefixes: [..] }
// Respuesta:    { repo, origin, destination, tags: { prefix: "prefix-<n>" } }
// ============================================================================
window.BB = window.BB || {};

BB.api.on("POST", "/api/tags", function (query) {
  const slug = query.repo;
  const prefixes = query.prefixes || [];
  const origin = query.origin || "release/REP-325073";

  // Tag derivado de la resolución de la rama origen para que sea estable.
  const base = (slug.length * 37 + origin.length * 911) % 900 + 100;
  const tags = {};
  for (const p of prefixes) {
    tags[p] = p + "-" + base;
  }

  return {
    repo: slug,
    origin,
    destination: query.destination || "master",
    tags,
  };
});