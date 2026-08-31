# Route Plan — v10 · Detección y clasificación de parámetros SSM (2/3)

## Objetivo
Detectar los parámetros SSM de la iniciativa (rama origen vs rama destino),
clasificarlos en **nuevo** / **reutilizado (productivo)** / **solo-destino** y
mostrarlos en una tabla con columna `Tipo` y `valor en QA` (placeholder), más
una sección de removidos. Permite elegir el modo de lectura: solo archivos del
diff (default) o todos los archivos del repo.

## Contexto
- Hoy `src/scan/params.py` solo matchea `{{resolve:ssm:...}}` y elimina la barra
  inicial; el patrón empresarial incluye rutas peladas `/config/...` `/common/...`
  (dentro de `${...}`, comillas o sueltas).
- `/api/diff` devuelve "params nuevos" (origen−destino) sin clasificar, con un
  `_suggests_ssm` que solo detecta `ssm:`/`{{resolve:`.
- Se necesitan 3 categorías por parámetro:
  1. **nuevo** — solo en rama origen (creado para la iniciativa).
  2. **reutilizado** — existe en la rama destino de algún repo (productivo) y
     además se agrega en origen de otro → revisar SSM.
  3. **solo-destino (removed)** — existe en destino del repo y no en su origen;
     NO significa que deba eliminarse.
- Modo de lectura elegible en la UI: `diff` (solo archivos cambiados) o `all`
  (todos los archivos del repo, más costoso).

## Cambios propuestos
- **`src/bitbucket/client.py`**: `list_files(slug, ref, tree)` recursivo
  paginado para enumerar archivos de un ref (modo `all`).
- **`src/scan/params.py`**: extracción por prefijo configurado conservando la
  barra inicial `/(config|common)/...` con lookbehind `(?<![\w:])` (no confunde
  con paths de Python/URLs) + mantiene `{{resolve:ssm:...}}\{arn}`; helper para
  construir el regex; `classify_ssm(origin, dest)` → `tipo` por path + removed.
- **`src/web/api/repos.py`**:
  - `_suggests_ssm` basado en la nueva extracción (paths pelados incluidos).
  - `GET /api/diff?mode=diff|all`: por repo calcula `added` y `removed`;
    clasifica globalmente; respuesta suma `tipo`, `qa_value: null`, `removed` y
    `mode` (retrocompatible con `params[] {param, arn, repos}`).
- **`frontend/.../home.html` **: toggle modo de escaneo; tabla de parámetros con
  columnas Tipo (badge) · Nombre · valor en QA (`—`) · Repos; card "Solo en rama
  destino (no eliminar)".
- **`frontend/.../home.ts`**: señal `scanMode`; `loadParams` recibe `mode`;
  interfaces actualizadas.
- **`tests/`**: extracción (paths pelados, comillas, `${}`, resolve, negativo
  Python/URL), `list_files` (paginación+recursión), clasificador,
  `/api/diff` con `mode=diff` y `mode=all`.

## Criterios de aceptación
- [x] `extract_ssm_params` captura `/config/...` y `/common/...` sueltos, en
      comillas y en `${...}`, y conserva la barra inicial.
- [x] No matchea paths de Python (`/usr/...`, `/var/...`) ni URLs.
- [x] `/api/diff` devuelve `tipo` (nuevo/reutilizado) y `removed`, con `mode`.
- [x] Tabla frontend: Tipo · Nombre · valor en QA · Repos + sección removidos.
- [x] Toggle diff/all recarga los parámetros con el modo elegido.
- [x] Tests verdes.

## Alcance
| Archivo | Cambio |
|---|---|
| `src/bitbucket/client.py` | `list_files` |
| `src/scan/params.py` | extracción ampliada + `classify_ssm` |
| `src/web/api/repos.py` | `_suggests_ssm`, `/api/diff` mode + clasificación |
| `frontend/src/app/pages/home/home.html` | toggle modo, tabla params, card removidos |
| `frontend/src/app/pages/home/home.ts` | `scanMode`, `loadParams(mode)`, interfaces |
| `tests/test_params.py` | casos de extracción y clasificación |
| `tests/test_bitbucket_client.py` | `list_files` |
| `tests/test_web_api.py` | `/api/diff` diff/all |

## Orden de implementación
1. Extracción ampliada + tests.
2. `list_files` + tests.
3. `classify_ssm` + tests.
4. `/api/diff` mode + tests.
5. Frontend (toggle + tabla + sección).
6. Versionado `0.7.8 → 0.8.0` + commit `feat:` + push.

## Verificación
- `python -m pytest` (suite completa verde).
- Verificación manual de referencias frontend vía grep si `ng build` no corre.

## Nota de versión
Funcionalidad significativa → `0.7.8` → `0.8.0`.

## Estado
- [x] 1. Extracción
- [x] 2. `list_files`
- [x] 3. Clasificador
- [x] 4. `/api/diff`
- [x] 5. Frontend
- [x] 6. Versionado + commit + push

Estado: implementado.