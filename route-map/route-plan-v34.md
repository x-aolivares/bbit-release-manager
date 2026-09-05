# Route Plan — v34 · DB del cache dentro del proyecto (`data/`)

## Objetivo

Que la base SQLite del cache viva dentro del repo, en `data/`, en vez de
`~/.bbit/` (fuera del proyecto).

## Contexto

`_DEFAULT_DB` apuntaba a `Path.home() / ".bbit" / "cache.db"`. La carpeta
`data/` ya existe en `.gitignore`, por lo que la DB se mantiene local y no se
versiona — consistente con el principio de datos efímeros de cache.

## Cambios propuestos

En `bbit_release/cache.py`:

```python
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_DB = _PROJECT_ROOT / "data" / "cache.db"
```

El `__init__` ya hace `mkdir(parents=True, exist_ok=True)`, así que `data/`
se crea solo si no existe. Las 4 tablas se crean ahí igual que antes.

## Criterios de aceptación

- [ ] La DB se crea en `<proyecto>/data/cache.db`.
- [ ] `data/` permanece en `.gitignore` (no se versiona).
- [ ] `git status` no muestra la DB como untracked.
- [ ] `pytest tests/ -q` verde.

## Alcance

| Archivo | Cambio |
|---|---|
| `bbit_release/cache.py` | `_DEFAULT_DB` relativo al proyecto (`data/cache.db`) |
| `pyproject.toml` | Bump a `0.23.2` |

## Orden de implementación

1. Cambiar `_DEFAULT_DB`.
2. Verificar creación en `data/` y `git status` limpio.
3. Correr tests.
4. Bump versión y commit.

## Verificación

```bash
python -m pytest tests/ -q
git status
```

## Nota de versión

Ajuste pequeño (cambio de ubicación de la DB, sin cambio funcional en
respuestas HTTP) → `0.23.1` → `0.23.2`, commit `fix:`.

## Estado

- [x] `_DEFAULT_DB` apunta a `data/cache.db`
- [x] Verificado creación de tablas en `data/`
- [x] `data/` ignorado por git
- [x] Tests verdes
- [x] Versión bumpeda (0.23.2)

Estado: implementado