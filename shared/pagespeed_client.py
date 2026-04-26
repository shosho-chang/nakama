"""Google PageSpeed Insights API v5 thin wrapper（ADR-009 Phase 1.5 Slice D.1）。

**職責邊界**：只做 API key auth + raw run query。返回 PageSpeed 原始 JSON
response（dict）；LCP / INP / CLS / SEO score 抽取由 `shared/seo_audit/performance.py`
負責（保持 thin wrapper 與 `gsc_client.py` 同風格）。

**為何不用 tenacity**：對齊 `gsc_client.py` — codebase 既有 clients 都自捲 retry
loop 或依賴 SDK 內建 retry；加 tenacity 會引入新 dep 且與既有 pattern 不一致。
本檔自捲簡短 retry：4xx 不 retry、5xx + 連線錯誤 exp backoff 重試 2 次。

Environment:
    PAGESPEED_INSIGHTS_API_KEY  GCP API key（啟用 PageSpeed Insights API 後取得）。
                                setup 流程見
                                `docs/runbooks/setup-wp-integration-credentials.md` §2e。
"""

from __future__ import annotations

import os
import time
from typing import Any, Literal

import httpx

from shared.log import get_logger

logger = get_logger("nakama.pagespeed_client")

_API_BASE = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
_DEFAULT_CATEGORIES: tuple[str, ...] = ("PERFORMANCE", "SEO", "BEST_PRACTICES", "ACCESSIBILITY")
_DEFAULT_TIMEOUT = 60.0  # 秒；PageSpeed 真實跑 lighthouse 慢，比 GSC 寬鬆
_MAX_ATTEMPTS = 3
_BACKOFF_BASE = 10.0  # 第 n 次失敗等 10 * 2^(n-1) 秒：10s / 20s
_BACKOFF_CAP = 30.0  # 上限避免無限增長

Strategy = Literal["mobile", "desktop"]


class PageSpeedCredentialsError(RuntimeError):
    """PAGESPEED_INSIGHTS_API_KEY 未設定。"""


class PageSpeedClient:
    """Thin wrapper — **不包含** business logic（threshold 判定 / aggregation 等），
    那屬 `shared/seo_audit/performance.py`。
    """

    def __init__(
        self,
        api_key: str | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        resolved = api_key or os.environ.get("PAGESPEED_INSIGHTS_API_KEY")
        if not resolved:
            raise PageSpeedCredentialsError(
                "PAGESPEED_INSIGHTS_API_KEY env var not set; "
                "see docs/runbooks/setup-wp-integration-credentials.md §2e"
            )
        self._api_key = resolved
        self._timeout = timeout

    @classmethod
    def from_env(cls) -> "PageSpeedClient":
        """從 `PAGESPEED_INSIGHTS_API_KEY` env var 建構。缺 env 報錯。"""
        return cls()

    def run(
        self,
        url: str,
        *,
        strategy: Strategy = "mobile",
        categories: tuple[str, ...] = _DEFAULT_CATEGORIES,
    ) -> dict[str, Any]:
        """Run PageSpeed Insights audit on `url`.

        Args:
            url: 目標頁面絕對 URL。
            strategy: "mobile" 或 "desktop"；預設 mobile（mobile-first index）。
            categories: 要跑的 lighthouse 類別；預設四項全跑。

        Returns:
            Raw API response（consumer 負責抽 audits / categories scores）。

        Raises:
            ValueError: strategy 非 "mobile" / "desktop"。
            httpx.HTTPStatusError: 4xx 不 retry 直接 raise；5xx 重試 2 次後仍失敗。
            httpx.RequestError: 連線錯誤重試 2 次後仍失敗。
        """
        if strategy not in ("mobile", "desktop"):
            raise ValueError(f"strategy must be 'mobile' or 'desktop', got {strategy!r}")

        params: list[tuple[str, str]] = [
            ("url", url),
            ("key", self._api_key),
            ("strategy", strategy),
        ]
        params.extend(("category", c) for c in categories)

        last_exc: Exception | None = None
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                response = httpx.get(_API_BASE, params=params, timeout=self._timeout)
                response.raise_for_status()
                logger.info(
                    "pagespeed_run_ok url=%s strategy=%s attempt=%d",
                    url,
                    strategy,
                    attempt,
                )
                return response.json()
            except httpx.HTTPStatusError as e:
                status = e.response.status_code
                if 400 <= status < 500:
                    logger.error(
                        "pagespeed_run_4xx url=%s status=%d body=%s",
                        url,
                        status,
                        e.response.text[:300],
                    )
                    raise
                last_exc = e
                logger.warning(
                    "pagespeed_run_5xx url=%s status=%d attempt=%d",
                    url,
                    status,
                    attempt,
                )
            except httpx.RequestError as e:
                last_exc = e
                logger.warning(
                    "pagespeed_run_neterr url=%s err=%s attempt=%d",
                    url,
                    e,
                    attempt,
                )

            if attempt < _MAX_ATTEMPTS:
                wait = min(_BACKOFF_BASE * (2 ** (attempt - 1)), _BACKOFF_CAP)
                time.sleep(wait)

        assert last_exc is not None
        logger.error("pagespeed_run_giveup url=%s after=%d attempts", url, _MAX_ATTEMPTS)
        raise last_exc
