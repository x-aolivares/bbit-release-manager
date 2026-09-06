"""Sesión de AWS (boto3 profile) con identidad explícita por cliente.

Multi-usuario web: la identidad se resuelve por ``(profile, region)`` del
cliente activo de la sesión del request (``service_authentication``), nunca
por un profile global/ambiental. El ``client_id`` solo se usa como clave de
cache de valores SSM (en ``aws/ssm.py``), no para resolver credenciales.
"""

from __future__ import annotations

from dataclasses import dataclass, field


SSO_HINT = "corre 'aws sso login --sso-session <session>'"


class AwsSessionError(RuntimeError):
    """Error de sesión AWS con mensaje orientado a la acción."""


@dataclass
class AwsSession:
    """Sesión boto3 construida con profile/región explícitos.

    ``client_id`` se guarda para el caller (cache SSM per-client), no se usa
    para resolver credenciales.
    """

    profile: str = ""
    region: str = ""
    client_id: str = ""
    username: str = ""
    _session: object = field(repr=False, default=None)

    def __post_init__(self) -> None:
        if not self.region:
            self.region = "us-east-1"

    # -- builders ---------------------------------------------------------------

    @classmethod
    def from_config(cls, cfg=None) -> "AwsSession":
        """Sesión desde un ``Config`` (cualquier cliente ya resuelto)."""
        from ..config import Config

        cfg = cfg or Config()
        return cls(
            profile=cfg.aws_profile,
            region=cfg.aws_region,
            client_id=cfg.client_id,
        )

    @property
    def available(self) -> bool:
        """Hay perfil configurado para probar identidad."""
        return bool(self.profile)

    # -- boto3 -------------------------------------------------------------------

    def _boto_session(self):
        if self._session is None:
            import boto3

            self._session = boto3.Session(
                profile_name=self.profile or None,
                region_name=self.region or None,
            )
        return self._session

    def client(self, service: str):
        return self._boto_session().client(service, region_name=self.region or None)

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