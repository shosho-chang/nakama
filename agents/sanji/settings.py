"""Sanji 服務設定。

secrets（app password、OAuth token）只走環境變數（.env / systemd EnvironmentFile）；
非敏感預設可放 config.yaml 的 ``agents.sanji``。缺 secrets 直接 fail-fast——
帶著錯誤憑證跑起來的靜默失效，比啟動失敗貴一百倍。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from shared.config import get_agent_config


@dataclass(frozen=True)
class SanjiConfig:
    wp_base_url: str  # https://fleet.shosho.tw
    wp_user: str  # sanji 服務帳號（也是社群成員）
    wp_app_password: str
    sanji_user_id: int  # 排除在點數經濟外的機器人 WP user id
    poll_seconds: int = 90  # 輪詢間隔（分鐘級回饋的心跳）
    fail_open_hours: int = 48  # 判定滯留自動放行門檻（漏斗⑦）


def load() -> SanjiConfig:
    cfg = get_agent_config("sanji") or {}
    wp = cfg.get("wordpress", {}) if isinstance(cfg, dict) else {}

    base = os.environ.get("GAM_WP_BASE", "") or str(wp.get("base_url", ""))
    user = os.environ.get("GAM_WP_USER", "") or str(wp.get("service_user", ""))
    password = os.environ.get("GAM_WP_APP_PASSWORD", "")
    sanji_uid = int(os.environ.get("GAM_SANJI_USER_ID", "0") or 0)

    missing = [
        name
        for name, val in [
            ("GAM_WP_BASE", base),
            ("GAM_WP_USER", user),
            ("GAM_WP_APP_PASSWORD", password),
            ("GAM_SANJI_USER_ID", sanji_uid),
        ]
        if not val
    ]
    if missing:
        raise RuntimeError(f"sanji config missing: {', '.join(missing)}（檢查 .env）")

    return SanjiConfig(
        wp_base_url=base,
        wp_user=user,
        wp_app_password=password,
        sanji_user_id=sanji_uid,
        poll_seconds=int(os.environ.get("GAM_POLL_SECONDS", "90") or 90),
        fail_open_hours=int(os.environ.get("GAM_FAIL_OPEN_HOURS", "48") or 48),
    )
