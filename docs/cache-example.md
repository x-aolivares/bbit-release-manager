# Estructura de datos del app

Caché persistente SQLite normalizada (reemplaza el esquema plano anterior de
5 tablas `key→JSON`). Un `request` SUCCESS vale mientras
`now - rq_created_at <= rt_ttl_seconds` del `request_type`; pasado ese lapso la
respuesta se trata como miss y se vuelve a consultar el servicio externo.

```sql
CREATE TABLE init_sesion (
    is_id INTEGER PRIMARY KEY AUTOINCREMENT,
    is_source TEXT NOT NULL,              -- origen de la consulta (rama o slug)
    is_target TEXT NOT NULL,              -- destino (rama o "all")
    is_details TEXT NOT NULL,             -- fingerprint JSON canónico de la config
    is_created_at REAL NOT NULL,          -- epoch
    is_updated_at REAL NOT NULL           -- epoch
);
CREATE INDEX IF NOT EXISTS uuidx_is_source_is_target
    ON init_sesion (is_source, is_target);

CREATE TABLE service_provider (
    sp_id INTEGER PRIMARY KEY AUTOINCREMENT,
    sp_name TEXT NOT NULL UNIQUE,
    sp_details TEXT NOT NULL              -- JSON
);

CREATE TABLE request_type (
    rt_id INTEGER PRIMARY KEY AUTOINCREMENT,
    rt_service_provider_id INTEGER NOT NULL REFERENCES service_provider(sp_id),
    rt_name TEXT NOT NULL UNIQUE,
    rt_ttl_seconds INTEGER NOT NULL DEFAULT 300,
    rt_service_url TEXT NOT NULL,
    rt_details TEXT NOT NULL,             -- JSON
    rt_created_at REAL NOT NULL,
    rt_updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rt_service_provider
    ON request_type (rt_service_provider_id);

CREATE TABLE request (
    rq_id INTEGER PRIMARY KEY AUTOINCREMENT,
    is_id INTEGER NOT NULL REFERENCES init_sesion(is_id),
    rt_id INTEGER NOT NULL REFERENCES request_type(rt_id),
    rq_status TEXT NOT NULL DEFAULT 'PENDING',   -- PENDING | SUCCESS | FAILED
    rq_details TEXT,                             -- JSON, payload de la respuesta
    rq_created_at REAL NOT NULL,                 -- epoch, clave del TTL
    rq_updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rq_type_created
    ON request (rt_id, rq_created_at DESC);      -- último hit por servicio
CREATE INDEX IF NOT EXISTS idx_rq_init_sesion
    ON request (is_id);                          -- histórico por sesión
```

Nota: los índices del DDL original (`rq_request_type_id`, `rq_init_sesion_id`)
referenciaban columnas inexistentes; se corrigieron a `rt_id` e `is_id`.

## is_details (fingerprint de sesión)

Es un JSON canónico (`json.dumps(..., sort_keys=True)`) que deduplica sesiones:
misma config = misma `init_sesion` (se reusa el `is_id`). El payload típico
visto desde el front:

```json
{
  "source_branch": "release/PER-325073",
  "target_branch": "master",
  "config": {
    "bypass_cache": false,
    "repositories": {
      "excluded": ["repo-a", "repo-b"],
      "prefixes": ["backend-", "frontend-"]
    },
    "deployment_environments": ["uat", "stgp", "prod"],
    "ssm": {"mode": "DIFF", "prefixes": ["/config", "/common"]}
  }
}
```

## Catálogo seeded

`service_provider`:
- `Bitbucket` → `{"base_url": "https://api.bitbucket.org/2.0"}`
- `CircleCi` → `{"vcs": "bb"}`

`request_type` (todos de Bitbucket, TTL 300 por defecto):

| rt_name | ttl | observación |
|---|---|---|
| get_user_repositories | 300 | listado del workspace |
| get_branch_repositories | 300 | repos que traen la rama |
| scan_release | 1800 | scan release |
| diff_ssm | 1800 | diff SSM vs master |
| get_master_params | 3600 | params de master por repo |

Las tablas legacy (`repo_cache`, `scan_cache`, `diff_cache`, `master_cache`,
`branch_repos`) se dropean al inicializar.

## Ejemplos de uso

1. Primer scan → miss, se insertan sesión + request SUCCESS.
   `SELECT is_id FROM init_sesion WHERE is_source='release/x' AND is_target='master';`
   `SELECT rq_id FROM request WHERE is_id=? AND rt_id=? ORDER BY rq_created_at DESC;`

2. Segundo scan idéntico → hit: `rq_created_at` fresco y TTL vigente.

3. `force=1` (invalidate) → se borran requests + sesión de la pareja de ramas
   (comparando `repositories`), por lo que el siguiente scan vuelve a ser miss.
   El catálogo seeds no se borra.

4. `invalidate_all()` → limpia `request` e `init_sesion`, conserva el catálogo.

## TTL

`ttl_seconds <= 0` → nunca expira. Un request `FAILED` nunca se devuelve como
hit. Entre dos SUCCESS del mismo tipo/sesión gana el de `rq_created_at` más
reciente (desempate por `rq_id` DESC).