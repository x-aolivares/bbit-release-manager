"""Genera o actualiza `.circleci/config.yml` con workflows de deploy por tag.

Cuando se crea un tag `{env}-{n}` (generado por la funcionalidad de tags por
ambiente) y el repo tiene este config, CircleCI lanza el workflow
`{env}-deploy-on-tag`, que queda en espera hasta que alguien lo aprueba
manualmente (`type: approval`) y recién ahí corre el job `deploy-{env}`.

El merge respeta el config existente: solo agrega jobs/workflows faltantes,
sin tocar jobs, orbs ni workflows que ya estaban definidos.
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


def ensure_tag_workflows(existing: str | None, envs: list[str]) -> tuple[str, bool, list[str]]:
    """Devuelve (yaml, changed, added_envs).

    - `existing` vacío/None → genera el config desde cero.
    - Si el config ya tiene el job y el workflow por environment, no cambia nada.
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
        job = f"deploy-{env}"
        workflow = f"{env}-deploy-on-tag"
        regex = f"/^{re.escape(env)}-[0-9]+$/"

        job_added = job not in jobs
        if job_added:
            jobs[job] = _job_snippet(env)
            changed = True

        wf_exists = False
        for key, wf in workflows.items():
            if not isinstance(wf, dict):
                continue
            for trigger in wf.get("triggers") or []:
                tags = (trigger or {}).get("tags") or {}
                only = tags.get("only") or []
                if regex in only:
                    wf_exists = True
                    break
            if wf_exists:
                break

        if not wf_exists:
            workflows[workflow] = {
                "triggers": [{"tags": {"only": [regex]}}],
                "jobs": [
                    {"approve": {"type": "approval"}},
                    {job: {"requires": ["approve"]}},
                ],
            }
            changed = True

        if job_added or not wf_exists:
            added.append(env)

    out = yaml.safe_dump(cfg, sort_keys=False)
    if not changed:
        return out, False, []
    return out, True, added