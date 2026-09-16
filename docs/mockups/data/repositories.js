// ============================================================================
// ALMACÉN MOCK: tabla `repositories` (r_details)
// ----------------------------------------------------------------------------
// Simula la tabla SQLite donde la app real persiste por repo + combinación de
// ramas (origin → destination) el último PR creado, con su timestamp. Eso
// permite que la primera consulta (GET /api/repos-quick) devuelva el PR sin
// volver a llamar a la API de Bitbucket, mientras el dato no haya expirado.
//
// TTL: 24 hs. Al expirar, GET /api/repos-quick ya no devuelve el PR y el front
// vuelve a ofrecer "Crear PR" (iría contra el API real de Bitbucket).
//
// Este archivo NO registra endpoints: es el "backend" compartido que usan
// repos-quick.js (lectura) y pr.js (escritura). Debe cargar antes que ambos.
// ============================================================================
window.BB = window.BB || {};

BB.__repo_store = BB.__repo_store || {
  TTL_MS: 24 * 60 * 60 * 1000,
  prs: {}, // key "slug|origin|destination" -> { pr, created_at }

  _key(slug, origin, destination) {
    return slug + "|" + origin + "|" + destination;
  },

  // Devuelve el PR cacheado si existe y no expiró (TTL 24h); si expiró, lo
  // descarta para que el front consulte la API de Bitbucket de nuevo.
  getPr(slug, origin, destination) {
    const k = this._key(slug, origin, destination);
    const e = this.prs[k];
    if (!e) return null;
    if (Date.now() - e.created_at > this.TTL_MS) {
      delete this.prs[k];
      return null;
    }
    return JSON.parse(JSON.stringify(e.pr));
  },

  // Upsert: guarda (o actualiza el timestamp de) el PR para la combinación.
  setPr(slug, origin, destination, pr) {
    this.prs[this._key(slug, origin, destination)] = {
      pr: JSON.parse(JSON.stringify(pr)),
      created_at: Date.now(),
    };
  },
};

// Seed de demostración: dos repos ya tienen PR para release/REP-325073 → master,
// así la primera carga los muestra sin ir a Bitbucket (cacheado, TTL fresco).
BB.__repo_store.setPr(
  "bbit-trnxd-orders-api",
  "release/REP-325073",
  "master",
  { id: 42, title: "Fix order processing bug", url: "https://bitbucket.org/acme-workspace/bbit-trnxd-orders-api/pull-requests/42", state: "OPEN" }
);
BB.__repo_store.setPr(
  "bbit-accts-catalog-search",
  "release/REP-325073",
  "master",
  { id: 87, title: "Catalog: release REP-325073", url: "https://bitbucket.org/acme-workspace/bbit-accts-catalog-search/pull-requests/87", state: "OPEN" }
);