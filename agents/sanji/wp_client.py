"""fleet-gamification plugin 窄 REST API 的 client（唯一 production 通道）。

認證：WP Application Password（HTTPS basic auth，帳號 = sanji 服務帳號）。
所有 production 讀寫走這裡——絕不直連 MySQL 寫（會繞過 WP 序列化/快取/hook 副作用）。

重試策略：網路錯誤與 5xx 指數退避重試；4xx 不重試（是我們的 bug 或權限問題）。
503 = gam_enabled 止血開關關閉——特殊處理成 GamDisabled，loop 收到後安靜等下一輪。
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from shared.log import get_logger

logger = get_logger("nakama.sanji.wp")

_RETRIES = 3
_BACKOFF_BASE = 2.0  # 秒；2 → 4 → 8


class GamAPIError(RuntimeError):
    """REST 呼叫在重試後仍失敗。"""

    def __init__(self, message: str, *, status: int = 0):
        super().__init__(message)
        self.status = status


class GamDisabled(GamAPIError):
    """gam_enabled=0——止血開關關閉中，不是故障。"""


class WPClient:
    def __init__(self, base_url: str, user: str, app_password: str, *, timeout: float = 30.0):
        self._client = httpx.Client(
            base_url=base_url.rstrip("/") + "/wp-json/nakama-gam/v1",
            auth=(user, app_password),
            timeout=timeout,
            headers={"User-Agent": "nakama-sanji/0.1"},
        )

    def close(self) -> None:
        self._client.close()

    # ── low level ────────────────────────────────────────────────
    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        last_exc: Exception | None = None
        for attempt in range(1, _RETRIES + 1):
            try:
                res = self._client.request(method, path, **kwargs)
            except httpx.HTTPError as exc:  # 連線層錯誤 → 退避重試
                last_exc = exc
                logger.warning(f"[wp] {method} {path} network error ({attempt}/{_RETRIES}): {exc}")
            else:
                if res.status_code == 503:
                    raise GamDisabled("gam_enabled=0 (kill switch)", status=503)
                if res.status_code < 500:
                    if res.status_code >= 400:
                        raise GamAPIError(
                            f"{method} {path} → {res.status_code}: {res.text[:300]}",
                            status=res.status_code,
                        )
                    return res.json()
                last_exc = GamAPIError(
                    f"{method} {path} → {res.status_code}", status=res.status_code
                )
                logger.warning(f"[wp] {method} {path} {res.status_code} ({attempt}/{_RETRIES})")
            if attempt < _RETRIES:
                time.sleep(_BACKOFF_BASE**attempt)
        raise GamAPIError(f"{method} {path} failed after {_RETRIES} tries: {last_exc}")

    # ── endpoints ────────────────────────────────────────────────
    def health(self) -> dict:
        return self._request("GET", "/health")

    def events(self, after_id: int, *, limit: int = 200, types: str = "") -> dict:
        params: dict[str, Any] = {"after_id": after_id, "limit": limit}
        if types:
            params["types"] = types
        return self._request("GET", "/events", params=params)

    def reactions(self, after_id: int, *, types: str = "bookmark", limit: int = 200) -> dict:
        return self._request(
            "GET", "/reactions", params={"after_id": after_id, "types": types, "limit": limit}
        )

    def feed(self, feed_id: int) -> dict:
        return self._request("GET", f"/feeds/{feed_id}")

    def comment(self, feed_id: int, text: str) -> dict:
        return self._request("POST", "/comments", json={"feed_id": feed_id, "comment": text})

    def grants(self, items: list[dict]) -> dict:
        """批次入帳（≤100/批；plugin 端 idempotency 保證重放安全）。"""
        if len(items) > 100:
            raise ValueError("max 100 grants per batch")
        return self._request("POST", "/grants", json={"grants": items})

    def balance(self, user_id: int, *, rebuild: bool = False) -> dict:
        params = {"rebuild": "1"} if rebuild else {}
        return self._request("GET", f"/balances/{user_id}", params=params)

    def balances(self, after_user_id: int = 0, *, limit: int = 200) -> dict:
        """投影列舉（游標式，user_id 遞增）。"""
        return self._request(
            "GET", "/balances", params={"after_user_id": after_user_id, "limit": limit}
        )

    def restamp_levels(self, items: list[dict]) -> dict:
        """只回沖等級帶、不動帳（≤500/批）。"""
        if len(items) > 500:
            raise ValueError("max 500 restamp items per batch")
        return self._request("POST", "/balances/restamp", json={"items": items})
