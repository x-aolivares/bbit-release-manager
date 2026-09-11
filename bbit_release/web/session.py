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


def _preload_repos_background(client: BitbucketClient, workspace: str) -> None:
    """Precarga repos en background en un thread separado.
    
    Si el cliente es LocalRepoClient (clones configurados), clona los repos
    para que luego las operaciones lean localmente (zero API calls).
    
    Si es solo BitbucketClient, cachea los repos en SQLite.
    
    Si falla, se ignora silenciosamente (no rompe la sesión).
    """
    import threading
    import logging
    
    log = logging.getLogger("bbit.session")
    
    def _load():
        try:
            repos = None
            if hasattr(client, 'list_repos'):
                # Cachear repos (paginación a Bitbucket)
                repos = client.list_repos(prefixes=None)
                log.info(f"Precache: {len(repos) if repos else 0} repos obtenidos")
            
            # Si es LocalRepoClient, clonar repos en paralelo en background
            # para que repos_with_branch() lea localmente (zero 429s)
            from ..localgit.client import LocalRepoClient
            if isinstance(client, LocalRepoClient):
                log.info(f"Usando LocalRepoClient, clones_dir: {client._clones_dir}")
                if repos:
                    log.info(f"Iniciando clone en background de {len(repos)} repos...")
                    cloned = 0
                    failed = 0
                    # Reutilizar repos ya obtenidos (no repetir paginación)
                    for repo in repos:
                        try:
                            # ensure_repo clona o fetch incremental
                            ok = client.ensure_repo(repo.slug)
                            if ok:
                                cloned += 1
                                log.debug(f"✓ {repo.slug} clonado/actualizado")
                            else:
                                failed += 1
                                log.debug(f"✗ {repo.slug} clone falló")
                        except Exception as exc:
                            failed += 1
                            log.warning(f"✗ {repo.slug} excepción: {exc}")
                    log.info(f"Clone background terminado: {cloned} OK, {failed} fallidos")
                else:
                    log.warning("No hay repos para clonar (repos es None)")
            else:
                log.info("Cliente NO es LocalRepoClient, usando solo API")
        except Exception as exc:
            log.warning(f"Preload repos background falló (no-critical): {exc}")
    
    thread = threading.Thread(target=_load, daemon=True)
    thread.start()


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
    
    # Precarga repos en background (BBIT-36: evitar espera al filtrar)
    _preload_repos_background(client, info.slug)
    
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