# Rediseño: búsqueda universal → panel de repositorios filtrados

> **Estado**: borrador (antes de issue/PR). Este archivo es el plano vivo del
> cambio y se actualiza a medida que afinamos el diseño.
> **Mock**: `docs/mockups/index.html` — un solo HTML editable (estructura + lógica inline). Los datos viven en `docs/mockups/data/*.js` como endpoints simulados (la app real consulta esos mismos paths) y `js/api.js` es la capa fetch.

## Cambio de paradigma

**Hoy** el flujo es vertical y acoplado: un solo panel ("Resolver ramas") junta
rama origen, rama destino, ambientes de deploy, prefijos y blacklist; apretás
"Obtener Repositorios" y un SSE pesado escanea, filtra, resuelve ramas, PRs,
tags y diff SSM en una sola pasada. El filtro vive mezclado con la consulta
pesada.

**Target (etapa 1)** — separar "qué repos quiero ver" de "qué info les pido":

1. **El buscador es el ÚNICO lugar con filtros**: prefijos de proyecto y
   blacklist. Nada más ahí.
2. **"Buscar" trae el universo completo del workspace** (los "todos todos
   todos"): `list_repos` paginado sobre el índice filter-free (BBIT-46), sin
   prefijos/blacklist en la clave.
3. Sobre ese universo se aplican **prefijo + blacklist como VISTA** → queda la
   lista que realmente nos importa. Ej.: 900 repos en el workspace → 100 con
   coincidencia.
4. **Las tablas actuales se ocultan** (repos-table de scan y el panel SSM).
   No se tocan los endpoints: quedan retrocompatibles para la etapa 2 y el CLI.
5. Aparece un **panel nuevo con una tabla del mismo estilo que la tabla de
   repositorios actual** (`bb-table--repos`), conteniendo los repos **YA
   filtrados** por prefijo y blacklist.
6. **En la cabecera de esa tabla vive la consulta de la rama origen**: para
   esa lista en concreto preguntamos a Bitbucket **¿existe esta rama dentro de
   este repositorio?** (1 request/repo en paralelo → `found` | `not_found`,
   cacheado por `(workspace, branch)` en `branch_refs`).

## Flujo en pantalla

```
┌─────────────────────────────────────────────────────────────┐
│  BUSCADOR  (único lugar con filtros)                        │
│  • Prefijos de proyecto: [orders] [catalog] [payments] +     │
│  • Blacklist: [experimental] [legacy-bin] +                  │
│  • [ Buscar repositorios ]                                   │
│  • 900 repos en workspace → 100 con coincidencia             │
└─────────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────────┐
│  REPOSITORIOS FILTRADOS (panel nuevo, misma tabla)          │
│  Cabecera de consulta:                                       │
│  • Rama origen [ release/REP-325073 ] [ Ver rama ]           │
│  • 100 repos · 87 con la rama · 13 sin la rama               │
│  Columnas:  Proyecto | Repositorio | Rama origen | Resuelta  │
│  ─────────────────────────────────────────────────────────   │
│  Orders Platform | orders-api      | ✓ Encontrada | V9       │
│  Catalog        | catalog-search   | ✗ No existe  | —        │
│  ...                                                         │
└─────────────────────────────────────────────────────────────┘
```

## Columnas de la nueva tabla (borrador)

- **Proyecto** — nombre del proyecto al que pertenece el repo.
- **Repositorio** — slug/name con link a Bitbucket.
- **Rama origen** — badge `Encontrada` / `No existe` / `Revisando…`.
- **Rama resuelta** (indistinta) — la variante efectiva p.ej. `-V9` cuando la
  rama literal no existe (comportamiento actual de `resolve_branch`).

> ⚠️ El listado original se cortó en "nombre del proyecto…". **Columnas
> pendientes de confirmar por el usuario.**

## Backend: qué ya existe y qué falta

**Ya existe (cero trabajo nuevo):**
- `/api/repos-quick` devuelve la lista universal filtrada y, si le pasás
  `origin` + `destination`, resuelve `branch_state` por repo usando el índice
  `branch_refs` (cacheado, single-flight BBIT-60). Es el endpoint de este panel.
- `_all_repos_cached` + `_apply_filters`: universo filter-free + vista.
- `repos_with_branch` / `_resolve_branch_refs`: el "¿existe esta rama?".

**Falta / decisión abierta:**
- La columna **Proyecto** necesita el campo `project` del repo, que hoy NO se
  captura en el sweep de `list_repos` (el dataclass `Repository` no lo tiene).
  `api.bitbucket.org/2.0/repositories/{workspace}` trae `project.key` y
  `project.name` por repo → cambio chico y retrocompatible en `client.py`.

## Decisiones abiertas (para el usuario)

1. **Columnas exactas** de la tabla (el mensaje se cortó en "Proyecto").
2. **"Proyecto"**: ¿el nombre real del proyecto de Bitbucket (`project.name`)
   o el prefijo del slug (inferido, sin tocar el backend)?
3. **Qué pasa con rama destino, ambientes, PRs y tags** en el nuevo flujo
   (¿etapa 2 sobre la misma tabla o paneles posteriores?).
4. **Tablas actuales**: ¿solo ocultas (retrocompatible, toggle) o eliminadas
   del front en una versión mayor?

## Alcance de implementación (cuando haya luz verde)

- Backend: exponer `project` en `Repository` + index (cambio menor).
- Frontend: componente `repos-browser` (buscador + tabla del nuevo flujo);
  convivir con el home actual o reemplazarlo según la decisión 4.
- El flujo pesado (flow/scan/diff/PRs/tags) queda intacto detrás de las
  acciones de la etapa 2.

## Versionado

Cambio de comportamiento en la web → `0.x.0`. Se dispara el issue BBIT-{N}
(próximo libre: ~BBIT-65) con su branch `feature/BBIT-{N}` al aprobar el diseño.