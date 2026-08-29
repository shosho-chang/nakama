"""統一組態管理：讀取 config.yaml + .env"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parent.parent
_config: dict | None = None


def _dotenv_path() -> Path:
    """Locate the nearest repo-level dotenv, including from a nested worktree."""

    for root in (_ROOT, *_ROOT.parents):
        candidate = root / ".env"
        if candidate.is_file():
            return candidate
    return _ROOT / ".env"


def load_config() -> dict:
    """載入 config.yaml，同時載入 .env 環境變數。"""
    global _config
    if _config is not None:
        return _config

    load_dotenv(_dotenv_path())

    config_path = _ROOT / "config.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        _config = yaml.safe_load(f)

    return _config


def get_vault_path() -> Path:
    """回傳 Obsidian vault 的絕對路徑。

    跨平台覆寫：`VAULT_PATH` env var 優先（讓 Windows 開發機、VPS、CI 各自指向不同位置）。
    """
    cfg = load_config()
    override = os.environ.get("VAULT_PATH")
    if override:
        return Path(override)
    return Path(cfg["vault_path"])


def get_db_path() -> Path:
    """回傳 SQLite 資料庫路徑。

    `NAKAMA_DATA_DIR` 是整個 runtime root 的明確覆寫；有設定時 DB 必須與
    token/progress 同目錄。否則才由 `DB_PATH` 覆寫。
    """
    cfg = load_config()
    runtime_root = os.environ.get("NAKAMA_DATA_DIR", "").strip()
    if runtime_root:
        return Path(runtime_root) / "state.db"
    override = os.environ.get("DB_PATH")
    if override:
        return Path(override)
    return Path(cfg["db_path"])


def get_runtime_data_dir() -> Path:
    """Resolve the shared machine runtime root for DB, OAuth tokens and progress.

    ``NAKAMA_DATA_DIR`` is the explicit operator override.  Otherwise the state
    database location is the canonical anchor, so a sibling git worktree cannot
    accidentally create its own token/progress directory.
    """

    load_config()
    configured = os.environ.get("NAKAMA_DATA_DIR", "").strip()
    if configured:
        return Path(configured)
    return get_db_path().parent


def get_agent_config(agent_name: str) -> dict:
    """取得指定 agent 的組態。"""
    cfg = load_config()
    return cfg.get("agents", {}).get(agent_name, {})
