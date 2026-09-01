# Route Plan — v22 · Eliminar escaneo duplicado de proyectos al iniciar sesión

## Objetivo

Eliminar la consulta redundante de repositorios que se ejecutaba al **iniciar
sesión** — `BitbucketClient.session()` llamaba `list_repos()`, y el frontend
volvía a pedir los proyectos al hacer clic en "Obtener proyectos"
(`GET /api/repos`). La sesión solo debe validar token y describir el workspace.

## Contexto

- `BitbucketClient.session()` (client.py) validaba el token (`GET /user`),
  describía el workspace (`GET /workspaces/{ws}`) y **además** enumeraba todos
  los repos con `list_repos()` (`GET /repositories/{ws}`, paginado).
- `create_session()` (web/session.py) usaba esa lista **solo** para
  `repo_count = len(repos)`; la lista no se usaba para poblar resultados.
- El frontend dispara `GET /api/repos` (o `/api/scan`/`/api/diff`) al solicitar
  proyectos, repitiendo la misma enumeración.
- Resultado: dos recorridos del catálogo de repos por login (uno oculto).

## Cambios propuestos

1. `client.session()` deja de listar repos: devuelve solo
   `tuple[WorkspaceInfo, str]` y el docstring pasa a "Valida token y describe
   workspace".
2. `create_session()` deja de desempacar repos y fija `repo_count=0` (el conteo
   real llega cuando el frontend pide proyectos vía `/api/repos`).
3. CLI `_probe_bitbucket()` se adapta al nuevo retorno; el comando `session`
   ya imprime la tabla con un segundo `list_repos()` filtrado, que se mantiene.

## Criterios de aceptación

1. `POST /api/session` y `POST /api/session/reuse` **no** invocan
   `GET /repositories/{workspace}`.
2. `dbus session`/CLI `session` valida credenciales y describe workspace sin
   enumerar repos en el primer paso.
3. Todo el resto del flujo (proyectos, scan, diff) sigue funcionando y
   actualiza `repo_count` al pedir proyectos.

## Alcance

| Archivo | Cambio |
|---|---|
| `route-map/route-plan-v22.md` | este plan |
| `bbit_release/bitbucket/client.py` | `session()` sin `list_repos()`, retorno de 2 tupla |
| `bbit_release/web/session.py` | desempacar 2 tupla; `repo_count=0` |
| `bbit_release/cli.py` | `_probe_bitbucket()` acorde al nuevo retorno |
| `tests/test_bitbucket_client.py` | `test_session_ok` sin repos; sin mock de `/repositories` |
| `tests/test_web_api.py` | stubs de `session()` devuelven 2 tupla; `repo_count==0` |

## Orden de implementación

1. `client.session()` → 2 tupla (sin `list_repos()`).
2. `create_session()` y `_probe_bitbucket()`.
3. Actualizar stubs de tests.
4. Tests verdes.
5. Version `0.18.0 → 0.18.1`, commit `fix:` + push.

## Verificación

- `python -m pytest tests/ -q` verde.

## Nota de versión

Bug fix / tweak de rendimiento → `0.18.0 → 0.18.1`, commit `fix:`.

## Estado

- [x] `client.session()` sin listar repos
- [x] `create_session()` y CLI adaptados
- [x] Tests actualizados
- [x] Tests verdes
- [x] Version + commit + push

Estado: implementado en v0.18.1