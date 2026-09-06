# my-org-bbit-release-manager — Agent Instructions

## Versioning

Before committing any changes, bump the version in `pyproject.toml`:

| Tipo de cambio | Formato | Rango | Ejemplo |
|---|---|---|---|
| Ajuste pequeño (bug fix, tweak) | `0.1.x` | x: 0–9 | `0.1.0` → `0.1.1` |
| Ajuste significativo | `0.x.0` | x: 0–9 | `0.1.0` → `0.2.0` |
| Nueva funcionalidad mayor | `x.0.0` | x: 0–∞ | `0.1.0` → `1.0.0` |

## Principles

- **Modular** — cada dominio en su propio módulo (`bitbucket/`, `scan/`, `report/`, `web/`)
- **Retrocompatible** — no romper comandos existentes. Si un cambio altera comportamiento, version mayor
- **Versionable** — todo cambio se versiona, se commitea y se pushea

## Issues GitHub — nomenclatura BBIT-N

Cada tema/HU se registra como un issue en GitHub con nomenclatura de JIRA:

- **Tag del issue**: `BBIT-{N}` con `N` = número del issue en GitHub (BBIT-1, BBIT-2, …). Va como prefijo del título y como scope del commit.
- **Un issue por tema**: cada issue identifica un solo tema pendiente o en curso, con objetivo, contexto, criterios de aceptación, alcance y nota de versión en su body.
- **El body del issue es el plan de ejecución**: no hay carpeta `route-map/`; el plan vive en el issue y se actualiza ahí.
- **Tema completado**: al dar luz verde y mergear el PR a `main`, se cierra el issue.

## Workflow

When a user requests an adjustment:

1. Abrir/crear un issue con tag `BBIT-{N}` (si el tema aún no lo tiene) y escribir el plan en su body.
2. Crear la branch `bbit-{N}` desde `main` (una branch por issue; varios temas conviven en paralelo).
3. Make the code change
4. Update `version` in `pyproject.toml`
5. `git add -A && git commit -m "tipo(BBIT-N): descripción concisa"`
6. `git push`
7. Solo al dar **luz verde**: PR `bbit-{N}` → `main`, merge y cierre del issue (el PR referencias el issue: "closes #N").

## Commit message format

Use conventional commits (tipo en inglés, descripción en español):
- `feat:` — new feature
- `fix:` — bug fix
- `refactor:` — code restructuring
- `docs:` — documentation only
- `chore:` — tooling, config, dependencies

El mensaje debe ser descriptivo: sujeto corto en español (ej: `feat: bbit web con dev loop por HMR`) y, si el cambio es grande, un cuerpo con viñetas detallando qué se tocó.

Todo commit del trabajo de un issue lleva el identificador `(BBIT-{N})`,
donde `N` es el número del issue en GitHub, como scope del tipo. Sujeto corto
y descriptivo, sin emojis:

- `fix(BBIT-1): list_files por nombre de rama evita 404 en /src`
- `feat(BBIT-2): optimización de consultas a APIs externas en el scan`

## Config y credenciales

No hay archivos `config/env.*`. La configuración y las credenciales por
servicio (bitbucket, circle, `aws` reservado para BBIT-1) viven en la **fila de
conexión** de SQLite: `init_sesion` con `is_source='config'` e
`is_target='connection'` en `data/cache.db` (`is_details` JSON canónico).
Se persisten/leen desde `bbit_release/config.py` (`Config`) y se gestionan con
`bbit login`, el endpoint `/api/session` de la web, o `DELETE /api/session`.

Reglas:
- Nunca commitees credenciales reales. La carpeta `config/` está gitignored y
  no debe contener secretos.
- La fila de conexión queda fuera de `DELETE /api/cache` (limpiar historial no
  borra credenciales); `DELETE /api/session?delete_credentials=1` sí la borra.

## Dependencias compartidas (sync con yappy-cli-manager)

Este paquete y `yappy-cli-manager` viven en el mismo entorno editable y
comparten convenciones. Mantené el **stack común** alineado en `docs/requirements.txt`:

- `typer`, `rich`, `python-dotenv` (runtime)
- `pytest`, `coverage` (`docs/requirements-dev.txt`)

El runtime distintivo (Bitbucket/CircleCI/SSM en este repo; AWS/Kafka/DB en
yappy) puede y debe diferir — son dominios distintos. Al tocar una dep común,
actualizala en AMBOS repos y commitealos juntos para no generar drift.

## Fase 2 (pendiente)

Pipeline completo contra la API REST de Bitbucket Cloud (`api.bitbucket.org/2.0`):

1. Repos que contienen `x` rama — `GET /repositories/{workspace}` + filtro por refs/branches
2. Diff vs `master` — `GET /repositories/{workspace}/{repo}/diff/{spec}` o compare
3. Obtener cambios (raw de archivos por ref)
4. Estado sync vs master (commits no alcanzados por la branch)
5. Escaneo de parámetros SSM en archivos del diff (`/config/...`, `/common/...`, `{{resolve:ssm:...}}`)
6. Lista en memoria con set() (HashSet, sin repetidos)
7. Reporte markdown (`# SSM Sync Report — {branch} vs master`)

Comandos fase 2: `bbit report`, `bbit flow`, `bbit repos`, `bbit diff`, `bbit params`, `bbit pr`.