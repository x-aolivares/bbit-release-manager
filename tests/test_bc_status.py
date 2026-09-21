"""Tests del enum de estados controlados."""

from __future__ import annotations

from backend.enums import BCStatusEnum


def test_ok_status_attributes() -> None:
    assert BCStatusEnum.OK.code == "BBIT-000"
    assert BCStatusEnum.OK.description == "todos los procesos se ejecutaron correctamente"
    assert BCStatusEnum.OK.httpStatus == 200


def test_internal_error_status_attributes() -> None:
    assert BCStatusEnum.INTERNAL_ERROR.code == "BBIT-999"
    assert BCStatusEnum.INTERNAL_ERROR.description == "ocurrió un error inesperado en el servidor"
    assert BCStatusEnum.INTERNAL_ERROR.httpStatus == 500