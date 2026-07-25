"""ADR-024 promotion-surface wiring — thin presentation-side shim.

ADR-052：組裝知識（env 解析、mode 分支、9-service collaborator graph）下沉到
Robin 的 composition root ``agents.robin.promotion.factory``。這裡只剩
presentation 責任：載入 config → 呼叫 factory → 把 service 注入 router
（``set_service``）。``thousand_sunny.app`` 的 lifespan 介面不變：
:func:`load_promotion_wiring_config` ＋ :func:`wire_promotion_surfaces`。

歷史：N518a/b 時代這裡自持 ~120 行 adapter 建構（見 git history）。那使
``PromotionReviewService`` 的組裝知識被鎖在 web 層，CLI / 未來 agent 無法
重用 — 與 Robin CONTEXT.md 的 ownership boundary 相違。工廠化後這個檔案
的存在理由只剩「router 注入是 Sunny 的事」。

Backward-compat re-exports：``PromotionWiringConfig`` /
``load_promotion_wiring_config`` 是既有 lifespan + 測試的公開名，保留為
factory 對應物的別名。
"""

from __future__ import annotations

from agents.robin.promotion import factory as _factory
from agents.robin.promotion.factory import (
    PromotionConfig as PromotionWiringConfig,
)
from shared.log import get_logger
from thousand_sunny.routers import promotion_review, writing_assist

__all__ = [
    "PromotionWiringConfig",
    "load_promotion_wiring_config",
    "wire_promotion_surfaces",
]

_logger = get_logger("nakama.web.promotion_wiring")


def load_promotion_wiring_config() -> PromotionWiringConfig:
    """Delegate to :func:`agents.robin.promotion.factory.load_promotion_config`.

    Kept as a module-level function (not a bare re-export) so the lifespan's
    import surface stays on this module.
    """
    return _factory.load_promotion_config()


def wire_promotion_surfaces(config: PromotionWiringConfig) -> None:
    """Build services via the Robin factory and inject them into both routers.

    Called from the FastAPI lifespan when Robin is enabled. After this
    helper returns, both ``promotion_review`` and ``writing_assist``
    routers have a wired service and will return 200 (not 503).
    """
    review_service = _factory.build_promotion_review_service(config)
    promotion_review.set_service(review_service)

    writing_assist_service = writing_assist._build_default_service(
        package_root=config.reading_context_package_root,
    )
    writing_assist.set_service(writing_assist_service)

    _logger.info(
        "promotion surfaces wired",
        extra={
            "category": "promotion_wiring_ready",
            "mode": config.promotion_mode,
            "vault_root": str(config.vault_root),
            "manifest_root": str(config.manifest_root),
            "package_root": str(config.reading_context_package_root),
        },
    )
