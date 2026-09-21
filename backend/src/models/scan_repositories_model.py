"""DTOs del flujo de escaneo de repositorios.

Cadena de herencia (cada nivel AGREGA campos, como el envelope en
``library/models/envelope_model.py``):

- ``ScanRepositoriesModelCreate``: base con ``name`` (lo mínimo de un repo).
- ``ScanRepositoriesModel``: agrega lo del scan (``slug``, ``tags``, ``sources``);
  hereda de Create porque un repo escaneado ES un repo con nombre.

``ScanRepositoriesResponse`` NO hereda: un scan de N repos no es un repo solo
con lista, COMPONE la lista. Se evita asi filtrar campos del repo al body.
"""

from __future__ import annotations

from enum import Enum
from typing import List

from pydantic import BaseModel, Field


class ScanRepositoriesModelCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class ScanRepositoriesModel(ScanRepositoriesModelCreate):
    slug: str
    tags: List[str]
    sources: List[SourceBranchesModel]


class ScanRepositoriesResponse(BaseModel):
    repositories: List[ScanRepositoriesModel]


class SourceBranchesModel(BaseModel):
    name: str
    tags: List[str]
    cci: List[CircleCiInfoModel]
    prs: List[PullRequestInfoModel]


class CircleCiInfoModel(BaseModel):
    name: str
    url: str
    status: PRCIStatusEnum


class PullRequestInfoModel(BaseModel):
    title: str
    target: str
    url: str
    status: PRCIStatusEnum


class PRCIStatusEnum(Enum):
    NOT_OPEN = "NOT_OPEN"
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    MERGED = "MERGED"