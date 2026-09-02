# Route Plan — v25 · Caché persistente con SQLite

## Objetivo

Reemplazar los caches en memoria (dicts con TTL) por una caché persistente en SQLite que sobreviva reinicios del servidor y permita reutilizar resultados entre sesiones.

## Contexto

Actualmente hay 3 caches en memoria en `repos.py`:
- `_REPO_CACHE` — descubrimiento de repos
- `_BRANCH_REPOS_CACHE` — repos con branch específica
- `_MASTER_CACHE` — parámetros SSM de master

Todos se pierden al reiniciar uvicorn. La key incluye `session_id`, así que un login nuevo genera cache frío.

El usuario quiere:
1. Login → reutilizar credenciales o crear nuevas
2. Home → llenar campos (ramas origen/destino, prefijos, ambientes, repos excluidos, forzar)
3. Consultar proyectos → guardar en caché
4. La caché debe ser accesible la próxima vez que se consulte la misma combinación origen-destino
5. Si se fuerza consulta, sobrescribir
6. Scan (tabla) y diff (parámetros) también en caché
7. Idempotencia: no consultar lo mismo dos veces

## Cambios propuestos

### 1. Nuevo módulo `bbit_release/cache.py`

SQLite en `~/.bbit/cache.db` (o `{config_dir}/cache.db`).

**Tablas:**

```sql
-- Cache de repos por combinación origen-destino
CREATE TABLE IF NOT EXISTS repo_cache (
    cache_key TEXT PRIMARY KEY,      -- "{origin}|{destination}|{prefixes}|{exclude}"
    origin TEXT NOT NULL,
    destination TEXT NOT NULL,
    repos_json TEXT NOT NULL,        -- JSON serializado de list[Repository]
    created_at REAL NOT NULL         -- time.time()
);

-- Cache de scan (tabla principal)
CREATE TABLE IF NOT EXISTS scan_cache (
    cache_key TEXT PRIMARY KEY,      -- "{origin}|{destination}|{prefixes}|{exclude}"
    origin TEXT NOT NULL,
    destination TEXT NOT NULL,
    scan_json TEXT NOT NULL,         -- JSON del response completo de /scan
    created_at REAL NOT NULL
);

-- Cache de diff/parámetros SSM
CREATE TABLE IF NOT EXISTS diff_cache (
    cache_key TEXT PRIMARY KEY,      -- "{origin}|{destination}|{prefixes}|{exclude}"
    origin TEXT NOT NULL,
    destination TEXT NOT NULL,
    diff_json TEXT NOT NULL,         -- JSON del response completo de /diff
    created_at REAL NOT NULL
);

-- Cache de master params (el más costoso)
CREATE TABLE IF NOT EXISTS master_cache (
    cache_key TEXT PRIMARY KEY,      -- "{slug}|{destination}"
    slug TEXT NOT NULL,
    destination TEXT NOT NULL,
    params_json TEXT NOT NULL,       -- JSON del set de (path, arn)
    created_at REAL NOT NULL
);
```

**API pública:**

```python
class ReleaseCache:
    def __init__(self, db_path: Path | None = None):
        """Abre/crea la DB. Singleton por defecto."""
    
    def get_repos(self, origin, destination, prefixes, exclude) -> list | None:
    def set_repos(self, origin, destination, prefixes, exclude, repos: list) -> None:
    
    def get_scan(self, origin, destination, prefixes, exclude) -> dict | None:
    def set_scan(self, origin, destination, prefixes, exclude, data: dict) -> None:
    
    def get_diff(self, origin, destination, prefixes, exclude) -> dict | None:
    def set_diff(self, origin, destination, prefixes, exclude, data: dict) -> None:
    
    def get_master(self, slug, destination) -> set | None:
    def set_master(self, slug, destination, params: set) -> None:
    
    def invalidate(self, origin, destination, prefixes, exclude) -> None:
    def invalidate_all(self) -> None:
```

**Key generation:**

```python
def _cache_key(origin: str, destination: str, prefixes: list[str], exclude: set[str]) -> str:
    p = ",".join(sorted(prefixes or []))
    e = ",".join(sorted(exclude))
    return f"{origin}|{destination}|{p}|{e}"
```

### 2. Modificar `repos.py`

- Instanciar `ReleaseCache` al inicio del módulo
- Reemplazar los 3 dicts de cache por llamadas a `ReleaseCache`
- Eliminar `_CACHE_TTL`, `_MASTER_CACHE`, `_REPO_CACHE`, `_BRANCH_REPOS_CACHE` y sus helper functions
- Agregar parámetro `force: int = 0` a `/scan` y `/diff` (ya existe en `/repos`)
- Cuando `force=1`, llamar `cache.invalidate(...)` antes de consultar

**Endpoints modificados:**

| Endpoint | Cambio |
|---|---|
| `GET /api/repos` | Leer/escribir `repo_cache` |
| `GET /api/scan` | Leer/escribir `scan_cache` + `master_cache` |
| `GET /api/diff` | Leer/escribir `diff_cache` + `master_cache` |

### 3. Limpiar caché al destruir sesión (opcional)

Cuando se llama `DELETE /api/session`, decidir si limpiar la caché o no. El usuario dice que la caché debe persistir entre sesiones, así que **NO limpiar al destruir sesión**.

### 4. Archivos a modificar

| Archivo | Cambio |
|---|---|
| `bbit_release/cache.py` | **NUEVO** — módulo SQLite cache |
| `bbit_release/web/api/repos.py` | Reemplazar caches en memoria por `ReleaseCache` |
| `pyproject.toml` | Bump versión |
| `tests/test_cache.py` | **NUEVO** — tests unitarios del cache |

## Criterios de aceptación

- [ ] Al consultar `/api/repos` con una combinación origen-destino, la segunda consulta NO hace llamadas a Bitbucket API
- [ ] Al consultar `/api/scan`, la segunda consulta con los mismos parámetros retorna el mismo resultado sin llamadas API
- [ ] Al consultar `/api/diff`, la segunda consulta con los mismos parámetros retorna el mismo resultado sin llamadas API
- [ ] Al pasar `force=1` a cualquier endpoint, se sobreescribe la caché y se consulta la API
- [ ] La caché persiste al reiniciar uvicorn (no se pierde)
- [ ] La caché es accesible entre sesiones distintas (mismo origin-destino reutiliza)
- [ ] Tests unitarios pasan

## Alcance

| Archivo | Cambio |
|---|---|
| `bbit_release/cache.py` | Nuevo módulo con clase `ReleaseCache` |
| `bbit_release/web/api/repos.py` | Reemplazar 3 dicts por `ReleaseCache` |
| `tests/test_cache.py` | Tests unitarios |
| `pyproject.toml` | Bump a `0.19.0` |

## Orden de implementación

1. Crear `bbit_release/cache.py` con la clase `ReleaseCache` y tests
2. Modificar `repos.py` para usar `ReleaseCache` en `/repos`
3. Modificar `repos.py` para usar `ReleaseCache` en `/scan`
4. Modificar `repos.py` para usar `ReleaseCache` en `/diff`
5. Agregar `force` a `/scan` y `/diff`
6. Bump versión en `pyproject.toml`
7. Correr tests y lint

## Verificación

```bash
pytest tests/test_cache.py -v
pytest tests/test_web_api.py -v
# Verificar que no se rompió nada existente
```

## Nota de versión

`0.18.3` → `0.19.0` (nueva funcionalidad: caché persistente)

## Estado

- [x] cache.py creado
- [x] repos.py modificado (repos)
- [x] repos.py modificado (scan)
- [x] repos.py modificado (diff)
- [x] force param agregado a scan/diff
- [x] tests creados
- [x] tests pasan (126/126)
- [x] lint/typecheck pasan
- [x] versión bumpeda (0.19.0)

Estado: implementado
