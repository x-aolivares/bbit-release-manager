// ============================================================================
// CAPA API SIMULADA.
// ----------------------------------------------------------------------------
// Sirve los endpoints definidos en data/ con la misma firma que usaría la app
// real (method + path + query). Devuelve una Promise con latencia artificial,
// como un fetch(). Cuando llegue la integración real, solo hay que reemplazar
// este archivo por un cliente HTTP (HttpClient/Fetch) contra el backend.
// ============================================================================
window.BB = window.BB || {};

BB.api = {
  _handlers: {}, // key -> handler(query) => payload

  defaults: {
    latency_ms: 350,
  },

  on: function (method, path, handler) {
    this._handlers[method + " " + path] = handler;
  },

  fetch: function (method, path, query) {
    const key = method + " " + path;
    const handler = this._handlers[key];
    const latency = (BB.config && BB.config.sim_latency_ms) != null
      ? BB.config.sim_latency_ms
      : this.defaults.latency_ms;

    return new Promise((resolve, reject) => {
      setTimeout(() => {
        if (!handler) {
          reject(new Error("endpoint no mockeado: " + key));
          return;
        }
        // deep clone para que el caller no mute el fixture compartido.
        const payload = handler(query || {});
        resolve(JSON.parse(JSON.stringify(payload)));
      }, latency);
    });
  },
};