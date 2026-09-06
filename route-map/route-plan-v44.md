# Route Plan — v44 · Modal de procesando para consultas pesadas

## Objetivo
Dar feedback visible (modal con spinner animado "Procesando…") mientras corren
las consultas pesadas (scan de repos / diff de parámetros SSM).

## Contexto
Hoy las consultas pesadas solo muestran spinners inline (en el botón
"Refrescar") con señal `reposLoading()`/`paramsLoading()`. Para consultas
largas el usuario no tiene un indicador claro de que se está procesando.

## Cambios propuestos
- Modal full-screen (máscara existente `.bb-modal-mask`) con tarjeta centrada,
  `ion-spinner name="dots"` grande y el texto "Procesando…".
- Se muestra mientras `reposLoading() || paramsLoading()`.
- Sin click para cerrar (es informativo de proceso, no un diálogo).

## Criterios de aceptación
- Al refrescar el scan o cargar el diff aparece el modal con spinner animado.
- Desaparece al completar o fallar la consulta.
- Build de Angular verde.

## Alcance
| Archivo | Cambio |
|---|---|
| `frontend/src/app/pages/home/home.html` | Modal de procesando al final del template |
| `frontend/src/app/pages/home/home.scss` | `.bb-modal--loading`, `.bb-spinner--lg`, `.bb-loading-text` |
| `pyproject.toml` | `0.29.0` → `0.29.1` |

## Orden de implementación
1. HTML. 2. SCSS. 3. Build. 4. Versionado + commit.

## Verificación
- `npx ng build --configuration development` sin errores.
- Prueba manual: `bbit web` → Refrescar / cargar diff muestra el modal.

## Nota de versión
`0.29.1` — ajuste pequeño (UI).

## Estado
- [x] Modal `@if (reposLoading() || paramsLoading())` con "Procesando…"
- [x] Estilos del modal y spinner grande
- [x] Build de Angular verde
- [x] `0.29.1`

Estado: implementado