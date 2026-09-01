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


def test_exclude_repos_property(monkeypatch):
    monkeypatch.setenv("BITBUCKET_EXCLUDE_REPOS", "Orders-App, billing-app, X")
    cfg = Config()
    assert cfg.exclude_repos == ["orders-app", "billing-app", "x"]


def test_save_filters_persists(tmp_path, monkeypatch):
    base = tmp_path / "env.base"
    base.write_text("BITBUCKET_PROJECT_PREFIXES=old\nKEEP=yes\n", encoding="utf-8")
    monkeypatch.setattr(Config, "_config_dir", tmp_path)

    cfg = Config()
    cfg.save_filters(project_prefixes="trans", exclude_repos="orders-app,billing")

    text = base.read_text(encoding="utf-8")
    assert "BITBUCKET_PROJECT_PREFIXES=trans" in text
    assert "BITBUCKET_EXCLUDE_REPOS=orders-app,billing" in text
    assert "KEEP=yes" in text
    assert text.count("BITBUCKET_PROJECT_PREFIXES=") == 1


def test_save_filters_partial_noop(tmp_path, monkeypatch):
    base = tmp_path / "env.base"
    base.write_text("BITBUCKET_PROJECT_PREFIXES=trans\n", encoding="utf-8")
    monkeypatch.setattr(Config, "_config_dir", tmp_path)

    cfg = Config()
    cfg.save_filters(exclude_repos="orders-app")

    text = base.read_text(encoding="utf-8")
    assert "BITBUCKET_PROJECT_PREFIXES=trans" in text
    assert "BITBUCKET_EXCLUDE_REPOS=orders-app" in text


def test_clear_filters_removes(tmp_path, monkeypatch):
    base = tmp_path / "env.base"
    base.write_text(
        "BITBUCKET_PROJECT_PREFIXES=trans\n"
        "BITBUCKET_EXCLUDE_REPOS=orders-app\n"
        "DEPLOY_PREFIXES=uat\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(Config, "_config_dir", tmp_path)

    cfg = Config()
    cfg.clear_filters()

    text = base.read_text(encoding="utf-8")
    assert "BITBUCKET_PROJECT_PREFIXES" not in text
    assert "BITBUCKET_EXCLUDE_REPOS" not in text
    assert "DEPLOY_PREFIXES=uat" in text