# bbit-release-manager

CLI + web para revisar ramas y parámetros SSM contra la API de Bitbucket.
Replica la arquitectura de `yappy-cli-manager`: CLI en Python (Typer/Rich),
backend FastAPI y frontend Angular 22 + Ionic 9.

## Fase 1 (actual): bbit web + bbit login + bbit session

```bash
pip install -e .
bbit login               # Guarda credenciales (bitbucket, circle) en la fila de conexión
bbit session             # Valida token/acceso a Bitbucket y lista los repos
bbit config              # Muestra la conexión persistida en data/cache.db
bbit web                 # Sirve el frontend compilado + API (modo prod local)
bbit web --dev           # uvicorn --reload (backend) + ng serve (HMR frontend)
bbit web --no-browser
```

`bbit session` abre una sesión real contra la API REST de **Bitbucket Cloud**
(`api.bitbucket.org/2.0`): valida el token, identifica el usuario, describe el
workspace y pagina los repositorios. La web consume la misma sesión desde
`/api/session`, `/api/repos` y `/api/health`; sin credenciales muestra el
estado "sin configurar" en vez de datos simulados.

La configuración y las credenciales viven en la **fila de conexión** de
`data/cache.db` (`init_sesion` con `is_source='config'`, `is_target=
'connection'`): corré `bbit login` (o conectate desde la web) para guardarlas.
No hay archivos `config/env.*`.

`bbit web --dev` levanta el backend con recarga automática y el frontend con
compilación en caliente: editás una línea en `bbit_release/` o en `frontend/src/` y se
refleja al instante, sin reinstalar el paquete.

### Requisitos

- Python >= 3.14
- Node.js >= 24 y npm (para compilar servír el frontend)

## Estructura

```
bbit_release/
├── cli.py               # Typer raíz: web, setup, login, session, version, home
├── config.py            # Config y credenciales desde la fila de conexión (SQLite)
├── logger.py            # Consola Rich
├── bitbucket/client.py  # Cliente Cloud API 2.0: auth (app password/PAT), paginación, sesión
└── web/                 # FastAPI + routers (health, repos, session)
frontend/                # Angular 22 + Ionic 9 (theme Bitbucket)
data/cache.db            # SQLite: cache + fila de conexión (gitignored)
tests/                   # pytest
```

## Fase 2 (planificada)

Pipeline Bitbucket: repos con la rama → diff vs master → estado sync →
escaneo de parámetros SSM (HashSet) → reporte markdown.
Ver `AGENTS.md`.