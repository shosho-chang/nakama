"""HTML fetcher for `seo-audit-post`（ADR-009 Phase 1.5 Slice D.1）。

抓 page HTML + 解析 BeautifulSoup tree + 收 response metadata（status / content
type / response time），讓下游 deterministic checks 可以 share 同一份 soup。

**4xx / 404 不 raise**：直接回 `FetchResult(soup=None, fetch_check=AuditCheck(
status="fail", rule_id="FETCH"))`，讓 D.2 主流程能繼續產 audit report（標明
fetch 失敗即可）— 不能讓 audit pipeline 因為 page 404 整個炸掉。

**5xx retry**：簡短 self-rolled exp backoff（與 `pagespeed_client.py` 同風格），
最多 3 次。Connection error 也 retry。

**firecrawl fallback**：`fetch_html_via_firecrawl()` 走 firecrawl scrape API
（`formats=["rawHtml"]`），用於 caller IP 被 CF SBFM 擋（VPS datacenter IP →
shosho.tw 全 403）的場景。每次 +1 firecrawl credit。
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any

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


def fetch_html_via_firecrawl(
    url: str,
    *,
    timeout: float = _DEFAULT_TIMEOUT,
    firecrawl_app: Any | None = None,
) -> FetchResult:
    """走 firecrawl scrape API 抓 raw HTML（含完整 `<head>`）。

    給 VPS / datacenter IP 被 CF SBFM 擋的場景用（VPS IP 打 shosho.tw 全 403，
    無論 UA 如何 — 詳見 docs/plans/2026-04-27-seo-phase15-acceptance-results.md
    F5）。`audit()` 接 `--via-firecrawl` flag 走這條 fetcher 路徑。

    Args:
        url: 目標 URL。
        timeout: 用於 firecrawl scrape timeout（秒；轉成毫秒傳給 firecrawl）。
        firecrawl_app: 注入的 Firecrawl client（測試用）；None 時 lazy import +
            從 ``FIRECRAWL_API_KEY`` env 建。

    Returns:
        `FetchResult`，shape 與 `fetch_html` 相同。失敗時 `soup=None`、
        `fetch_check.status="fail"`，actual 寫明「via firecrawl」。
    """
    if firecrawl_app is None:
        try:
            from firecrawl import Firecrawl  # type: ignore

            firecrawl_app = Firecrawl(api_key=os.environ["FIRECRAWL_API_KEY"])
        except Exception as e:
            check = AuditCheck(
                rule_id="FETCH",
                name="page fetched OK",
                category="fetch",
                severity="critical",
                status="fail",
                actual=f"firecrawl client init failed: {e}",
                expected="HTTP 2xx/3xx",
                fix_suggestion="Set FIRECRAWL_API_KEY env or pass firecrawl_app=",
                details={},
            )
            return FetchResult(
                url=url,
                final_url=url,
                status_code=0,
                content_type="",
                response_time_ms=0,
                html="",
                soup=None,
                fetch_check=check,
            )

    t0 = time.monotonic()
    try:
        # rawHtml 比 html 多保留 <head> + structural elements；audit
        # deterministic checks 需要 head meta（M1-M5 / O1-O4）。
        doc = firecrawl_app.scrape(
            url,
            formats=["rawHtml"],
            only_main_content=False,
            timeout=int(timeout * 1000),
        )
    except Exception as e:
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        logger.warning("fetch_html_firecrawl_failed url=%s err=%s", url, e)
        check = AuditCheck(
            rule_id="FETCH",
            name="page fetched OK",
            category="fetch",
            severity="critical",
            status="fail",
            actual=f"firecrawl scrape error: {e}",
            expected="HTTP 2xx/3xx",
            fix_suggestion="Check firecrawl quota / target URL",
            details={"response_time_ms": elapsed_ms},
        )
        return FetchResult(
            url=url,
            final_url=url,
            status_code=0,
            content_type="",
            response_time_ms=elapsed_ms,
            html="",
            soup=None,
            fetch_check=check,
        )

    elapsed_ms = int((time.monotonic() - t0) * 1000)
    raw_html = (
        getattr(doc, "rawHtml", None)
        or getattr(doc, "raw_html", None)
        or (doc.get("rawHtml", "") if isinstance(doc, dict) else "")
        or (doc.get("raw_html", "") if isinstance(doc, dict) else "")
    )
    metadata = getattr(doc, "metadata", None) or (
        doc.get("metadata", {}) if isinstance(doc, dict) else {}
    )
    if isinstance(metadata, dict):
        status_code = metadata.get("statusCode") or 200
    else:
        status_code = getattr(metadata, "statusCode", 200) or 200

    if not raw_html or status_code >= 400:
        check = AuditCheck(
            rule_id="FETCH",
            name="page fetched OK",
            category="fetch",
            severity="critical",
            status="fail",
            actual=f"HTTP {status_code} via firecrawl, html_len={len(raw_html)}",
            expected="HTTP 2xx/3xx",
            fix_suggestion="Check URL / firecrawl access",
            details={"response_time_ms": elapsed_ms},
        )
        return FetchResult(
            url=url,
            final_url=url,
            status_code=status_code,
            content_type="text/html",
            response_time_ms=elapsed_ms,
            html=raw_html,
            soup=None,
            fetch_check=check,
        )

    soup = BeautifulSoup(raw_html, "html.parser")
    check = AuditCheck(
        rule_id="FETCH",
        name="page fetched OK",
        category="fetch",
        severity="critical",
        status="pass",
        actual=f"HTTP {status_code} in {elapsed_ms}ms (via firecrawl)",
        expected="HTTP 2xx/3xx",
        fix_suggestion="",
        details={
            "content_type": "text/html",
            "final_url": url,
            "response_time_ms": elapsed_ms,
            "fetcher": "firecrawl",
        },
    )
    logger.info(
        "fetch_html_firecrawl_ok url=%s status=%d elapsed=%dms html_len=%d",
        url,
        status_code,
        elapsed_ms,
        len(raw_html),
    )
    return FetchResult(
        url=url,
        final_url=url,
        status_code=status_code,
        content_type="text/html",
        response_time_ms=elapsed_ms,
        html=raw_html,
        soup=soup,
        fetch_check=check,
    )
