@echo off
cd C:\Development\bbit-release-manager
git add -A
git commit -m "feat(BBIT-33): Eliminar HTTP API calls para verificación de branches, auto-clone en background

BBIT-33 Implementation:
- Auto-clone repos en background daemon durante login
- Default git_clones_dir=~/.bbit/clones (automático)
- LocalRepoClient wrapper con git local para verificación de branches (ZERO API calls)
- _resolve_for_branch() llama ensure_repo() antes de leer localmente
- Test agregado: repos_with_branch() clona on-demand si no está disponible

Plus: Filtrado inteligente de repos sin rama
- _repo_scan() retorna None si no encuentra commit
- _stream_scan() y _scan_repos() filtran silenciosamente repos sin rama
- Tabla final muestra SOLO repos relevantes

Plus: Fix git pull desde directorio correcto
- bbit web --dev ahora ejecuta git pull desde el proyecto (no home)

Resultados:
✅ Zero 429 rate limit errors para verificación de branches
✅ Verificación instant (git local, offline)
✅ Background cloning: user no espera
✅ Fallback automático a HTTP si git falla"
git log --oneline -1
