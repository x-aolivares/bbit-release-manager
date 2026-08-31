"""Genera o actualiza `.circleci/config.yml` con workflows de deploy por tag.

Cuando se crea un tag `{env}-{n}` (generado por la funcionalidad de tags por
ambiente) y el repo tiene este config, CircleCI lanza el workflow
`{env}-deploy-on-tag`, que queda en espera hasta que alguien lo aprueba
manualmente (`type: approval`) y recién ahí corre el job `deploy-{env}`.

Los workflows por tag se filtran con `filters.tags` en CADA job del workflow:
CircleCI 2.1 no admite `triggers[].tags` (la clave `triggers` solo acepta
`schedule`/`webhook`). Para que el workflow corra solo por tag, todos sus jobs
llevan `filters.tags.only /^{env}-[0-9]+$/` + `branches.ignore: /.*/`.

El merge respeta el config existente: solo agrega jobs/workflows faltantes y
reparar los workflows `{env}-deploy-on-tag` que ya estuvieran generados con la
forma inválida (con `triggers`), reemplazándolos por la forma con filtros.
"""

from __future__ import annotations

import re

import yaml


def _job_snippet(env: str) -> dict:
    return yaml.safe_load(
        f"""docker:
  - image: cimg/base:2024.05
steps:
  - checkout
  - run:
      name: "Desplegar {env} desde $CIRCLE_TAG"
      command: |
        echo "Deploy manual de $CIRCLE_TAG en {env}"
        # TODO: reemplazar estos pasos por el deploy real de {env}
"""
    )


def _tag_filter(env: str, regex: str) -> dict:
    return {
        "filters": {
            "tags": {"only": regex},
            "branches": {"ignore": "/.*/"},
        }
    }


def _workflow_snippet(env: str, regex: str) -> dict:
    tag_filter = _tag_filter(env, regex)
    return {
        "jobs": [
            {"approve": {"type": "approval", **tag_filter}},
            {f"deploy-{env}": {"requires": ["approve"], **tag_filter}},
        ],
    }


def _has_tag_filter(wf: object, regex: str) -> bool:
    """True si `wf` ya corre por tag: algún job tiene `filters.tags.only` con el regex."""
    if not isinstance(wf, dict):
        return False
    for job_ref in wf.get("jobs") or []:
        if not isinstance(job_ref, dict):
            continue
        for cfg_part in job_ref.values():
            if not isinstance(cfg_part, dict):
                continue
            tags = (cfg_part.get("filters") or {}).get("tags") or {}
            only = tags.get("only")
            entries = only if isinstance(only, list) else [only]
            if regex in entries:
                return True
    return False


def ensure_tag_workflows(existing: str | None, envs: list[str]) -> tuple[str, bool, list[str]]:
    """Devuelve (yaml, changed, added_envs).

    - `existing` vacío/None → genera el config desde cero.
    - Si el config ya tiene el job y un workflow por ambiente con filtros de tag,
      no cambia nada.
    - Un workflow `{env}-deploy-on-tag` existente con la forma inválida
      (`triggers[].tags`) se reemplaza por la forma con `filters` en cada job.
    - Un YAML existente inválido lanza ValueError (no se sobrescribe).
    """
    envs = sorted({e.strip().lower() for e in envs if e.strip()})
    if not envs:
        raise ValueError("Sin ambientes para generar workflows")

    changed = False
    added: list[str] = []

    if not existing or not existing.strip():
        cfg: dict = {"version": 2.1}
    else:
        try:
            cfg = yaml.safe_load(existing)
        except yaml.YAMLError as exc:
            raise ValueError(f"config.yml inválido: {exc}") from exc
        if not isinstance(cfg, dict):
            raise ValueError("config.yml no es un documento YAML de mapa")

    cfg.setdefault("version", 2.1)
    jobs = cfg.setdefault("jobs", {})
    workflows = cfg.setdefault("workflows", {})

    for env in envs:
        workflow = f"{env}-deploy-on-tag"
        regex = f"/^{re.escape(env)}-[0-9]+$/"

        if f"deploy-{env}" not in jobs:
            jobs[f"deploy-{env}"] = _job_snippet(env)
            changed = True

        wf_exists = any(_has_tag_filter(wf, regex) for wf in workflows.values())
        if not wf_exists:
            workflows[workflow] = _workflow_snippet(env, regex)
            changed = True

        if not wf_exists:
            added.append(env)

    out = yaml.safe_dump(cfg, sort_keys=False)
    if not changed:
        return out, False, []
    return out, True, added