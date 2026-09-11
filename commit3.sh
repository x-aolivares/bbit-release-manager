#!/bin/bash
cd "C:\Development\bbit-release-manager"
git add bbit_release/localgit/client.py bbit_release/web/api/repos.py
git commit -m "refactor(BBIT-33): Clone repos DIRECTAMENTE en repos_with_branch()

BBIT-33 Phase 4: Desacoplado de cache.

Cambio:
- Antes: Clone en background despues de cache
- Ahora: Clone directo en repos_with_branch() cuando user clickea

Flujo:
1. User clickea Obtener Repositorios + prefijos
2. repos_with_branch() inicia
3. Clona INMEDIATAMENTE (4 concurrent)
4. Ver [CLONE] en terminal en tiempo real
5. Resuelve branches localmente (git, CERO API)
6. Retorna repos con rama

Resultado: Cero API calls para branch verification"
