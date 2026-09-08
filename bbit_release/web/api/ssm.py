"""SSM View API (BBIT-2): valores por ambiente, comparador JSONPath y query.

- ``GET /api/ssm/values``       — filas de ``ssm_values`` por parámetro.
- ``PATCH /api/ssm/values``     — upsert de un valor ``(param, env)``.
- ``POST /api/ssm/compare``     — diff JSONPath entre dos ambientes.
- ``GET /api/ssm/update-query`` — comando ``aws ssm put-parameter`` con solo
                                  los cambios seleccionados.
- ``PATCH /api/ssm/environments`` — persiste ``SSM_ENVIRONMENTS``/``SSM_READ_SECRETS``.

Los valores de secretos se enmascaran en la lectura, y exponer valores de un
secreto (compare/update-query) exige ``SSM_READ_SECRETS`` (403 sin permiso).
"""

from __future__ import annotations

import json
import logging
import time

from fastapi import APIRouter, HTTPException

from ...cache import DEFAULT_AWS_REGION, get_cache
from ...config import Config
from ...ssm.jsonpath import apply_updates, diff_json, parse_value
from ...ssm.store import SsmStore
from .repos import _require_session, _session_config

log = logging.getLogger("bbit.ssm.api")

router = APIRouter(prefix="/api/ssm", tags=["ssm"])


def _store() -> SsmStore:
    return SsmStore(get_cache())


def _is_secret(param: str) -> bool:
    return any(row["is_secret"] for row in _store().for_param(param))


def _require_secret_permission(cfg: Config, param: str) -> None:
    """403 si el parámetro es secreto y no hay SSM_READ_SECRETS."""
    if _is_secret(param) and not cfg.ssm_read_secrets:
        raise HTTPException(
            403, "SSM_READ_SECRETS deshabilitado: no se pueden ver valores de secretos.",
        )


@router.get("/values")
def values(param: str, fetch: int = 0):
    """Filas de ``ssm_values`` por ambiente para ``param``.

    El valor de un secreto llega enmascarado salvo que ``SSM_READ_SECRETS``.

    ``fetch=1`` (o cuando la tabla está vacía para ese param): consulta
    SSM/LocalStack en vivo para cada ambiente configurado y cachea el
    resultado en ``ssm_values``.
    """
    if not param:
        raise HTTPException(400, "param es obligatorio")
    data = _require_session()
    cfg = _session_config(data)
    live: dict[str, dict] = {}
    rows = _store().for_param(param)

    if fetch or not rows:
        rows, live = _fetch_live(param, cfg, data)

    environments = []
    for r in rows:
        secret = r["is_secret"]
        reveal = secret and cfg.ssm_read_secrets
        if secret and not reveal:
            value, masked = None, True
        else:
            value, masked = _display_value(r["value"]), False
        environments.append({
            "environment": r["environment"],
            "value": value,
            "masked": masked,
            "is_secret": secret,
            "source": r["source"],
            "secret_arn": r["secret_arn"],
            "updated_at": r["updated_at"],
        })
    return {"param": param, "environments": environments, "live": live}


def _fetch_live(param: str, cfg, data) -> tuple[list[dict], dict[str, dict]]:
    """Consulta SSM/LocalStack en vivo para cada ambiente configurado.

    Devuelve ``(rows, live)``: las filas de ``ssm_values`` y un mapa
    ``live[env] = {"status": "ok"|"missing"|"unavailable", "changed": bool, "at": ts}``
    para que la UI muestre qué leyó en vivo y qué cambió respecto a lo guardado.

    Releer en vivo **preserva** el flag ``is_secret`` y el ``secret_arn`` de la
    fila guardada: traer el valor real no desmarca un secreto que el usuario
    haya marcado en el tablero.
    """
    from ...aws.session import AwsSession
    from ...ssm.jsonpath import parse_value

    store = _store()
    environments = cfg.aws_environments
    if not environments:
        return [], {}
    live: dict[str, dict] = {}
    now = time.time()

    for env in environments:
        name = env.get("name", "")
        if not name:
            continue
        region = env.get("region", "") or cfg.aws_region
        endpoint = env.get("endpoint_url", "")
        localstack = bool(env.get("localstack"))

        session = AwsSession(
            profile=cfg.aws_profile,
            region=region,
            endpoint_url=endpoint if localstack else "",
            access_key_id=cfg.aws_access_key_id,
            secret_access_key=cfg.aws_secret_access_key,
            session_token=cfg.aws_session_token,
        )
        if not session.available:
            live[name] = {"status": "unavailable", "changed": False, "at": None}
            continue

        try:
            resp = session.client("ssm").get_parameter(
                Name=param,
                WithDecryption=str(cfg.aws_env.get("SSM_DECRYPT") or "").strip().lower() in {"1", "true", "yes"},
            )
            value = resp.get("Parameter", {}).get("Value", "")
        except Exception:
            log.debug("ssm fetch_live: param %s no existe en %s (%s)", param, name, region)
            live[name] = {"status": "missing", "changed": False, "at": None}
            continue

        if not value:
            live[name] = {"status": "missing", "changed": False, "at": None}
            continue

        prev = store.get(param, name)
        changed = prev is not None and parse_value(prev["value"]) != parse_value(value)
        store.upsert(
            param,
            name,
            value,
            is_secret=bool(prev and prev["is_secret"]),
            source="ssm",
            secret_arn=prev["secret_arn"] if prev else None,
        )
        live[name] = {"status": "ok", "changed": changed, "at": now}

    return store.for_param(param), live


@router.patch("/values")
def patch_value(body: dict):
    """Upsert ``(param, env)`` con ``{value, is_secret?, source?, secret_arn?}``.

    ``value`` es opcional: si el body no lo trae se conserva el detalle
    guardado (toggle de ``is_secret``/metadatos sin pisar el JSON del valor).
    En ese modo la fila debe existir y hay que cambiar al menos un flag.
    """
    param = (body.get("param") or "").strip()
    environment = (body.get("environment") or "").strip()
    if not param or not environment:
        raise HTTPException(400, "param y environment son obligatorios")
    data = _require_session()
    _ = data

    store = _store()
    if "value" not in body:
        existing = store.get(param, environment)
        if existing is None:
            raise HTTPException(
                400, "No hay valor guardado: guardalo antes de togglear el secreto.",
            )
        changed = {k for k in ("is_secret", "source", "secret_arn") if k in body}
        if not changed:
            raise HTTPException(
                400, "Sin cambios: falta value o al menos un flag (is_secret/source/secret_arn).",
            )
        value = existing["value"]
        is_secret = bool(body["is_secret"]) if "is_secret" in changed else bool(existing["is_secret"])
        source = (body.get("source") if "source" in changed else existing["source"] or "ssm").strip() or "ssm"
        secret_arn = (body.get("secret_arn") if "secret_arn" in changed else existing["secret_arn"] or "").strip() or None
    else:
        value = body.get("value", "")
        is_secret = bool(body.get("is_secret"))
        source = (body.get("source") or "ssm").strip() or "ssm"
        secret_arn = (body.get("secret_arn") or "").strip() or None
    try:
        sid = store.upsert(
            param,
            environment,
            value,
            is_secret=is_secret,
            source=source,
            secret_arn=secret_arn,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "param": param, "environment": environment, "id": sid}


@router.post("/compare")
def compare(body: dict):
    """Diff JSONPath origin→dest para ``{param, origin_env, dest_env}``."""
    param = (body.get("param") or "").strip()
    origin_env = (body.get("origin_env") or "").strip()
    dest_env = (body.get("dest_env") or "").strip()
    if not (param and origin_env and dest_env):
        raise HTTPException(400, "param, origin_env y dest_env son obligatorios")
    if origin_env == dest_env:
        raise HTTPException(400, "origin_env y dest_env deben ser distintos")
    data = _require_session()
    cfg = _session_config(data)
    _require_secret_permission(cfg, param)

    store = _store()
    origin = store.get(param, origin_env)
    dest = store.get(param, dest_env)
    if origin is None or dest is None:
        raise HTTPException(
            400, f"Falta el valor de '{param}' para '{origin_env or dest_env}' (guardalo antes de comparar).",
        )
    result = diff_json(origin["value"], dest["value"])
    return {
        "param": param,
        "origin_env": origin_env,
        "dest_env": dest_env,
        **result,
    }


def _update_payload(param: str, env: str, origin: str, changes: str, cfg: Config) -> dict:
    """Payload de update: diffs origin→dest, aplica los cambios elegidos y
    construye el comando ``aws ssm put-parameter`` plus la región/endpoint/type."""
    store = _store()
    dest = store.get(param, env)
    if dest is None:
        raise HTTPException(400, f"No hay valor guardado para '{param}' en '{env}'.")
    origin_env = origin.strip()
    if origin_env and origin_env != env:
        origin_row = store.get(param, origin_env)
        if origin_row is None:
            raise HTTPException(400, f"No hay valor guardado para '{param}' en '{origin_env}'.")
        origin_value = origin_row["value"]
    else:
        origin_value = dest["value"]

    result = diff_json(origin_value, dest["value"])
    selected = {c.strip() for c in changes.split(",") if c.strip()}
    merged = apply_updates(
        dest["value"], result["updates"],
        selected=selected if selected else None,
    )

    region = (
        cfg.ssm_environments.get(env)
        or cfg.aws_region
        or DEFAULT_AWS_REGION
    )
    endpoint_url = next(
        (
            e.get("endpoint_url")
            for e in cfg.aws_environments
            if e.get("name") == env and e.get("endpoint_url")
        ),
        "",
    )
    param_type = "SecureString" if dest["is_secret"] else "String"
    value_json = json.dumps(merged, ensure_ascii=False, separators=(",", ":"))
    command = (
        f"aws ssm put-parameter --overwrite --type {param_type} "
        f"--region {region} --name {_sh_quote(param)} --value {_sh_quote(value_json)}"
    )
    if endpoint_url:
        command += f" --endpoint-url {_sh_quote(endpoint_url)}"
    return {
        "dest": dest,
        "value_json": value_json,
        "region": region,
        "endpoint_url": endpoint_url,
        "type": param_type,
        "changes": sorted(selected) if selected else [u["path"] for u in result["updates"]],
        "command": command,
    }


@router.get("/update-query")
def update_query(param: str, env: str, origin: str = "", changes: str = ""):
    """Comando ``aws ssm put-parameter`` con solo los cambios seleccionados.

    ``changes``: paths JSONPath separados por coma (de /compare). Sin ``changes``
    aplica todos. La región sale de ``SSM_ENVIRONMENTS[env]``.
    """
    if not param or not env:
        raise HTTPException(400, "param y env son obligatorios")
    data = _require_session()
    cfg = _session_config(data)
    _require_secret_permission(cfg, param)

    payload = _update_payload(param, env, origin, changes, cfg)
    return {
        "ok": True,
        "param": param,
        "env": env,
        "region": payload["region"],
        "endpoint_url": payload["endpoint_url"],
        "type": payload["type"],
        "changes": payload["changes"],
        "command": payload["command"],
    }


@router.post("/apply")
def apply(body: dict):
    """Ejecuta ``put_parameter`` contra SSM/LocalStack con los cambios elegidos.

    Mismo payload que ``/update-query`` pero en vez de devolver el comando lo
    corre contra la sesión AWS del ambiente destino (region + endpoint por
    ambiente). Devuelve el comando equivalente (para copiar) y el valor que
    quedó escrito.
    """
    param = (body.get("param") or "").strip()
    env = (body.get("env") or "").strip()
    if not param or not env:
        raise HTTPException(400, "param y env son obligatorios")
    data = _require_session()
    cfg = _session_config(data)
    _require_secret_permission(cfg, param)

    payload = _update_payload(param, env, body.get("origin") or "", body.get("changes") or "", cfg)

    from ...aws.session import AwsSession

    env_row = next((e for e in cfg.aws_environments if e.get("name") == env), None)
    localstack = bool(env_row and env_row.get("localstack"))
    session = AwsSession(
        profile=cfg.aws_profile,
        region=payload["region"],
        endpoint_url=(payload["endpoint_url"] if localstack else ""),
        access_key_id=cfg.aws_access_key_id,
        secret_access_key=cfg.aws_secret_access_key,
        session_token=cfg.aws_session_token,
    )
    if not session.available:
        raise HTTPException(
            400,
            f"Sin credenciales AWS para ejecutar la query en '{env}': configurá "
            f"AWS_PROFILE/credenciales directas (o LocalStack) para ese ambiente.",
        )
    try:
        session.client("ssm").put_parameter(
            Name=param,
            Value=payload["value_json"],
            Type=payload["type"],
            Overwrite=True,
        )
    except Exception as exc:
        log.warning("ssm apply: put_parameter falló en %s (%s): %s", env, payload["region"], exc)
        raise HTTPException(502, f"No se pudo ejecutar put_parameter sobre '{env}': {exc}")
    return {
        "ok": True,
        "param": param,
        "env": env,
        "region": payload["region"],
        "endpoint_url": payload["endpoint_url"],
        "type": payload["type"],
        "changes": payload["changes"],
        "command": payload["command"],
        "value": payload["value_json"],
    }


@router.get("/environments")
def get_environments():
    """Lista completa de ambientes → región (aws_environment + settings).

    Es la fuente que consume el gestor de ambientes de la ssm-view: incluye
    los ambientes sembrados (qa/dev) y los agregados por el usuario. ``environments``
    trae las filas completas (región, docker/localstack, endpoint URL).
    """
    data = _require_session()
    cfg = _session_config(data)
    return {
        "ok": True,
        "environments": cfg.aws_environments,
        "ssm_environments": cfg.ssm_environments,
        "ssm_read_secrets": cfg.ssm_read_secrets,
    }


@router.patch("/environments")
def patch_environments(body: dict):
    """Persiste ambientes (``environments``: filas completas, o ``ssm_environments``
    legacy: mapa env → región) y ``SSM_READ_SECRETS`` como settings."""
    data = _require_session()
    cfg = _session_config(data)
    ssm_environments = body.get("ssm_environments")
    environments = body.get("environments")
    ssm_read_secrets = body.get("ssm_read_secrets")
    if environments is None and ssm_environments is None and ssm_read_secrets is None:
        return {
            "ok": True,
            "environments": cfg.aws_environments,
            "ssm_environments": cfg.ssm_environments,
            "ssm_read_secrets": cfg.ssm_read_secrets,
        }
    try:
        cfg.save_ssm_settings(
            ssm_environments=environments
            if environments is not None
            else dict(ssm_environments)
            if isinstance(ssm_environments, dict)
            else None,
            ssm_read_secrets=bool(ssm_read_secrets)
            if ssm_read_secrets is not None
            else None,
        )
    except TypeError as exc:
        raise HTTPException(400, f"ssm_environments inválido: {exc}")
    return {
        "ok": True,
        "environments": cfg.aws_environments,
        "ssm_environments": cfg.ssm_environments,
        "ssm_read_secrets": cfg.ssm_read_secrets,
    }


def _sh_quote(value: str) -> str:
    """Envuelve en comillas simples shell escapando las que haya dentro."""
    return "'" + value.replace("'", "'\\''") + "'"


def _display_value(text: str) -> str:
    """Valor visible de una fila: subárboles JSON en una línea, escalares tal cual."""
    parsed = parse_value(text)
    if isinstance(parsed, (dict, list)):
        return json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
    return parsed