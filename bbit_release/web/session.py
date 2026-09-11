"""Sesión en memoria: almacena el BitbucketClient por workspace.

El token vive SOLO en RAM del proceso uvicorn. Se pierde al reiniciar.
No se persiste, no se loguea, no va a disco ni a engram.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field

from ..bitbucket.client import BitbucketClient, BitbucketError, BitbucketAuthError
from ..cache import get_cache
from ..config import Config


@dataclass
class SessionData:
    session_id: str
    client: BitbucketClient
    workspace: str
    identity: str
    repo_count: int
    client_id: str = ""


_sessions: dict[str, SessionData] = {}


def _recorder(entry: dict) -> None:
    try:
        get_cache().record_service_call(**entry)
    except Exception:
        pass


def create_session(workspace: str, token: str, url: str = "", client_id: str = "") -> SessionData:
    client = BitbucketClient(
        workspace, 
        token, 
        url=url, 
        recorder=_recorder,
        cache=get_cache(),  # Pasar caché agresiva
    )
    try:
        info, identity = client.session()
    except (BitbucketAuthError, BitbucketError):
        client.close()
        raise
    client = _wrap_local_git(client, client_id)
    sid = secrets.token_urlsafe(16)
    data = SessionData(
        session_id=sid,
        client=client,
        workspace=info.slug,
        identity=identity,
        repo_count=0,
        client_id=client_id,
    )
    _sessions[sid] = data
    return data


def _wrap_local_git(client: BitbucketClient, client_id: str) -> BitbucketClient:
    """Si el cliente tiene carpeta de clones configurada, envuelve al cliente
    con el motor local-git (BBIT-33); si no, devuelve el cliente API tal cual
    (comportamiento previo)."""
    try:
        cfg = Config.for_client(client_id) if client_id else Config()
        clones_dir = cfg.git_clones_dir or ""
    except Exception:
        clones_dir = ""
    if not clones_dir:
        return client
    from ..localgit.client import LocalRepoClient

    return LocalRepoClient(client, clones_dir, cfg=cfg)


def get_session(sid: str) -> SessionData | None:
    return _sessions.get(sid)


def destroy_session(sid: str) -> bool:
    data = _sessions.pop(sid, None)
    if data:
        data.client.close()
        return True
    return False


def active_session_id() -> str | None:
    return next(iter(_sessions), None)