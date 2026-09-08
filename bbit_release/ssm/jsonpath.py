"""Motor de diff JSONPath determinista para valores SSM (BBIT-2).

Compara el JSON del valor entre dos ambientes y reporta **solo lo tocado**:
modificaciones puntuales de hojas, inserciones en una sola línea para
subárboles ausentes en el destino, y un warning cuando hay asimetría entre
los objetos (le falta info al destino o al destino le sobra data).

Los valores no parseables como JSON se tratan como escalares (compare como
string). El orden de las operaciones es determinístico (keys ordenadas).
"""

from __future__ import annotations

import json
from typing import Any

# Mensaje canónico del issue (BBIT-2).
WARNING_MSG = (
    "es posible que para sincronizar correctamente se deba eliminar data del "
    "ambiente destino, o agregar la información faltante al ambiente origen"
)

_ROOT = "$"


def parse_value(value: Any) -> Any:
    """Convierte texto a JSON si es parseable; si no, lo deja como escalar."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return value
    return value


def _serialize(value: Any) -> Any:
    """Valor de una operación: subárboles en una sola línea JSON, escalares tal cual."""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return value


def _p(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key


def _walk(
    origin: Any,
    dest: Any,
    path: str,
    updates: list[dict],
    asym: list[bool],
) -> None:
    """Acumula operaciones de diff origin→dest a lo largo de ``path``."""
    if isinstance(origin, dict) and isinstance(dest, dict):
        for key in sorted(origin):
            child = _p(path, key)
            if key not in dest:
                asym[0] = True
                updates.append({
                    "op": "insert",
                    "path": child,
                    "value": _serialize(origin[key]),
                    "json": True,
                })
            else:
                _walk(origin[key], dest[key], child, updates, asym)
        for key in dest:
            if key not in origin:
                asym[0] = True  # data en destino que el origen no posee
        return
    if isinstance(origin, list) and isinstance(dest, list):
        if origin != dest:
            updates.append({
                "op": "update",
                "path": path,
                "value": _serialize(origin),
                "json": True,
            })
        return
    if origin != dest:
        updates.append({
            "op": "update",
            "path": path,
            "value": _serialize(origin),
            "json": isinstance(origin, (dict, list)),
        })


def diff_json(origin: Any, dest: Any) -> dict:
    """Diffs JSONPath de ``origin`` vs ``dest`` (determinista, solo lo tocado).

    Devuelve ``{"updates": [...], "warning": str | None}``.

    ``updates``: lista de ``{"op": "insert"|"update", "path": "...", "value": ...}``.
    - ``insert``: key/subárbol que el origen tiene y el destino no (una línea).
    - ``update``: hoja distinta o tipo distinto (reemplazo puntual de la hoja).

    ``warning``: mensaje canónico cuando hay asimetría entre los objetos
    (inserciones, o data en destino ausente en origen).
    """
    o = parse_value(origin)
    d = parse_value(dest)
    updates: list[dict] = []
    asym: list[bool] = [False]
    _walk(o, d, _ROOT, updates, asym)
    warning = WARNING_MSG if asym[0] else None
    return {"updates": updates, "warning": warning}


def apply_updates(value: Any, updates: list[dict], *, selected: set[str] | None = None) -> Any:
    """Aplica en una copia de ``value`` solo los updates seleccionados.

    ``selected`` es el set de paths JSONPath a aplicar; ``None`` aplica todos.
    Devuelve el objeto completo con las modificaciones (para ``--value`` del
    comando ``aws ssm put-parameter``), nunca más que lo marcado.
    """
    base = parse_value(value)
    new_root: Any | None = None
    for upd in updates:
        path = upd.get("path", "")
        if selected is not None and path not in selected:
            continue
        value = upd.get("value")
        if upd.get("op") == "insert" or upd.get("json"):
            value = parse_value(value)
        if path == _ROOT:
            new_root = value
            continue
        _set_path(base, path, value)
    if new_root is not None:
        base = new_root
    return base


def _set_path(root: Any, path: str, value: Any) -> None:
    """Asigna ``value`` en ``root`` según ``path`` tipo ``$.a.b.c``."""
    parts = path.split(".") if path.startswith(_ROOT) else path.split(".")
    if parts and parts[0] == _ROOT:
        parts = parts[1:]
    node = root
    for segment in parts[:-1]:
        if isinstance(node, dict):
            node = node.setdefault(segment, {})
        else:
            return
    if parts:
        last = parts[-1]
        if isinstance(node, dict):
            node[last] = value