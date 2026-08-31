import pytest

from src.circleci.configyml import ensure_tag_workflows


def test_genera_config_desde_cero():
    yml, changed, added = ensure_tag_workflows(None, ["uat", "stgp"])
    assert changed is True
    assert added == ["stgp", "uat"]
    assert "jobs:" in yml and "workflows:" in yml
    assert "deploy-uat:" in yml and "deploy-stgp:" in yml
    assert "uat-deploy-on-tag:" in yml and "stgp-deploy-on-tag:" in yml
    assert yml.count("type: approval") >= 2
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


def test_sin_ambientes_error():
    with pytest.raises(ValueError):
        ensure_tag_workflows(None, [])