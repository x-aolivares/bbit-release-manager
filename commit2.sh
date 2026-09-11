#!/bin/bash
cd "C:\Development\bbit-release-manager"
git add bbit_release/web/session.py bbit_release/web/api/repos.py
git commit -m "refactor(BBIT-33): Clonar repos DESPUÉS del filtrado por prefijos

Cambio de estrategia:

Antes:
- Login clona TODO (~150 repos) en background
- User espera o falla

Ahora:
- Login cachea lista (sin clonar)
- User clickea 'Obtener Repos' + prefijos
- Se filtran repos por prefijo
- Se clona SOLO los repos filtrados en background (5-10)
- Verificación de branches local (git)

Beneficios:
- Solo clonas lo que necesitas
- Más rápido
- Menos disco
- User no espera en login

Funciones nuevas:
- _preload_repos_list_background(): Cachea lista sin clonar
- _clone_repos_background(): Clona repos filtrados"
