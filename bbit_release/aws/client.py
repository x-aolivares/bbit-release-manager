"""AWS: validación de credenciales (STS) y regiones del catálogo.

BBIT-1 usará estas credenciales para el escaneo SSM; acá solo se valida que
exista una sesión inválida de AWS (profile/región) antes de guardarla.
"""

from __future__ import annotations


def validate_credentials(
    profile: str = "",
    region: str = "",
    endpoint_url: str = "",
    access_key_id: str = "",
    secret_access_key: str = "",
    session_token: str = "",
) -> tuple[bool, str]:
    """Valida credenciales AWS con STS ``GetCallerIdentity``.

    Devuelve ``(ok, detalle)``. Requiere ``boto3`` instalado y credenciales
    resolubles (profile, directas o default chain). Las credenciales directas
    tienen precedencia cuando vienen definidas; ``endpoint_url`` opcional
    apunta a un endpoint alternativo (LocalStack).
    """
    import boto3

    try:
        session_kwargs = {"region_name": region or None}
        if access_key_id and secret_access_key:
            session_kwargs.update({
                "aws_access_key_id": access_key_id,
                "aws_secret_access_key": secret_access_key,
                "aws_session_token": session_token or None,
            })
        else:
            session_kwargs["profile_name"] = profile or None
        session = boto3.Session(**session_kwargs)
        client_kwargs = {"region_name": region or "us-east-1"}
        if endpoint_url:
            client_kwargs["endpoint_url"] = endpoint_url
        sts = session.client("sts", **client_kwargs)
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