"""Sesión en memoria: almacena el BitbucketClient por workspace.

El token vive SOLO en RAM del proceso uvicorn. Se pierde al reiniciar.
No se persiste, no se loguea, no va a disco ni a engram.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field

from ..bitbucket.client import BitbucketClient, BitbucketError, BitbucketAuthError


@dataclass
class SessionData:
    session_id: str
    client: BitbucketClient
    workspace: str
    identity: str
    repo_count: int


_sessions: dict[str, SessionData] = {}


def create_session(workspace: str, token: str, url: str = "") -> SessionData:
    client = BitbucketClient(workspace, token, url=url)
    try:
        info, identity, repos = client.session()
    except (BitbucketAuthError, BitbucketError):
        client.close()
        raise
    sid = secrets.token_urlsafe(16)
    data = SessionData(
        session_id=sid,
        client=client,
        workspace=info.slug,
        identity=identity,
        repo_count=len(repos),
    )
    _sessions[sid] = data
    return data


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