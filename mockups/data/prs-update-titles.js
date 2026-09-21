// ============================================================================
// ENDPOINT: POST /api/prs/update-titles
// ----------------------------------------------------------------------------
// bbit_release/web/api/repos.py:1851 — actualiza el título de todos los PRs
// de origin → destination en los repos indicados (uso masivo).
//
// Query (mock): { repos: [slug], origin, destination, title, project_prefixes, exclude }
// Respuesta:    { repos: [{ slug, id, title, url }] }
// ============================================================================
window.BB = window.BB || {};

BB.api.on("POST", "/api/prs/update-titles", function (query) {
  const origin = query.origin;
  const destination = query.destination;
  const title = query.title || "Release: " + origin + " → " + destination;
  const repos = query.repos || [];

  return {
    repos: repos.map((slug) => {
      const id = 100 + slug.length * 7;
      return {
        slug,
        id,
        title,
        url: "https://bitbucket.org/acme-workspace/" + slug + "/pull-requests/" + id,
      };
    }),
  };
});