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
  const pr = {
    id,
    title,
    url: "https://bitbucket.org/acme-workspace/" + slug + "/pull-requests/" + id,
    state: "OPEN",
  };

  // Persistir en la tabla `repositories` (mock): el PR queda cacheado para
  // "slug + origin→dest" con TTL 24h, así la próxima consulta de repos lo
  // devuelve sin volver a contactar a Bitbucket.
  if (BB.__repo_store) BB.__repo_store.setPr(slug, origin, destination, pr);

  return {
    slug,
    origin,
    destination,
    pr,
    cached: true,
    cached_ttl_hours: BB.__repo_store ? BB.__repo_store.TTL_MS / 3600000 : 24,
  };
});