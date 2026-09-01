# Route Plan — v16 · Fix colisión CLI `yappy`/`bbit` (paquete top-level `src`)

## Objetivo
Eliminar la colisión entre los paquetes instalados en editable de
`yappy-cli-manager` y `bbit-release-manager`: ambos registraban el módulo
top-level `src`, por lo que `import src` resolvía siempre a uno de ellos y los
entry points (`yappy`, `bbit`) cargaban el `src.cli` equivocado. Efecto
reportado: `yappy setup` ejecutaba el `setup` de `bbit`.

## Contexto
- Ambos `pyproject.toml` definen `[tool.setuptools.packages.find] include = ["src*"]`
  y entry point `... = "src.cli:app"`.
- En el entorno editable compartido, `.pth`/finders de ambos mapean `src` a su
  propio directorio. El finder que se registra primero gana → el otro `src` queda
  shadowed. Verificado: `import src` resolvía a `my-org-bbit-release-manager/src`.
- Interno de cada repo usa imports relativos; solo tests y entry points usan
  rutas absolutas `src.*`.

## Cambios propuestos
- **bbit**: renombrar paquete `src/` → `bbit_release/` (git mv). Actualizar
  `pyproject.toml` (`include = ["bbit_release*"]`, `bbit = "bbit_release.cli:app"`),
  tests (`from src.` → `from bbit_release.`, strings `"src.web.*"`), y worker de
  uvicorn `"src.web.main:app"` en `web/run.py`.
- **yappy**: renombrar paquete `src/` → `yappy_cli/` (git mv). Actualizar
  `pyproject.toml` (`yappy = "yappy_cli.cli:app"`), tests y strings de
  `python -m src.db.refresher` → `python -m yappy_cli.db.refresher`.

## Criterios de aceptación
- [x] `import src` ya no resuelve a ninguno de los dos (ModuleNotFoundError).
- [x] `yappy <cmd>` ejecuta el CLI de yappy y `bbit <cmd>` el de bbit.
- [x] Tests backend de ambos repos en verde tras reinstalar editable.

## Alcance
| Archivo | Cambio |
|---|---|
| `src/` → `bbit_release/` (bbit) | rename + imports |
| `src/` → `yappy_cli/` (yappy) | rename + imports |
| `pyproject.toml` (ambos) | packages.find + entry point |
| `bbit_release/web/run.py` | worker uvicorn |
| `yappy_cli/db/tunnel.py`, `yappy_cli/db/refresher.py` | `-m yappy_cli.db.refresher` |

## Orden de implementación
1. `git mv src <nuevo>` en cada repo.
2. Actualizar imports/tests/strings/config.
3. `pip install -e .` y correr tests (86 bbit + 51 yappy).
4. Versionado + commit + push por repo.

## Verificación
- `python -m pytest -q` → verde en ambos.
- `yappy version` → yappy; `bbit version` → bbit.

## Nota de versión
Ajuste significativo (refactor de nombre de paquete) → `0.12.1` → `0.13.0` (bbit)
y `1.5.2` → `1.6.0` (yappy).

## Estado
- [x] 1. Rename + imports
- [x] 2. Reinstall + tests
- [ ] 3. Versionado + commit + push

Estado: implementado (código + tests verdes).
