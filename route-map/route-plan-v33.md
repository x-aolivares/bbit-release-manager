# Route Plan — v33 · Observabilidad del cache (logs hit/miss)

## Objetivo

Que los logs de `bbit web` dejen claro si una respuesta se resuelve desde
SQLite (cache) o desde el servicio externo (Bitbucket/CircleCi), sin necesidad
de inferirlo por la ausencia/presencia de líneas `httpx`.

## Contexto

Con el cache normalizado (route-plan v32), los accesos van por la facade
`ReleaseCache` (`_get_cached` / `_set_cached` / `invalidate`). Pero el hit se
logueaba en `DEBUG`, los miss y stores no logueaban nada, y `web/main.py`
configura `basicConfig(level=INFO)`. Resultado: un miss se "lee" solo cuando
aparecen los `HTTP Request: GET ... httpx` del servicio externo, y un hit desde
DB pasa totalmente invisible (dos GET consecutivos a `/api/scan` o `/api/diff`
parecen servir igual aunque el segundo no tocó la red).

## Cambios propuestos

En `bbit_release/cache.py`:

- `_get_cached`: log `INFO` explícito para **miss** (`cache miss {rt}: {s}->{t}
  -> consulta servicio externo`) y para **hit** (`cache hit {rt}: {s}->{t}
  desde SQLite (is_id, creado hace Ns)`), con TTL restante en DEBUG.
- `_set_cached`: log `INFO` de **store** (`cache store {rt}: {s}->{t}
  guardado en SQLite (is_id, rq_id)`) para que el write sea auditable.
- `invalidate` / `invalidate_all`: subidos de DEBUG a INFO (el `force=1` debe
  verse en el log).
- Uso `->` ASCII (no `→`) para evitar problemas de codepage en consolas
  Windows (MINGW/cmd).

## Criterios de aceptación

- [ ] Un segundo `/api/scan` (o `/api/diff`) idéntico loguea `cache hit`.
- [ ] Un primer acceso o uno con config distinta loguea `cache miss ... -> consulta servicio externo`.
- [ ] El write tras el miss loguea `cache store`.
- [ ] `force=1` loguea `cache invalidate`.
- [ ] `pytest tests/ -q` verde sin cambios de contrato HTTP.

## Alcance

| Archivo | Cambio |
|---|---|
| `bbit_release/cache.py` | Logs INFO de miss/hit/store e invalidate |
| `pyproject.toml` | Bump a `0.23.1` |

## Orden de implementación

1. Agregar logs en `_get_cached` / `_set_cached`.
2. Subir `invalidate` / `invalidate_all` a INFO.
3. Verificar salida y correr tests.
4. Bump versión y commit.

## Verificación

```bash
python -m pytest tests/ -q
```

Chequeo manual: dos GET seguidos al mismo endpoint cacheado muestran
`cache hit` en el segundo; `force=1` muestra `cache invalidate`; el primer
acceso muestra `cache miss` + `cache store` + las llamadas httpx externas.

## Nota de versión

Ajuste pequeño (tweak de logging, sin cambio funcional en respuestas HTTP)
→ `0.23.0` → `0.23.1`, commit `fix:`.

## Estado

- [x] Logs de miss/hit/store en la facade
- [x] `invalidate` / `invalidate_all` a INFO
- [x] Verificado en consola y tests verdes
- [x] Versión bumpeda (0.23.1)

Estado: implementado