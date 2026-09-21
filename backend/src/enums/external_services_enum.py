"""Servicios externos que el backend consulta."""

from __future__ import annotations

from enum import Enum


class ExternalService(str, Enum):
    BITBUCKET = "bitbucket"
    CIRCLECI = "circleci"
    AWS = "aws"
    LOCALGIT = "localgit"