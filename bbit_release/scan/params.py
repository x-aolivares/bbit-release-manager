"""Extracción y clasificación de parámetros SSM desde texto de repos."""

from __future__ import annotations

import re
from typing import Iterable

# `{{resolve:ssm:/path:version}}` con versión/ARN opcional y barra inicial conservada.
SSM_RESOLVE_RE = re.compile(
    r"\{\{resolve:ssm:([A-Za-z0-9_./-]+)(?::([A-Za-z0-9_.:/-]+))?\}\}"
)

# Caracteres válidos en un segmento de ruta SSM (no `:` ni `}`).
_PATH_CHARS = r"[A-Za-z0-9_.,-]+"


def _prefix_slugs(prefixes: Iterable[str]) -> list[str]:
    """Prefijos limpios en minúscula sin barra inicial (config -> 'config')."""
    slugs: list[str] = []
    for p in prefixes or []:
        s = p.strip().strip("/").lower()
        if s:
            slugs.append(s)
    return slugs


def build_ssm_path_re(prefixes: Iterable[str]) -> re.Pattern:
    """Regex de rutas SSM `/{prefix}/seg/...` con lookbehind para no confundir
    con paths de Python (`/usr/...`) ni URLs (`https://...`)."""
    slugs = _prefix_slugs(prefixes)
    if slugs:
        alt = "|".join(re.escape(s) for s in slugs)
        return re.compile(rf"(?<![\w:])(?:/(?:{alt})(?:/{_PATH_CHARS})+)")
    return re.compile(rf"(?<![\w:])/(?:{_PATH_CHARS})(?:/{_PATH_CHARS})*")


def _match_prefix(path: str, slugs: list[str]) -> bool:
    """¿La ruta cae bajo algún prefijo, exigiendo límite de segmento?

    `/config/x` y `/config` matchean `/config`; `/configfoo/x` NO (no hay
    barra después del prefijo). Consistente con build_ssm_path_re.
    """
    if not slugs:
        return True
    p = path.lower()
    return any(p == f"/{s}" or p.startswith(f"/{s}/") for s in slugs)


def extract_ssm_params(lines: Iterable[str], prefixes: list[str]) -> list[tuple[str, str]]:
    """Devuelve (path, arn) de parámetros SSM únicos, con la barra inicial.

    Captura rutas peladas `/config/...` `/common/...` (sueltas, en comillas o
    dentro de `${...}`) y la forma `{{resolve:ssm:/path:arn}}`.
    """
    slugs = _prefix_slugs(prefixes)
    path_re = build_ssm_path_re(prefixes)
    seen: set[tuple[str, str]] = set()
    for line in lines:
        for m in SSM_RESOLVE_RE.finditer(line):
            path = "/" + m.group(1).lstrip("/")
            if _match_prefix(path, slugs):
                seen.add((path, m.group(2) or ""))
        for m in path_re.finditer(line):
            seen.add((m.group(0), ""))
    # Orden determinístico para reportes estables.
    return sorted(seen, key=lambda t: (t[0], t[1]))


def classify_ssm(
    origin: dict[str, set[str]],
    global_dest: set[str],
) -> dict[str, str]:
    """Clasifica caminos SSM según aparezcan en el master PRODUCCIÓN global.

    `global_dest` es la unión de los parámetros presentes en la rama destino
    (master) de TODOS los repos del proyecto, aunque no traigan la rama origen.
    Esto evita que un repo totalmente nuevo que reutiliza parámetros ya
    productivos los marque como `nuevo`.

    Devuelve `tipo` por path:
    - `nuevo`: path sumado en origen (release) de algún repo y que NO existe en
      ningún master (creado para la iniciativa).
    - `reutilizado`: path sumado en origen de un repo que ya está en el master
      de otro/s repo/s (productivo) → revisar SSM.
    """
    tipo: dict[str, str] = {}
    for paths in origin.values():
        for path in paths:
            if path not in tipo:
                tipo[path] = "reutilizado" if path in global_dest else "nuevo"
    return tipo