#!/bin/bash
cd "C:\Development\bbit-release-manager"
git add bbit_release/localgit/client.py bbit_release/web/session.py
git commit -m "refactor(BBIT-33): API first, clone matched repos in background

Phase 6: Verificar rama con API primero, clonar SOLO repos con rama en background.

Antes: Clonaba 139 repos antes de validar
Ahora: Verifica rama en API (10s), clona solo ~20 repos en background

Mucho mas rapido."
git push origin main
