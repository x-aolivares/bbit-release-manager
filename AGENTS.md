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

## Route Map (carpeta `route-map/`)

La carpeta `route-map/` en la raíz contiene el plan de desarrollo de cada tema
que se discuta del proyecto. Reglas:

- **Un archivo por tema/iteración**: cada plan identifica un solo tema pendiente o en curso, con objetivo, contexto, cambios, criterios de aceptación, alcance, orden de implementación, verificación y nota de versión.
- **Numeración monotónica**: `route-plan-v{N}.md` con `N` incremental; cada tema nuevo que se discuta recibe `v{N+1}` (nunca se reutiliza un número).
- **Antes de trabajar un tema**: leer el `route-plan` correspondiente más reciente; si el tema todavía no tiene plan, crearlo con el próximo número antes de implementar.
- **Tema completado**: marcar con `[x]` los puntos del checklist y agregar al final `Estado: implementado` una vez verificado (tests verdes + criterios de aceptación).
- **Los plan files se versionan**: se commitean junto con el trabajo que describen (`docs:` si es plan únicamente, o junto al `feat:`/`fix:` correspondiente).

Template de cada `route-plan-v{N}.md`:

```markdown
# Route Plan — v{N} · <tema>

## Objetivo
## Contexto          <estado actual, flujo, piso/límites>
## Cambios propuestos
## Criterios de aceptación
## Alcance            <tabla archivo → cambio>
## Orden de implementación
## Verificación
## Nota de versión
## Estado            <checklist [ ]/[x] + "Estado: implementado" al cerrar>
```

## Workflow

When a user requests an adjustment:

1. Make the code change
2. Update `version` in `pyproject.toml`
3. `git add -A && git commit -m "tipo: descripción concisa"`
4. `git push`

## Commit message format

Use conventional commits (tipo en inglés, descripción en español):
- `feat:` — new feature
- `fix:` — bug fix
- `refactor:` — code restructuring
- `docs:` — documentation only
- `chore:` — tooling, config, dependencies

El mensaje debe ser descriptivo: sujeto corto en español (ej: `feat: bbit web con dev loop por HMR`) y, si el cambio es grande, un cuerpo con viñetas detallando qué se tocó.

## Config files

Files under `config/env.*` (without `.example`) are gitignored.
Never commit real credentials or environment-specific values.
Always update the `.example` templates when the config shape changes.

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