"""Ambientes de AWS disponibles."""

from __future__ import annotations

from enum import Enum


class AwsEnvironment(Enum):
    QA = ("qa", "us-west-1")
    DEV = ("dev", "us-west-2")
    UAT = ("uat", "us-east-1")
    STGP = ("stgp", "us-east-2")

    def __init__(self, env: str, region: str) -> None:
        self.env = env
        self.region = region