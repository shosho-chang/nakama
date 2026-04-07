"""統一組態管理：讀取 config.yaml + .env"""

from pathlib import Path

import yaml
from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parent.parent
_config: dict | None = None


def load_config() -> dict:
    """載入 config.yaml，同時載入 .env 環境變數。"""
    global _config
    if _config is not None:
        return _config

    load_dotenv(_ROOT / ".env")

    config_path = _ROOT / "config.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        _config = yaml.safe_load(f)

    return _config


def get_vault_path() -> Path:
    """回傳 Obsidian vault 的絕對路徑。"""
    cfg = load_config()
    return Path(cfg["vault_path"])


def get_db_path() -> Path:
    """回傳 SQLite 資料庫路徑。"""
    cfg = load_config()
    return Path(cfg["db_path"])


def get_agent_config(agent_name: str) -> dict:
    """取得指定 agent 的組態。"""
    cfg = load_config()
    return cfg.get("agents", {}).get(agent_name, {})
