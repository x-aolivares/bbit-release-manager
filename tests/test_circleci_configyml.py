import pytest

from bbit_release.circleci.configyml import ensure_tag_workflows


def test_genera_config_desde_cero():
    yml, changed, added = ensure_tag_workflows(None, ["uat", "stgp"])
    assert changed is True
    assert added == ["stgp", "uat"]
    assert "jobs:" in yml and "workflows:" in yml
    assert "deploy-uat:" in yml and "deploy-stgp:" in yml
    assert "uat-deploy-on-tag:" in yml and "stgp-deploy-on-tag:" in yml
    assert yml.count("type: approval") >= 2
    assert yml.count("filters:") >= 2
    assert "triggers:" not in yml
    assert "requires:" in yml
    assert "/^uat-[0-9]+$/" in yml and "/^stgp-[0-9]+$/" in yml


def test_merge_respeta_config_existente():
    existing = """\
version: 2.1
orbs:
  slack: circleci/slack@4.12.1
jobs:
  build:
    docker:
      - image: cimg/node:20
    steps:
      - checkout
      - run: npm ci
workflows:
  build-only:
    jobs:
      - build
"""
    yml, changed, added = ensure_tag_workflows(existing, ["uat"])
    assert changed is True
    assert added == ["uat"]
    assert "orbs:" in yml and "slack: circleci/slack@4.12.1" in yml
    assert "build:" in yml
    assert "build-only:" in yml
    assert "deploy-uat:" in yml
    assert "uat-deploy-on-tag:" in yml


def test_idempotente_cuando_ya_existe():
    first, _, _ = ensure_tag_workflows(None, ["uat"])
    yml, changed, added = ensure_tag_workflows(first, ["uat"])
    assert changed is False
    assert added == []
    assert yml.strip() == first.strip()


def test_invalido_lanza_error():
    with pytest.raises(ValueError):
        ensure_tag_workflows("version: 2.1\nkey: [1, 2", ["uat"])


def test_remigra_forma_con_triggers_invalida():
    broken = """\
version: 2.1
jobs:
  deploy-uat:
    docker:
      - image: cimg/base:2024.05
    steps:
      - checkout
workflows:
  uat-deploy-on-tag:
    triggers:
      - tags:
          only:
            - /^uat-[0-9]+$/
    jobs:
      - approve:
          type: approval
      - deploy-uat:
          requires:
            - approve
"""
    yml, changed, added = ensure_tag_workflows(broken, ["uat"])
    assert changed is True
    assert added == ["uat"]
    assert "triggers:" not in yml
    assert yml.count("filters:") == 2
    assert "only: /^uat-[0-9]+$/" in yml
    assert "ignore: /.*/" in yml


def test_sin_ambientes_error():
    with pytest.raises(ValueError):
        ensure_tag_workflows(None, [])