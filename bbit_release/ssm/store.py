"""Almacén persistente ``ssm_values`` por (parámetro, ambiente).

Tablero de trabajo de la SSM View (BBIT-2): guarda el JSON del valor de un
parámetro SSM para cada ambiente/región. Se alimenta desde la web (PATCH de
valores por ambiente) y alimenta el comparador de ambientes y el query de
``aws ssm put-parameter``.

La tabla es global (sin ``c_id``): es un tablero de procesamiento, no un
vault de credenciales. La lectura de secretos se regula aparte con el setting
``SSM_READ_SECRETS``.
"""

from __future__ import annotations

import json
import logging
import time

from .jsonpath import parse_value

log = logging.getLogger("bbit.ssm.store")


class SsmStore:
    """Operaciones sobre ``ssm_values`` usando la conexión thread-safe del cache."""

    def __init__(self, cache):
        self._cache = cache

    # -- helpers ---------------------------------------------------------------

    def _row(self, row: tuple) -> dict:
        return {
            "id": row[0],
            "param": row[1],
            "environment": row[2],
            "is_secret": bool(row[3]),
            "source": row[4],
            "secret_arn": row[5],
            "value": row[6],
            "created_at": row[7],
            "updated_at": row[8],
        }

    # -- escritura -------------------------------------------------------------

    def upsert(
        self,
        param: str,
        environment: str,
        value,
        *,
        is_secret: bool = False,
        source: str = "ssm",
        secret_arn: str | None = None,
    ) -> int:
        """Upsert de un valor por ``(param, environment)`` (UNIQUE index).

        ``value`` se serializa a JSON de práctica (dumps determinístico sin
        espacios en blanco); los escalares se guardan como su representación
        text (un string JSON sigue siendo guardado como JSON válido).
        """
        if not param or not environment:
            raise ValueError("param y environment son obligatorios")
        parsed: Any = parse_value(value) if isinstance(value, str) else value
        details = json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
        now = time.time()
        return self._cache._insert(
            "INSERT INTO ssm_values "
            "(sv_name, sv_environment, sv_is_secret, sv_source, sv_secret_arn, "
            " sv_details, sv_created_at, sv_updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(sv_name, sv_environment) DO UPDATE SET "
            "  sv_is_secret = excluded.sv_is_secret, "
            "  sv_source = excluded.sv_source, "
            "  sv_secret_arn = excluded.sv_secret_arn, "
            "  sv_details = excluded.sv_details, "
            "  sv_updated_at = excluded.sv_updated_at",
            (
                param,
                environment,
                1 if is_secret else 0,
                source,
                secret_arn,
                details,
                now,
                now,
            ),
        )

    def delete(self, param: str, environment: str) -> bool:
        with self._cache._lock:
            cur = self._cache._conn.cursor()
            try:
                cur.execute(
                    "DELETE FROM ssm_values WHERE sv_name = ? AND sv_environment = ?",
                    (param, environment),
                )
                n = cur.rowcount
            finally:
                cur.close()
            self._cache._conn.commit()
        return n > 0

    # -- lectura ---------------------------------------------------------------

    def get(self, param: str, environment: str) -> dict | None:
        row = self._cache._fetchone(
            "SELECT sv_id, sv_name, sv_environment, sv_is_secret, sv_source, "
            "  sv_secret_arn, sv_details, sv_created_at, sv_updated_at "
            "FROM ssm_values WHERE sv_name = ? AND sv_environment = ?",
            (param, environment),
        )
        return self._row(row) if row else None

    def for_param(self, param: str) -> list[dict]:
        """Todas las filas de un parámetro, ordenadas por ambiente."""
        rows = self._cache._fetchall(
            "SELECT sv_id, sv_name, sv_environment, sv_is_secret, sv_source, "
            "  sv_secret_arn, sv_details, sv_created_at, sv_updated_at "
            "FROM ssm_values WHERE sv_name = ? ORDER BY sv_environment",
            (param,),
        )
        return [self._row(r) for r in rows]

    def all(self) -> list[dict]:
        rows = self._cache._fetchall(
            "SELECT sv_id, sv_name, sv_environment, sv_is_secret, sv_source, "
            "  sv_secret_arn, sv_details, sv_created_at, sv_updated_at "
            "FROM ssm_values ORDER BY sv_name, sv_environment",
        )
        return [self._row(r) for r in rows]

    # -- fachada sobre el payload del diff -------------------------------------

    def enrich_param(self, param: str, *, read_secrets: bool = False) -> dict:
        """Resumen por ambiente de un parámetro para la tabla del diff.

        Devuelve ``{"type": "secret"|"ssm", "env_values": {env: value}}`` con
        los valores de ambientes secretos enmascarados salvo que ``read_secrets``.
        """
        env_values: dict[str, str] = {}
        is_secret = False
        for row in self.for_param(param):
            if row["is_secret"]:
                is_secret = True
            if row["environment"] in env_values:
                continue
            if row["is_secret"] and not read_secrets:
                env_values[row["environment"]] = "••••••"
            else:
                value = parse_value(row["value"])
                env_values[row["environment"]] = _display(value)
        return {
            "type": "secret" if is_secret else "ssm",
            "env_values": env_values,
        }


def _display(value) -> str:
    """Representación compacta del valor para la tabla (subárboles en una línea)."""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return value