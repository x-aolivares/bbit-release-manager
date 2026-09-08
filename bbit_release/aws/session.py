"""Sesión de AWS (boto3) con identidad explícita por cliente.

Multi-usuario web: la identidad se resuelve por ``(profile, region)`` del
cliente activo de la sesión del request (``service_authentication``), nunca
por un profile global/ambiental. El ``client_id`` solo se usa como clave de
cache de valores SSM (en ``aws/ssm.py``), no para resolver credenciales.

Admite dos modalidades de credenciales (opcionales entre sí):
- ``profile``: perfil en ``~/.aws/config`` (SSO incluido: boto3 resuelve el
  token de ``~/.aws/sso/cache``).
- Credenciales directas (``access_key_id`` + ``secret_access_key`` +
  ``session_token`` opcional): para máquinas sin ``~/.aws`` o LocalStack.
  Tienen precedencia sobre el profile cuando vienen definidas.
``endpoint_url`` opcional apunta a un endpoint alternativo (LocalStack).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..cache import DEFAULT_AWS_REGION


SSO_HINT = "corre 'aws sso login --sso-session <session>'"


class AwsSessionError(RuntimeError):
    """Error de sesión AWS con mensaje orientado a la acción."""


@dataclass
class AwsSession:
    """Sesión boto3 construida con credenciales explícitas.

    ``client_id`` se guarda para el caller (cache SSM per-client), no se usa
    para resolver credenciales.
    """

    profile: str = ""
    region: str = ""
    client_id: str = ""
    username: str = ""
    endpoint_url: str = ""
    access_key_id: str = ""
    secret_access_key: str = ""
    session_token: str = ""
    _session: object = field(repr=False, default=None)

    def __post_init__(self) -> None:
        if not self.region:
            self.region = DEFAULT_AWS_REGION
        self.profile = (self.profile or "").strip()
        self.endpoint_url = (self.endpoint_url or "").strip()
        self.access_key_id = (self.access_key_id or "").strip()

    # -- builders ---------------------------------------------------------------

    @classmethod
    def from_config(cls, cfg=None) -> "AwsSession":
        """Sesión desde un ``Config`` (cualquier cliente ya resuelto).

        Cuando ``AWS_LOCALSTACK=1`` y no hay credenciales directas, inyecta
        credenciales dummy (``test/test``) para que boto3 NO resuelva desde
        ``~/.aws/credentials`` (que puede tener credenciales reales para el
        profile "localstack").
        """
        from ..config import Config

        cfg = cfg or Config()
        access_key_id = cfg.aws_access_key_id
        secret_access_key = cfg.aws_secret_access_key
        session_token = cfg.aws_session_token

        if cfg.aws_localstack and not access_key_id:
            access_key_id = "test"
            secret_access_key = "test"

        return cls(
            profile=cfg.aws_profile,
            region=cfg.aws_region,
            client_id=cfg.client_id,
            endpoint_url=cfg.aws_endpoint_url if cfg.aws_localstack else "",
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            session_token=session_token,
        )

    @property
    def available(self) -> bool:
        """Hay credenciales (profile o directas) para probar identidad."""
        return bool(self.profile or self.access_key_id)

    @property
    def uses_direct_credentials(self) -> bool:
        """Las credenciales directas (si vienen) tienen precedencia."""
        return bool(self.access_key_id and self.secret_access_key)

    # -- boto3 -------------------------------------------------------------------

    def _boto_session(self):
        if self._session is None:
            import boto3

            kwargs = {"region_name": self.region or None}
            if self.uses_direct_credentials:
                kwargs.update({
                    "aws_access_key_id": self.access_key_id,
                    "aws_secret_access_key": self.secret_access_key,
                    "aws_session_token": self.session_token or None,
                })
            else:
                kwargs["profile_name"] = self.profile or None
            self._session = boto3.Session(**kwargs)
        return self._session

    def client(self, service: str):
        kwargs = {"region_name": self.region or None}
        if self.endpoint_url:
            kwargs["endpoint_url"] = self.endpoint_url
        return self._boto_session().client(service, **kwargs)

    # -- identidad ---------------------------------------------------------------

    def whoami(self) -> dict:
        """STS GetCallerIdentity: arn, account, user_id (sin secretos)."""
        try:
            identity = self.client("sts").get_caller_identity()
        except Exception as exc:  # boto3/botocore lanzan excepciones muy variadas
            raise AwsSessionError(
                f"No se pudo validar la sesión AWS (profile '{self.profile}'): {exc}. {SSO_HINT}"
            ) from exc
        return {
            "arn": identity.get("Arn", "?"),
            "account": identity.get("Account", "?"),
            "user_id": identity.get("UserId", "?"),
        }

    def status(self) -> dict:
        """Estado de la sesión sin exponer secretos (para web y CLI)."""
        identity = self.whoami()
        return {
            "profile": self.profile,
            "region": self.region,
            "arn": identity["arn"],
            "account": identity["account"],
            "user_id": identity["user_id"],
        }

    # -- compat ------------------------------------------------------------------

    def close(self) -> None:
        self._session = None