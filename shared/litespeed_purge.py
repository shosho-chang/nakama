"""LiteSpeed Cache purge helper (ADR-005b §5).

Day 1 實測（2026-04-24，見 docs/runbooks/litespeed-purge.md）確認：LiteSpeed
plugin 透過 WP `save_post` hook 自動 invalidate cache，Usopp 走 WP REST API 建立/
更新文章時**天然觸發 auto-purge**，不需要 explicit purge call。`POST /wp-json/
litespeed/v1/purge` 這個 endpoint 根本不存在（404 `rest_no_route`）— v1/v3
namespace 沒有任何 purge route，v3 是 QUIC.cloud CDN 管理用途。

因此本模組在 Phase 1 的正解是 **noop**：呼叫 `purge_url()` 回傳 False + 記 INFO log，
不做任何事。`cache_purged=False` 在 publish_jobs 表與 `PublishResultV1.cache_purged`
是合法值，不代表失敗 — 代表「WP plugin hook 已經處理，無需 explicit call」。

模組為什麼還留著：保留 `purge_url()` 簽名 + env var + 呼叫點（`agents/usopp/
publisher.py:321`），讓 state machine 的 `cache_purged` stage 維持完整語意。若未來
有非 WP-REST 的寫入路徑（e.g. 直接改 DB / wp-cli batch 腳本），此模組是重新接上
explicit purge 的錨點；`git log shared/litespeed_purge.py` 能找回 rest method 的
歷史實作（已移除，因 endpoint 虛構）。

Environment variables read:
    LITESPEED_PURGE_METHOD   只接受 "noop"（唯一合法值）；任何其他值（含舊值
                             "rest" / "admin_ajax"）會記 WARNING 並 fallback noop。
                             預設值：noop。
"""

from __future__ import annotations

import os
from typing import Literal

from shared.log import get_logger
from shared.wordpress_client import WordPressClient

logger = get_logger("nakama.litespeed_purge")

LiteSpeedMethod = Literal["noop"]


def _get_method() -> LiteSpeedMethod:
    value = os.environ.get("LITESPEED_PURGE_METHOD", "noop").strip().lower()
    if value != "noop":
        logger.warning(
            "LITESPEED_PURGE_METHOD=%r not supported (only 'noop' valid post Day 1 "
            "2026-04-24 — WP save_post hook handles cache invalidation); using noop",
            value,
        )
    return "noop"


def purge_url(
    url: str,
    *,
    wp_client: WordPressClient | None = None,
    method: LiteSpeedMethod | None = None,
    operation_id: str = "",
) -> bool:
    """No-op cache purge call.

    Day 1 實測：WP `save_post` hook 自動處理 LiteSpeed cache invalidation，
    Usopp 走 WP REST API 的寫入路徑無需 explicit purge。呼叫此函式只為保留
    state machine `cache_purged` stage 的語意完整性。

    Args:
        url:          Permalink（僅用於 log，不呼叫 endpoint）。
        wp_client:    忽略（保留參數以維持 call site 相容）。
        method:       忽略（保留參數以維持 call site 相容）。
        operation_id: Log correlation。

    Returns:
        永遠 False。`cache_purged=False` 是正解，不是失敗。
    """
    del wp_client, method  # kept for signature compatibility, not used
    chosen = _get_method()  # always "noop"; resolves env to log warnings on bad values
    logger.info(
        "litespeed purge noop url=%s op=%s method=%s — WP plugin hook handles cache",
        url,
        operation_id,
        chosen,
    )
    return False
