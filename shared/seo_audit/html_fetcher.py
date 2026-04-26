"""HTML fetcher for `seo-audit-post`（ADR-009 Phase 1.5 Slice D.1）。

抓 page HTML + 解析 BeautifulSoup tree + 收 response metadata（status / content
type / response time），讓下游 deterministic checks 可以 share 同一份 soup。

**4xx / 404 不 raise**：直接回 `FetchResult(soup=None, fetch_check=AuditCheck(
status="fail", rule_id="FETCH"))`，讓 D.2 主流程能繼續產 audit report（標明
fetch 失敗即可）— 不能讓 audit pipeline 因為 page 404 整個炸掉。

**5xx retry**：簡短 self-rolled exp backoff（與 `pagespeed_client.py` 同風格），
最多 3 次。Connection error 也 retry。
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx
from bs4 import BeautifulSoup

from shared.log import get_logger
from shared.seo_audit.types import AuditCheck

logger = get_logger("nakama.seo_audit.html_fetcher")

_DEFAULT_TIMEOUT = 20.0
_MAX_ATTEMPTS = 3
_BACKOFF_BASE = 2.0
_USER_AGENT = "Mozilla/5.0 (compatible; NakamaBot/1.0; +https://shosho.tw/about) seo-audit/1.0"


@dataclass
class FetchResult:
    """HTML fetch 結果包：soup 為 None 時表示 fetch 失敗，看 `fetch_check.status`。"""

    url: str
    final_url: str  # 跟 redirect 後的最終 URL
    status_code: int  # 0 表示連線層失敗
    content_type: str
    response_time_ms: int
    html: str
    soup: BeautifulSoup | None
    fetch_check: AuditCheck


def fetch_html(url: str, *, timeout: float = _DEFAULT_TIMEOUT) -> FetchResult:
    """抓取 `url` 並解析 BeautifulSoup。

    Args:
        url: 目標 URL（含 scheme）。
        timeout: HTTP timeout（秒），預設 20s。

    Returns:
        `FetchResult`：成功時 `soup` 為 BeautifulSoup 物件、`fetch_check.status="pass"`；
        4xx/5xx/連線錯誤時 `soup=None`、`fetch_check.status="fail"`，呼叫者直接把
        `fetch_check` 加進 `AuditResult.checks`、跳過後續 deterministic check。

    Raises:
        Never — 所有錯誤都封進 `FetchResult.fetch_check`。
    """
    headers = {"User-Agent": _USER_AGENT, "Accept": "text/html,application/xhtml+xml"}

    last_status = 0
    last_err: str = ""
    elapsed_ms = 0
    final_url = url
    content_type = ""
    body = ""

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        start = time.monotonic()
        try:
            response = httpx.get(
                url,
                headers=headers,
                timeout=timeout,
                follow_redirects=True,
            )
        except httpx.RequestError as e:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            last_err = f"{type(e).__name__}: {e}"
            last_status = 0
            logger.warning("fetch_html_neterr url=%s err=%s attempt=%d", url, last_err, attempt)
            if attempt < _MAX_ATTEMPTS:
                time.sleep(_BACKOFF_BASE ** (attempt - 1))
                continue
            break

        elapsed_ms = int((time.monotonic() - start) * 1000)
        last_status = response.status_code
        final_url = str(response.url)
        content_type = response.headers.get("content-type", "")
        body = response.text

        if 200 <= response.status_code < 400:
            soup = BeautifulSoup(body, "html.parser")
            check = AuditCheck(
                rule_id="FETCH",
                name="page fetched OK",
                category="fetch",
                severity="critical",
                status="pass",
                actual=f"HTTP {response.status_code} in {elapsed_ms}ms",
                expected="HTTP 2xx/3xx",
                fix_suggestion="",
                details={
                    "content_type": content_type,
                    "final_url": final_url,
                    "response_time_ms": elapsed_ms,
                },
            )
            logger.info(
                "fetch_html_ok url=%s status=%d elapsed=%dms",
                url,
                response.status_code,
                elapsed_ms,
            )
            return FetchResult(
                url=url,
                final_url=final_url,
                status_code=response.status_code,
                content_type=content_type,
                response_time_ms=elapsed_ms,
                html=body,
                soup=soup,
                fetch_check=check,
            )

        if 400 <= response.status_code < 500:
            last_err = f"HTTP {response.status_code}"
            logger.warning("fetch_html_4xx url=%s status=%d (no retry)", url, response.status_code)
            break

        # 5xx → retry
        last_err = f"HTTP {response.status_code}"
        logger.warning(
            "fetch_html_5xx url=%s status=%d attempt=%d", url, response.status_code, attempt
        )
        if attempt < _MAX_ATTEMPTS:
            time.sleep(_BACKOFF_BASE ** (attempt - 1))

    fail_check = AuditCheck(
        rule_id="FETCH",
        name="page fetched OK",
        category="fetch",
        severity="critical",
        status="fail",
        actual=last_err or "unknown error",
        expected="HTTP 2xx/3xx",
        fix_suggestion=(
            "確認 URL 可達；4xx 檢查路徑 / WP page 是否 publish；"
            "5xx 確認 server 健康；連線錯誤檢查 DNS / TLS。"
        ),
        details={
            "status_code": last_status,
            "response_time_ms": elapsed_ms,
            "content_type": content_type,
        },
    )
    return FetchResult(
        url=url,
        final_url=final_url,
        status_code=last_status,
        content_type=content_type,
        response_time_ms=elapsed_ms,
        html=body,
        soup=None,
        fetch_check=fail_check,
    )
