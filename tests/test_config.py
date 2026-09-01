from bbit_release.config import Config


def test_save_tokens_updates_existing(tmp_path, monkeypatch):
    base = tmp_path / "env.base"
    base.write_text("# header\nBITBUCKET_TOKEN=old\nKEEP=yes\n", encoding="utf-8")
    monkeypatch.setattr(Config, "_config_dir", tmp_path)

    cfg = Config()
    path = cfg.save_tokens(bitbucket_token="new", circleci_token="cci", workspace="my_ws")

    assert path == base
    text = base.read_text(encoding="utf-8")
    assert "BITBUCKET_TOKEN=new" in text
    assert "CIRCLECI_TOKEN=cci" in text
    assert "BITBUCKET_WORKSPACE=my_ws" in text
    assert "KEEP=yes" in text
    assert text.startswith("# header")


def test_save_tokens_idempotent(tmp_path, monkeypatch):
    base = tmp_path / "env.base"
    base.write_text("BITBUCKET_TOKEN=a\n", encoding="utf-8")
    monkeypatch.setattr(Config, "_config_dir", tmp_path)

    cfg = Config()
    cfg.save_tokens(bitbucket_token="b", workspace="w")
    cfg.save_tokens(bitbucket_token="c", workspace="w")

    text = base.read_text(encoding="utf-8")
    assert text.count("BITBUCKET_TOKEN=") == 1
    assert "BITBUCKET_TOKEN=c" in text


def test_save_tokens_noop_without_values(tmp_path, monkeypatch):
    base = tmp_path / "env.base"
    base.write_text("BITBUCKET_TOKEN=a\n", encoding="utf-8")
    monkeypatch.setattr(Config, "_config_dir", tmp_path)

    cfg = Config()
    cfg.save_tokens(workspace="w")

    assert base.read_text(encoding="utf-8") == "BITBUCKET_TOKEN=a\n"


def test_remove_credentials_keeps_rest(tmp_path, monkeypatch):
    base = tmp_path / "env.base"
    base.write_text(
        "BITBUCKET_WORKSPACE=ws\n"
        "BITBUCKET_TOKEN=secret\n"
        "CIRCLECI_TOKEN=secret2\n"
        "DEPLOY_PREFIXES=uat\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(Config, "_config_dir", tmp_path)

    cfg = Config()
    cfg.remove_credentials()

    text = base.read_text(encoding="utf-8")
    assert "BITBUCKET_TOKEN" not in text
    assert "CIRCLECI_TOKEN" not in text
    assert "BITBUCKET_WORKSPACE=ws" in text
    assert "DEPLOY_PREFIXES=uat" in text
    assert "\r" not in text