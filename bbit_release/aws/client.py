"""AWS: validación de credenciales (STS) y regiones del catálogo.

BBIT-1 usará estas credenciales para el escaneo SSM; acá solo se valida que
exista una sesión inválida de AWS (profile/región) antes de guardarla.
"""

from __future__ import annotations


def validate_credentials(profile: str = "", region: str = "") -> tuple[bool, str]:
    """Valida credenciales AWS con STS ``GetCallerIdentity``.

    Devuelve ``(ok, detalle)``. Requiere ``boto3`` instalado y credenciales
    resolubles (profile, env vars o default chain).
    """
    import boto3

    try:
        session = boto3.Session(profile_name=profile or None, region_name=region or None)
        sts = session.client("sts", region_name=region or "us-east-1")
        identity = sts.get_caller_identity()
        arn = identity.get("Arn", "?")
        account = identity.get("Account", "?")
        return True, f"STS OK — {arn} (account {account})"
    except Exception as exc:  # boto3/botocore lanzan excepciones muy variadas
        return False, str(exc)


def available_regions(cache) -> list[str]:
    """Regiones sembradas del provider AWS (catálogo editable)."""
    provider = cache.get_provider("AWS")
    if provider is None:
        return []
    details = provider.get("details") or {}
    return list(details.get("regions") or [])