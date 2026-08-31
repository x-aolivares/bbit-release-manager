from fastapi import APIRouter

from ..._version import read_version
from ..session import active_session_id, get_session

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
def health():
    sid = active_session_id()
    data = get_session(sid) if sid else None
    return {
        "status": "ok",
        "version": read_version(),
        "connected": data is not None,
        "workspace": data.workspace if data else None,
        "identity": data.identity if data else None,
        "repo_count": data.repo_count if data else 0,
    }