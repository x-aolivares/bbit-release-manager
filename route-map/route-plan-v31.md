# Route Plan — v31 · Historial de sesiones y persistencia de configuración de ramas

## Objetivo

Agregar un panel lateral (sidebar) con historial de sesiones guardadas. Cada sesión persiste:
- Rama origen (`origin`)
- Rama destino (`destination`)
- Prefijos de ambientes (`prefixes`)
- Prefijos de proyectos (`projectPrefixes`)
- Blacklist de proyectos excluidos (`blacklisted`)
- Checkbox `forceCache`

Al seleccionar una sesión del historial, se carga toda la configuración automáticamente. Las sesiones se ordenan por última actualización (más reciente arriba). Se guardan en `localStorage` del navegador.

## Contexto

Actualmente `home.ts` tiene los campos de configuración sueltos como señales (`signal`) y variables. No hay persistencia entre recargas ni forma de cambiar entre configuraciones previas. El usuario pide:
1. Cachear la config actual al usarla
2. Sidebar izquierdo con lista de sesiones: `release/ABC → master`, `feature/XYZ → master`, etc.
3. Click en una sesión = cargar esa config
4. Sobrescribir si ya existe misma pareja origen→destino

## Cambios propuestos

### 1. Nuevo servicio `SessionHistoryService` (`frontend/src/app/services/session-history.service.ts`)
- CRUD en `localStorage` clave `bbit_session_history`
- Schema por sesión: `{ id, name, origin, destination, prefixes[], projectPrefixes[], blacklisted[], forceCache, updatedAt }`
- `name` autogenerado: `${origin} → ${destination}`
- Límite: 50 sesiones (FIFO al exceder)
- Métodos: `getAll()`, `save(config)`, `load(id)`, `delete(id)`, `clear()`

### 2. Componente `SessionSidebarComponent` (`frontend/src/app/components/session-sidebar/`)
- Standalone, selector `app-session-sidebar`
- Input: `currentSessionId` (opcional, para resaltar activa)
- Output: `sessionSelected` (emite sesión completa)
- Lista ordenada por `updatedAt` desc
- Item muestra: nombre, origen→destino, timestamp relativo
- Click = emite sesión para cargar
- Botón eliminar por sesión
- Botón "Limpiar historial"

### 3. Integración en `HomeComponent` (`home.ts` + `home.html`)
- Inyectar `SessionHistoryService`
- `ngOnInit`: cargar última sesión si existe (o la más reciente)
- Después de `loadRepos()` exitoso: `saveCurrentSession()` (auto-guardado)
- Método `loadSession(session)`: setea todas las signals/variables y llama `loadRepos()`
- Agregar sidebar en template (izquierda, colapsible en móvil)

### 4. Estilos (`home.scss` + `session-sidebar.scss`)
- Layout flex: sidebar fijo 280px + contenido principal
- Breakpoint móvil: sidebar como drawer/offcanvas
- Item activo resaltado
- Hover/touch states

## Criterios de aceptación

- [ ] Sidebar visible a la izquierda en desktop
- [ ] Lista sesiones ordenadas por última actualización (reciente arriba)
- [ ] Click en sesión carga: origin, destination, prefixes, projectPrefixes, blacklisted, forceCache
- [ ] Auto-guardado tras `loadRepos()` exitoso
- [ ] Si ya existe sesión con mismo `origin→destination`, actualiza (upsert)
- [ ] Persiste en localStorage entre recargas del navegador
- [ ] Eliminar sesión individual funciona
- [ ] "Limpiar historial" funciona
- [ ] Responsive: en móvil sidebar colapsa (drawer)
- [ ] Build frontend compila (`npx ng build`)

## Alcance

| Archivo | Cambio |
|---|---|
| `frontend/src/app/services/session-history.service.ts` | Nuevo servicio |
| `frontend/src/app/components/session-sidebar/session-sidebar.ts` | Nuevo componente |
| `frontend/src/app/components/session-sidebar/session-sidebar.html` | Template |
| `frontend/src/app/components/session-sidebar/session-sidebar.scss` | Estilos |
| `frontend/src/app/pages/home/home.ts` | Integración + auto-save + load |
| `frontend/src/app/pages/home/home.html` | Layout con sidebar |
| `frontend/src/app/pages/home/home.scss` | Layout flex + responsive |
| `pyproject.toml` | Bump a `0.22.0` |

## Orden de implementación

1. Crear `SessionHistoryService` con tests básicos
2. Crear `SessionSidebarComponent` 
3. Integrar en `HomeComponent` (TS + HTML + SCSS)
4. Build frontend y verificar
5. Bump versión en `pyproject.toml`

## Verificación

```bash
cd frontend && npx ng build
```

## Nota de versión

Nueva funcionalidad mayor (sidebar + persistencia) → `0.21.1` → `0.22.0`, commit `feat:`.

## Estado

- [x] SessionHistoryService
- [x] SessionSidebarComponent
- [x] Integración en HomeComponent
- [x] Build compila
- [x] Versión bumpeda (0.22.0)

Estado: implementado