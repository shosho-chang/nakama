"""shared.config — VAULT_PATH / DB_PATH 載入順序 regression."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _reset_config(monkeypatch):
    """Clear cached _config + isolate VAULT_PATH/DB_PATH from caller env."""
    import shared.config as config

    config._config = None
    monkeypatch.delenv("VAULT_PATH", raising=False)
    monkeypatch.delenv("DB_PATH", raising=False)
    yield
    config._config = None


def _isolate_root(tmp_path, monkeypatch, *, env_text: str, yaml_text: str):
    import shared.config as config

    (tmp_path / ".env").write_text(env_text, encoding="utf-8")
    (tmp_path / "config.yaml").write_text(yaml_text, encoding="utf-8")
    monkeypatch.setattr(config, "_ROOT", tmp_path)


def test_get_vault_path_reads_dotenv_when_shell_unset(tmp_path, monkeypatch):
    """Regression: load_dotenv must run BEFORE os.environ.get('VAULT_PATH').

    Pre-fix bug: env override read os.environ first; load_dotenv ran later
    inside load_config — only on yaml fallback path. Result: VAULT_PATH set in
    .env was never visible to the env override branch (silent fall through to
    yaml).
    """
    import shared.config as config

    _isolate_root(
        tmp_path,
        monkeypatch,
        env_text="VAULT_PATH=/dotenv/vault\n",
        yaml_text="vault_path: /yaml/vault\ndb_path: /yaml/db\n",
    )
    assert config.get_vault_path() == Path("/dotenv/vault")


def test_get_vault_path_falls_back_to_yaml(tmp_path, monkeypatch):
    import shared.config as config

    _isolate_root(
        tmp_path,
        monkeypatch,
        env_text="",
        yaml_text="vault_path: /yaml/vault\ndb_path: /yaml/db\n",
    )
    assert config.get_vault_path() == Path("/yaml/vault")


def test_get_vault_path_shell_export_beats_dotenv(tmp_path, monkeypatch):
    """OS env (e.g. systemd Environment=) overrides .env — load_dotenv default no-override."""
    import shared.config as config

    _isolate_root(
        tmp_path,
        monkeypatch,
        env_text="VAULT_PATH=/dotenv/vault\n",
        yaml_text="vault_path: /yaml/vault\ndb_path: /yaml/db\n",
    )
    monkeypatch.setenv("VAULT_PATH", "/shell/vault")
    assert config.get_vault_path() == Path("/shell/vault")


def test_get_db_path_reads_dotenv_when_shell_unset(tmp_path, monkeypatch):
    import shared.config as config

    _isolate_root(
        tmp_path,
        monkeypatch,
        env_text="DB_PATH=/dotenv/db\n",
        yaml_text="vault_path: /yaml/vault\ndb_path: /yaml/db\n",
    )
    assert config.get_db_path() == Path("/dotenv/db")


def test_get_db_path_falls_back_to_yaml(tmp_path, monkeypatch):
    import shared.config as config

    _isolate_root(
        tmp_path,
        monkeypatch,
        env_text="",
        yaml_text="vault_path: /yaml/vault\ndb_path: /yaml/db\n",
    )
    assert config.get_db_path() == Path("/yaml/db")
