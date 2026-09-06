# Configuración y credenciales — esquema de datos

Documento de referencia del modelo de configuración de BBit. Reemplaza la
documentación de `config/env.*` (los archivos ya no existen): los **settings**
viven en la **fila de conexión** de SQLite (`init_sesion` en `data/cache.db`)
y las **credenciales** en `service_authentication`, una fila por
(cliente, proveedor). Cada usuario/VPS tiene su **cliente** (alias → uuid).

## 1. Variables y su destino

### Bitbucket

| Variable (legacy env) | Destino hoy | Default | Descripción |
|---|---|---|---|
| `BITBUCKET_URL` | `sa_details.env.BITBUCKET_URL` | `https://bitbucket.org` | Base URL de la instancia |
| `BITBUCKET_WORKSPACE` | `sa_details.env.BITBUCKET_WORKSPACE` | `""` | Workspace (ej. `my_org_web_dev`) |
| `BITBUCKET_USERNAME` | `sa_details.env.BITBUCKET_USERNAME` | `""` | Usuario (app password) |
| `BITBUCKET_TOKEN` | `sa_details.env.BITBUCKET_TOKEN` | `""` | App password o PAT |
| `BITBUCKET_REPOS` | `settings.repos` | `[]` | Repos relevantes (vacío = todos) |
| `BITBUCKET_PROJECT_PREFIXES` | `settings.project_prefixes` | `[]` | Prefijos de slugs a escanear |
| `BITBUCKET_EXCLUDE_REPOS` | `settings.exclude_repos` | `[]` | Blacklist de repos |
| `BITBUCKET_DEFAULT_BRANCH` | `settings.default_branch` | `master` | Rama base del diff |

### CircleCI

| Variable (legacy env) | Destino hoy | Default | Descripción |
|---|---|---|---|
| `CIRCLECI_TOKEN` | `sa_details.env.CIRCLECI_TOKEN` | `""` | Token de usuario |
| `CIRCLECI_VCS` | `sa_details.env.CIRCLECI_VCS` | `bb` | VCS del slug |
| `CIRCLECI_ORG` | `sa_details.env.CIRCLECI_ORG` | `workspace` | Org del slug |

### AWS (activo desde BBIT-5, escaneo SSM en BBIT-1)

| Variable (legacy env) | Destino hoy | Default | Descripción |
|---|---|---|---|
| `AWS_PROFILE` | `sa_details.env.AWS_PROFILE` | `""` | Profile de `~/.aws` a validar por STS |
| `AWS_REGION` | `sa_details.env.AWS_REGION` | `us-east-1` | Región principal |

Las **regiones disponibles** son un bloque estructurado en el seeder del
proveedor (`service_provider.sp_details.regions`), no credenciales libres.

### Compartidas (settings)

| Variable (legacy env) | Destino hoy | Default | Descripción |
|---|---|---|---|
| `SSM_PREFIXES` | `settings.ssm_prefixes` | `["/config","/common"]` | Prefijos de rutas SSM detectadas |
| `DEPLOY_PREFIXES` | `settings.deploy_prefixes` | `["uat","stgp","prod"]` | Prefijos de tags/deploy |

## 2. Estructura en base de datos (BBIT-5)

### `client` — identidad por usuario/máquina

```sql
CREATE TABLE IF NOT EXISTS client (
    c_id TEXT PRIMARY KEY,            -- uuid (no adivinable, no autoincremental)
    c_alias TEXT NOT NULL UNIQUE,     -- ejemplo: 'local', 'compilador-2'
    c_details TEXT NOT NULL,          -- JSON libre
    c_created_at REAL NOT NULL,
    c_updated_at REAL NOT NULL
);
```

### `service_authentication` — credenciales por (cliente, proveedor)

```sql
CREATE TABLE IF NOT EXISTS service_authentication (
    sa_id INTEGER PRIMARY KEY AUTOINCREMENT,
    c_id TEXT NOT NULL,               -- = client.c_id
    sp_id INTEGER NOT NULL,           -- = service_provider.sp_id
    sa_details TEXT NOT NULL,         -- JSON canónico (abajo)
    sa_created_at REAL NOT NULL,
    sa_updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS uuidx_sa_c_id_sp_id
    ON service_authentication (c_id, sp_id);
```

**JSON canónico de `sa_details`** (guarda vía `_session_fingerprint`):

```json
{
  "env": {
    "BITBUCKET_URL": "https://bitbucket.org",
    "BITBUCKET_WORKSPACE": "my_org_web_dev",
    "BITBUCKET_TOKEN": "…",
    "CIRCLECI_ORG": "my_org_web_dev",
    "AWS_PROFILE": "my-org-dev"
  },
  "expires_at": 1789000000
}
```

- `env` son las claves canónicas `BITBUCKET_…` / `CIRCLECI_…` / `AWS_…`; los
  valores vacíos se descartan antes de guardar.
- `expires_at` es **opcional**: ni Bitbucket ni CircleCI exponen fecha de
  vencimiento. Si se setea, la UI/CLI muestran `vence pronto`/`vencido`; sin
  ella, se hace un probe por inicio de sesión.

### `service_provider` (catálogo, seeded)

Proveedores: `Bitbucket`, `CircleCi`, `AWS`. El seeder hace **upsert** de
`sp_details` (idem `request_type`): una edición manual se refresca al reabrir.

### `request_type` y `request` (estándar del equipo)

- Sin `REFERENCES` (las relaciones se conocen en código, no en el DDL).
- La columna de referencia usa el **mismo nombre que la PK** que referencia:
  `request_type.sp_id`, `request.is_id`, `request.rt_id`.
- Migración idempotente: `_migrate_schema()` renombra
  `rt_service_provider_id` → `sp_id` en DBs existentes y recrea
  `idx_rt_sp_id`.

### Fila de conexión (settings, NO credenciales)

- `is_source = 'config'`, `is_target = 'connection'`, JSON:
  `{"bypass_cache": false, "settings": {…}}`
- Queda **fuera** de `DELETE /api/cache` y de `clear_authentications`
  (limpiar historial no borra credenciales ni settings).
- `DELETE /api/session?delete_credentials=1` borra SOLO
  `service_authentication` del cliente activo (la identidad `client` queda).

## 3. Cliente activo y migraciones

- **Cliente activo**: marker local `data/client_id` (sobrescribible con
  `BBIT_CLIENT_MARKER` en tests) o `BBIT_CLIENT_ID`. `bbit login --alias`
  o `POST /api/session {alias}` crean/cambian el cliente y lo vuelven default.
- **Migración `config/env.base` → SA** (uno-shot, solo DB real): importa el
  histórico a `service_authentication` del cliente `local`, mergea settings en
  la fila de conexión y borra el archivo.
- **Migración `connection.credentials` → SA** (uno-shot): divide la fila de
  conexión con credenciales (esquema BBIT-3) en `service_authentication` por
  proveedor; la fila conserva solo settings.

## 4. Superficie externa

- CLI: `bbit login [--alias] [--workspace] [--token] [--circleci-token]
  [--aws-profile] [--aws-region]` — carrusel Bitbucket → CircleCI → AWS, cada
  paso valida contra su API antes de guardar. `bbit config` muestra cliente,
  estado por servicio y vencimientos.
- API: `GET /api/client`, `POST /api/auth/{service}` (valida y guarda),
  `POST /api/auth/{service}/validate` (valida sin guardar), `GET/POST/DELETE
  /api/session` con `client_alias` + `services` (stored/expires/warning).
- UI de configuración por servicios (carrusel con "ahora validando
  credenciales", dropdown del cliente): **BBIT-6** (frontend).