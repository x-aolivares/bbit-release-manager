"""Motor local-git (BBIT-33): lecturas de repos/ramas/tags/diff/params sobre
clones locales, con fallback a la API Bitbucket por repo.
"""

from .client import LocalRepoClient

__all__ = ["LocalRepoClient"]