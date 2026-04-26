"""Performance deterministic checks（P1-P3）— 從 PageSpeed Insights 抽 metric。

Slice D.1 §附錄 A 3 條 rule：
    P1 LCP < 2.5s  (audits['largest-contentful-paint'].numericValue / 1000)
    P2 INP < 200ms (CrUX loadingExperience.metrics.INTERACTION_TO_NEXT_PAINT_MS.percentile)
    P3 CLS < 0.1   (audits['cumulative-layout-shift'].numericValue)

INP 在 lighthouse lab 沒有等價（Total Blocking Time TBT 不是 1:1），所以走 CrUX
field data；CrUX 沒有資料時（流量太低 / 新頁面）→ status="skip"，不 fail。

Lab 數據 vs Field 數據：
- LCP / CLS：用 lighthouse lab numericValue（每次 audit 都有）
- INP：用 CrUX field（部分 URL 沒資料）
"""

from __future__ import annotations

from typing import Any

from shared.log import get_logger
from shared.seo_audit.types import AuditCheck

logger = get_logger("nakama.seo_audit.performance")

_LCP_MAX = 2.5  # 秒
_INP_MAX = 200  # ms
_CLS_MAX = 0.1


def check_performance(pagespeed_result: dict[str, Any]) -> list[AuditCheck]:
    """跑 P1-P3 共 3 條 performance check。

    Args:
        pagespeed_result: `shared.pagespeed_client.PageSpeedClient.run(...)` 回傳值。
    """
    if not isinstance(pagespeed_result, dict):
        audits: dict = {}
        loading_exp: dict = {}
    else:
        lh = pagespeed_result.get("lighthouseResult")
        audits = lh.get("audits") if isinstance(lh, dict) else None
        if not isinstance(audits, dict):
            audits = {}
        loading_exp_raw = pagespeed_result.get("loadingExperience")
        loading_exp = loading_exp_raw if isinstance(loading_exp_raw, dict) else {}
    return [
        _check_lcp(audits),
        _check_inp(loading_exp),
        _check_cls(audits),
    ]


def _audit_numeric(audits: dict, key: str) -> float | None:
    """從 lighthouse audits dict 抽 numericValue；缺值或非數字回 None。"""
    audit = audits.get(key)
    if not isinstance(audit, dict):
        return None
    val = audit.get("numericValue")
    if isinstance(val, (int, float)):
        return float(val)
    return None


def _check_lcp(audits: dict) -> AuditCheck:
    raw_ms = _audit_numeric(audits, "largest-contentful-paint")
    if raw_ms is None:
        return AuditCheck(
            rule_id="P1",
            name=f"LCP < {_LCP_MAX}s",
            category="performance",
            severity="critical",
            status="skip",
            actual="lighthouse audit 缺 LCP 數據",
            expected=f"< {_LCP_MAX}s",
            fix_suggestion="重跑 PageSpeed audit；確認 URL 可被 Google 爬",
        )
    seconds = raw_ms / 1000.0
    if seconds < _LCP_MAX:
        return AuditCheck(
            rule_id="P1",
            name=f"LCP < {_LCP_MAX}s",
            category="performance",
            severity="critical",
            status="pass",
            actual=f"{seconds:.2f}s",
            expected=f"< {_LCP_MAX}s",
            fix_suggestion="",
        )
    return AuditCheck(
        rule_id="P1",
        name=f"LCP < {_LCP_MAX}s",
        category="performance",
        severity="critical",
        status="fail",
        actual=f"{seconds:.2f}s",
        expected=f"< {_LCP_MAX}s",
        fix_suggestion=(
            "LCP 元素 preload；首屏圖片不 lazy；伺服器 TTFB；CDN cache；減 render-blocking CSS / JS"
        ),
    )


def _check_inp(loading_exp: dict) -> AuditCheck:
    metrics = loading_exp.get("metrics", {}) if isinstance(loading_exp, dict) else {}
    inp = metrics.get("INTERACTION_TO_NEXT_PAINT_MS", {}) if isinstance(metrics, dict) else {}
    percentile = inp.get("percentile") if isinstance(inp, dict) else None

    if not isinstance(percentile, (int, float)):
        return AuditCheck(
            rule_id="P2",
            name=f"INP < {_INP_MAX}ms",
            category="performance",
            severity="warning",
            status="skip",
            actual="CrUX 缺 INP field data（流量太低 / 新頁）",
            expected=f"< {_INP_MAX}ms",
            fix_suggestion="累積流量等 CrUX 收齊；或本地 lighthouse lab TBT 替代",
        )
    val = float(percentile)
    if val < _INP_MAX:
        return AuditCheck(
            rule_id="P2",
            name=f"INP < {_INP_MAX}ms",
            category="performance",
            severity="warning",
            status="pass",
            actual=f"{val:.0f}ms",
            expected=f"< {_INP_MAX}ms",
            fix_suggestion="",
        )
    return AuditCheck(
        rule_id="P2",
        name=f"INP < {_INP_MAX}ms",
        category="performance",
        severity="warning",
        status="fail",
        actual=f"{val:.0f}ms",
        expected=f"< {_INP_MAX}ms",
        fix_suggestion="減 JS 主執行緒阻塞；defer non-critical JS；分割長 task",
    )


def _check_cls(audits: dict) -> AuditCheck:
    raw = _audit_numeric(audits, "cumulative-layout-shift")
    if raw is None:
        return AuditCheck(
            rule_id="P3",
            name=f"CLS < {_CLS_MAX}",
            category="performance",
            severity="warning",
            status="skip",
            actual="lighthouse audit 缺 CLS 數據",
            expected=f"< {_CLS_MAX}",
            fix_suggestion="重跑 PageSpeed audit",
        )
    if raw < _CLS_MAX:
        return AuditCheck(
            rule_id="P3",
            name=f"CLS < {_CLS_MAX}",
            category="performance",
            severity="warning",
            status="pass",
            actual=f"{raw:.3f}",
            expected=f"< {_CLS_MAX}",
            fix_suggestion="",
        )
    return AuditCheck(
        rule_id="P3",
        name=f"CLS < {_CLS_MAX}",
        category="performance",
        severity="warning",
        status="fail",
        actual=f"{raw:.3f}",
        expected=f"< {_CLS_MAX}",
        fix_suggestion="圖片 / iframe 標 width/height；font-display: optional；避免動態插入內容",
    )
