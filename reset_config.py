#!/usr/bin/env python3
"""Reset git_clones_dir to default (~/.bbit/clones)"""

from bbit_release.cache import get_cache
from bbit_release.config import Config

cache = get_cache()

# Leer config actual
conn = cache.get_connection()
print(f"Config actual: {conn}")

# Limpiar git_clones_dir
if conn:
    settings = conn.get("settings", {})
    settings["git_clones_dir"] = ""  # Vacío = default
    conn["settings"] = settings
    cache.save_connection(conn)
    print("✓ git_clones_dir limpiado, ahora usará default ~/.bbit/clones/")
else:
    print("No hay config")

# Verificar
cfg = Config()
print(f"git_clones_dir después reset: {cfg.git_clones_dir}")
