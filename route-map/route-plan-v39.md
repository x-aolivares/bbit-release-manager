# Route Plan — v39 · Auto-reuse de sesión guardada al arrancar

## Objetivo

Eliminar el 401 de `/api/scan` que aparece tras reiniciar `bbit web`: el
frontend dispara la carga de la última sesión (que llama a `/api/scan`) sin
haber reusado la sesión guardada en el backend. Ahora, si hay credenciales
guardadas, se reusa automáticamente antes de cargar la última sesión.

## Contexto

Al reiniciar el server, la sesión en RAM (`web/session.py`, dict `_sessions`)
muere: `/api/session` responde `{"active": False, "stored": True, ...}`. El
constructor de `home.ts` hacía en paralelo:

1. `GET /api/session` → si `stored`, solo prendía el banner de "reusar".
2. `loadSession(latest)` → `loadRepos()` → `GET /api/scan` **sin sesión activa**
   → 401 (`_require_session()`).

Secuencia del bug en logs:
```
GET /api/session → 200 (active:false, stored:true)
GET /api/scan    → 401 Unauthorized
GET /api/repos   → 200 (sin validación de sesión)
```

El endpoint `POST /api/session/reuse` ya existía (`repos.py:189`): reconstruye
la sesión desde `Config().bitbucket_token` + `workspace`, abortando con 401 si
las credenciales vencieron.

## Cambios propuestos

1. `frontend/src/app/pages/home/home.ts`:
   - Constructor: el `GET /api/session` deja de cargar la última sesión de forma
     incondicional.
     - `active` → cargar última sesión directo (mantiene comportamiento).
     - `stored` → `reuseSession(onSuccess)` y recién en el callback cargar la
       última sesión.
     - ni stored ni active → no cargar (no hay sesión backend; evita 401).
   - `reuseSession(onSuccess?)`: nuevo callback opcional ejecutado cuando el
     POST `/api/session/reuse` devuelve `ok`.
   - Nuevo helper `loadLatestSession()` (encapsula `getLatest()` +
     `loadSession()`).
   - El banner `storedCreds` se sigue usando: tras `disconnect(false)` y en
     errores del reuse (credenciales vencidas → "Generá de nuevo").

## Criterios de aceptación

- [ ] Tras reiniciar `bbit web` con credenciales guardadas, `/api/scan` no
      dispara 401: el front reusa la sesión antes de cargar la última config.
- [ ] Sin credenciales guardadas, no se carga la última sesión (ni 401).
- [ ] Credenciales vencidas → error visible "Generá de nuevo" (banner).
- [ ] Build Angular sin errores; tests Python verdes.

## Alcance

| Archivo | Cambio |
|---|---|
| `frontend/src/app/pages/home/home.ts` | Constructor encadena reuse → loadLatestSession; callback en reuseSession |
| `pyproject.toml` | Bump |

## Orden de implementación

1. Constructor: encadenar el reuse antes de `loadLatestSession`.
2. `reuseSession(onSuccess?)` con callback.
3. Build + bump + commit + push.

## Verificación

```bash
cd frontend && npm run build -- --configuration development
python -m pytest tests/ -q
```

## Nota de versión

Bug fix (401 al reiniciar) → `0.27.0` → `0.27.1`, commit `fix:`.

## Estado

- [x] Constructor encadena reuse → loadLatestSession
- [x] `reuseSession(onSuccess?)` con callback
- [x] Build Angular OK, 161 tests Python verdes
- [x] Bump `0.27.1`

Estado: implementado