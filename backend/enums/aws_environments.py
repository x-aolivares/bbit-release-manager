"""Ambientes de AWS disponibles."""

from __future__ import annotations

from enum import Enum


class AwsEnvironment(str, Enum):
    QA = "qa"
    UAT = "uat"
    STGP = "stgp"