"""Tests del rate limiter por proveedor (BBIT-51)."""

from bbit_release.http import get_global_rate_limiter, get_rate_limiter


def test_rate_limiter_independiente_por_proveedor():
    bb = get_rate_limiter("bitbucket")
    cc = get_rate_limiter("circleci")
    assert bb is not cc

    for _ in range(4):
        bb.acquire()
    try:
        # Bitbucket con sus 4 slots ocupados NO bloquea a CircleCI.
        assert cc.acquire(timeout=1) is True
    finally:
        cc.release()
        for _ in range(4):
            bb.release()


def test_get_global_rate_limiter_alias_retrocompat():
    # Retrocompat: single-worker mode == el limiter de Bitbucket.
    assert get_global_rate_limiter() is get_rate_limiter("bitbucket")