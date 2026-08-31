# Route Plan — v7 · Validar que el PR obtenido esté realmente OPEN

## Objetivo
Garantizar que la detección de "existe un PR de origen → destino" solo dé por
válido un PR cuyo `state` sea `OPEN`, para evitar falsos positivos con PRs
MERGED/DECLINED/SUPERSEDED.

## Contexto
- `BitbucketClient.find_pr` consulta `GET /pullrequests` con `state=OPEN` y la
  query por ramas, pero devuelve el **primer** `values` sin verificar su campo
  `state`.
- Si Bitbucket devolviera en esa lista un PR no abierto (respuesta paginada,
  quirk del filtro, etc.), hoy se trataría como PR válido:
  - `/scan` (`_repo_scan`): usa `source_commit` del PR para el head de origen y
    reporta `exists: true`.
  - `/prs/create-missing`: salta el repo aunque no haya PR abierto.
  - `/prs/update-titles`: renombra PRs que no están abiertos.
  - `/diff`: usa el head del PR como ref de origen.

## Cambios propuestos
- **`src/bitbucket/client.py`**: en `find_pr`, saltar todo PR cuyo
  `state` (normalizado a mayúsculas) no sea `OPEN`. Devolver `None` si no hay
  ninguno abierto.
- **`tests/test_bitbucket_client.py`**: casos con PR MERGED (→ `None`) y con
  lista mixta MERGED+OPEN (→ solo el OPEN).

## Criterios de aceptación
- [x] `find_pr` solo retorna PRs con `state == "OPEN"`.
- [x] Con `values` sin PRs OPEN retorna `None`.
- [x] Con mezcla MERGED/OPEN retorna el OPEN.
- [x] Tests verdes.

## Alcance
| Archivo | Cambio |
|---|---|
| `src/bitbucket/client.py` | filtro defensivo `state == "OPEN"` en `find_pr` |
| `tests/test_bitbucket_client.py` | tests de PR no abierto y lista mixta |

## Orden de implementación
1. Filtro en `find_pr`. 2. Tests. 3. Verificación. 4. Versionado + commit + push.

## Verificación
- `python -m pytest` (tests verdes).

## Nota de versión
Fix de robustez → `0.7.5` → `0.7.6`.

## Estado
- [x] 1. Filtro en `find_pr`
- [x] 2. Tests
- [x] 3. Verificación
- [x] 4. Versionado + commit + push

Estado: implementado.