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
  repoTags: {}, // slug -> [tags de flujo, minúsculas]. viven en r_details real.

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

  // Tags de flujo por repo (tabla `repositories.r_details` en la app real).
  getRepoTags(slug) {
    return JSON.parse(JSON.stringify(this.repoTags[slug] || []));
  },

  // Asigna la lista COMPLETA de tags del repo (UPSERT/REEMPLAZO total).
  setRepoTags(slug, tags) {
    const clean = (tags || []).map((t) => String(t).toLowerCase());
    if (clean.length) this.repoTags[slug] = JSON.parse(JSON.stringify(clean));
    else delete this.repoTags[slug];
  },
};

// Seed de tags de flujo (estado inicial de la "BD"). Es la fuente de verdad que
// usa GET /api/repos-quick: limpiar/aplicar tags escribe acá vía setRepoTags.
BB.__repo_store.setRepoTags("bbit-trnxd-orders-api", ["fargate", "batch"]);
BB.__repo_store.setRepoTags("bbit-trnxd-orders-web", ["fargate"]);
BB.__repo_store.setRepoTags("bbit-trnxd-orders-batch", ["batch"]);
BB.__repo_store.setRepoTags("bbit-trnxd-orders-ingest", ["step-function"]);
BB.__repo_store.setRepoTags("bbit-trnxd-orders-reporting", ["batch"]);
BB.__repo_store.setRepoTags("bbit-accts-catalog-search", ["fargate"]);
BB.__repo_store.setRepoTags("bbit-accts-catalog-ingest", ["step-function"]);
BB.__repo_store.setRepoTags("bbit-accts-catalog-admin", ["fargate"]);
BB.__repo_store.setRepoTags("bbit-accts-catalog-images", ["workflow"]);
BB.__repo_store.setRepoTags("bbit-accts-payments-core", ["fargate"]);
BB.__repo_store.setRepoTags("bbit-accts-payments-gateway", ["fargate", "workflow"]);
BB.__repo_store.setRepoTags("bbit-accts-payments-refunds", ["step-function"]);
BB.__repo_store.setRepoTags("bbit-accts-shipping-tracker", ["fargate"]);
BB.__repo_store.setRepoTags("bbit-accts-identity-auth", ["workflow"]);
BB.__repo_store.setRepoTags("bbit-accts-notifications", ["step-function", "fargate"]);
BB.__repo_store.setRepoTags("bbit-trnxd-backend-db-scripts", ["batch"]);

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