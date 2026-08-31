"""Extracción de parámetros SSM desde líneas añadidas de un diff."""

from __future__ import annotations

import re
from typing import Iterable

SSM_RE = re.compile(r"\{\{resolve:ssm:([A-Za-z0-9_./-]+)(?::([A-Za-z0-9_.:/-]+))?\}\}")


def extract_ssm_params(lines: Iterable[str], prefixes: list[str]) -> list[tuple[str, str]]:
    """Devuelve (path, arn) de parámetros SSM únicos cuya ruta matchea un prefijo.

    Un parámetro se considera NUEVO si su `{{resolve:ssm:...}}` aparece en una
    línea añadida del diff (no existía en la rama base).
    """
    clean_prefixes = [p.strip().strip("/").lower() for p in prefixes if p.strip()]
    seen: set[tuple[str, str]] = set()
    for line in lines:
        for m in SSM_RE.finditer(line):
            path = m.group(1).lstrip("/")
            arn = m.group(2) or ""
            if not path:
                continue
            if clean_prefixes and not any(
                path.lower().startswith(p) for p in clean_prefixes
            ):
                continue
            seen.add((path, arn))
    # Orden determinístico para reportes estables.
    return sorted(seen, key=lambda t: t[0])