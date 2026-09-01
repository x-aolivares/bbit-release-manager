# bbit-release-manager

CLI + web para revisar ramas y parámetros SSM contra la API de Bitbucket.
Replica la arquitectura de `yappy-cli-manager`: CLI en Python (Typer/Rich),
backend FastAPI y frontend Angular 22 + Ionic 9.

## Fase 1 (actual): bbit web + bbit setup + bbit session

```bash
pip install -e .
bbit setup              # Verifica deps y crea config/env.base
bbit session            # Valida token/acceso a Bitbucket y lista los repos
bbit web                # Sirve el frontend compilado + API (modo prod local)
bbit web --dev          # uvicorn --reload (backend) + ng serve (HMR frontend)
bbit web --no-browser
```

`bbit session` abre una sesión real contra la API REST de **Bitbucket Cloud**
(`api.bitbucket.org/2.0`): valida el token, identifica el usuario, describe el
workspace y pagina los repositorios. La web consume la misma sesión desde
`/api/session`, `/api/repos` y `/api/health`; sin credenciales muestra el
estado "sin configurar" en vez de datos simulados.

Configurá `config/env.base`: `BITBUCKET_WORKSPACE` (ej. `my_org_web_dev`),
`BITBUCKET_TOKEN` (app password o PAT) y, si usás app password,
`BITBUCKET_USERNAME`. Después corré `bbit session`.

`bbit web --dev` levanta el backend con recarga automática y el frontend con
compilación en caliente: editás una línea en `bbit_release/` o en `frontend/src/` y se
refleja al instante, sin reinstalar el paquete.

### Requisitos

- Python >= 3.14
- Node.js >= 24 y npm (para compilar servír el frontend)

## Estructura

```
bbit_release/
├── cli.py               # Typer raíz: web, setup, session, version, home
├── config.py            # Config por entorno (dotenv, mirror yappy)
├── logger.py            # Consola Rich
├── bitbucket/client.py  # Cliente Cloud API 2.0: auth (app password/PAT), paginación, sesión
└── web/                 # FastAPI + routers (health, repos, session)
frontend/                # Angular 22 + Ionic 9 (theme Bitbucket)
config/                  # env.base.example -> env.base (gitignored)
tests/                   # pytest
```

## Fase 2 (planificada)

Pipeline Bitbucket: repos con la rama → diff vs master → estado sync →
escaneo de parámetros SSM (HashSet) → reporte markdown.
Ver `AGENTS.md`.