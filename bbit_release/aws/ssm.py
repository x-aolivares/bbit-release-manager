"""Valores SSM Parameter Store con cache por cliente y conciencia de permiso.

Límites (verificados): GetParameter/GetParameters/GetParametersByPath
comparten 40 TPS por cuenta-región (default; higher throughput lleva
GetParameters a 1000 TPS). Se usa ``get_parameters`` en batches de 10
(máximo por llamada) y cache SQLite TTL 3600 para no golpear el límite
cuando varios usuarios miran el mismo plan.

La cache es permission-aware: se guarda por ``(c_id, path, decrypt)`` y el
valor descifrado de un cliente nunca entra en la vista de otro.
"""

from __future__ import annotations

import logging

from ..cache import get_cache
from .session import AwsSession, AwsSessionError

log = logging.getLogger("bbit.aws.ssm")

MAX_BATCH = 10
DEFAULT_TTL = 3600


def _chunks(paths: list[str], size: int = MAX_BATCH) -> list[list[str]]:
    return [paths[i : i + size] for i in range(0, len(paths), size)]


def fetch_values(
    session: AwsSession,
    paths: list[str],
    *,
    decrypt: bool = False,
    cache=None,
    ttl: float = DEFAULT_TTL,
    force: bool = False,
) -> dict[str, dict]:
    """Valores reales de paths SSM: ``{path: {"value", "encrypted"}}``.

    - ``get_parameters`` en batches de 10.
    - Parametros inexistentes (``InvalidParameters``) → quedan fuera del
      resultado (el caller los marca ``missing``).
    - Si hay cache: un batch completo con hit se responde de cache; lo que
      falta se pide a AWS y se persiste (``force`` saltea la cache).
    """
    if not paths:
        return {}
    unique = sorted({p for p in paths if p})
    cache = cache if cache is not None else get_cache()

    out: dict[str, dict] = {}
    if not force and cache is not None:
        cached = cache.get_ssm_values(session.client_id, unique, decrypt=decrypt, ttl=ttl)
        out.update({p: {"value": v["value"], "encrypted": v["encrypted"]} for p, v in cached.items()})

    pending = [p for p in unique if p not in out]
    fresh: dict[str, dict] = {}
    for batch in _chunks(pending):
        try:
            resp = session.client("ssm").get_parameters(
                Names=batch,
                WithDecryption=decrypt,
            )
        except AwsSessionError:
            raise
        except Exception as exc:  # boto3/botocore
            raise AwsSessionError(f"No se pudieron leer parámetros SSM: {exc}") from exc

        for p in resp.get("Parameters", []):
            name = p.get("Name", "")
            if not name:
                continue
            value = p.get("Value", "")
            info = {"value": value, "encrypted": bool(p.get("Type") == "SecureString")}
            out[name] = info
            fresh[name] = info

        invalid = [i for i in resp.get("InvalidParameters", []) if i in pending]
        if invalid:
            log.info("ssm.fetch_values: %d parámetro(s) inexistente(s): %r", len(invalid), invalid)

    if fresh and cache is not None:
        cache.set_ssm_values(session.client_id, fresh, decrypt=decrypt, ttl=ttl)

    return out


def enrich_diff_params(
    params: list[dict],
    session: AwsSession | None,
    *,
    decrypt: bool = False,
    cache=None,
    ttl: float = DEFAULT_TTL,
) -> list[dict]:
    """Aplica ``qa_value`` + ``aws_status`` a los params de /api/diff.

    - Sin sesión AWS usable → ``skipped`` con ``qa_value=None`` (comportamiento
      actual, nada se rompe).
    - Con sesión → ``ok`` (valor real) o ``missing`` (no existe en SSM).
    El resultado comparte lista y orden; solo se muta cada dict de parametro.
    """
    paths = sorted({p.get("param", "") for p in params if p.get("param")})
    if not paths or session is None or not session.available:
        for p in params:
            p["aws_status"] = "skipped"
            p["qa_value"] = None
        return params

    values = fetch_values(session, paths, decrypt=decrypt, cache=cache, ttl=ttl)
    for p in params:
        path = p.get("param", "")
        info = values.get(path)
        if info is None:
            p["aws_status"] = "missing"
            p["qa_value"] = None
        else:
            p["aws_status"] = "ok"
            p["qa_value"] = info.get("value")
    return params