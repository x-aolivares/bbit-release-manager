// ============================================================================
// ENDPOINT: POST /api/pr
// ----------------------------------------------------------------------------
// bbit_release/web/api/repos.py:1795 — crea un pull request de
// origin → destination en el repo dado.
//
// Query (mock): { repo, origin, destination, title }
// Respuesta:    { slug, origin, destination, pr: { id, title, url, state } }
// ============================================================================
window.BB = window.BB || {};

BB.api.on("POST", "/api/pr", function (query) {
  const slug = query.repo;
  const origin = query.origin;
  const destination = query.destination;
  const title = query.title || "Release: " + origin + " → " + destination;

  const id = 100 + slug.length * 7;
  return {
    slug,
    origin,
    destination,
    pr: {
      id,
      title,
      url: "https://bitbucket.org/acme-workspace/" + slug + "/pull-requests/" + id,
      state: "OPEN",
    },
  };
});