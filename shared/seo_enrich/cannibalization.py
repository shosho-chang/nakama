"""Keyword cannibalization detection（ADR-009 Slice B 的一部份）。

Cannibalization = 同一個關鍵字在 GSC 內有多個自家 URL 競爭 impression / click。
這個模組吃 GSC raw rows（`dimensions=["query", "page"]`）、group by keyword、
依 impressions 分布決定 severity（low / medium / high）並產生可操作的繁中建議。

Public API：

    load_cannibalization_thresholds(path=None) -> dict
        讀 `config/seo-enrich.yaml`；缺檔或缺 key 回 baked-in defaults，不 raise。

    detect_cannibalization(rows, thresholds=None) -> list[CannibalizationWarningV1]
        吃 GSC raw rows、回 warnings（按 severity 降序）。

GSC raw row shape（assumed，ADR-009 Slice A 契約）：
    {"keys": [keyword, url], "clicks": int, "impressions": int,
     "ctr": float, "position": float}

Severity 邏輯（threshold 可調）：
    - 過濾 impressions < `min_impressions_per_url` 的 URL
    - 剩 < `min_urls_per_keyword` 個 URL → 不產 warning
    - 2 URL：
        * top share ≥ `dominant_share` → low
        * top share 落在 [balanced_share_min, balanced_share_max] → high（50:50 即 high）
        * 其餘 → medium
    - 3+ URL：
        * 至少 3 個 URL share ≥ `significant_urls_share` → high
        * 有 1 個 URL share ≥ dominant_share → low
        * 其餘 → medium

T9 設計：threshold 全部從 yaml 載入、預設值 baked-in；這是 ADR-009 T9 要求
（「150-200 LOC + threshold config，不是原估 50 行」）的落地。
"""

from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml

from shared.schemas.publishing import CannibalizationWarningV1

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "seo-enrich.yaml"

# Baked-in defaults — yaml 缺檔或缺 key 時用。值必須與 `config/seo-enrich.yaml` 對齊，
# tests 裡有 round-trip test 保證兩邊 drift 不過。
_DEFAULT_THRESHOLDS: dict[str, Any] = {
    "min_urls_per_keyword": 2,
    "min_impressions_per_url": 10,
    "dominant_share": 0.70,
    "balanced_share_min": 0.40,
    "balanced_share_max": 0.60,
    "significant_urls_share": 0.20,
}

# severity 降序排序用
_SEVERITY_ORDER: dict[str, int] = {"high": 0, "medium": 1, "low": 2}


def load_cannibalization_thresholds(path: Path | None = None) -> dict[str, Any]:
    """讀 yaml 設定檔並 merge 進 baked-in defaults。

    - `path=None` → 走 `config/seo-enrich.yaml`
    - 檔不存在 → 回 `_DEFAULT_THRESHOLDS` copy（warn log，不 raise）
    - yaml parse 失敗 → warn log + fallback defaults
    - yaml 裡缺 key → 那個 key 用 default
    - yaml 有多餘 key → 無視（保留 forward-compat 空間）
    """
    target = path or _CONFIG_PATH
    merged: dict[str, Any] = dict(_DEFAULT_THRESHOLDS)

    if not target.exists():
        logger.warning(
            "seo-enrich threshold config not found at %s; using baked-in defaults",
            target,
        )
        return merged

    try:
        raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        logger.warning("failed to parse %s: %s; using baked-in defaults", target, exc)
        return merged

    if not isinstance(raw, dict):
        logger.warning("%s is not a mapping; using baked-in defaults", target)
        return merged

    for key in _DEFAULT_THRESHOLDS:
        if key in raw:
            merged[key] = raw[key]
    return merged


def _aggregate_by_url(
    keyword_rows: list[dict[str, Any]],
) -> dict[str, int]:
    """把同一 keyword 的 rows group by URL、加總 impressions。

    同一 (keyword, url) 理論上 GSC 只回一列，但防守性 aggregate 以免
    上游 dedupe 失效時我們仍正確運作。
    """
    totals: dict[str, int] = defaultdict(int)
    for row in keyword_rows:
        keys = row.get("keys") or []
        if len(keys) < 2:
            continue
        url = keys[1]
        impressions = int(row.get("impressions", 0) or 0)
        totals[url] += impressions
    return dict(totals)


def _classify_severity(
    url_shares: list[float],
    thresholds: dict[str, Any],
) -> str:
    """依 share 分布決定 severity。`url_shares` 已按降序排。"""
    dominant = float(thresholds["dominant_share"])
    balanced_min = float(thresholds["balanced_share_min"])
    balanced_max = float(thresholds["balanced_share_max"])
    significant = float(thresholds["significant_urls_share"])

    top_share = url_shares[0]

    if len(url_shares) == 2:
        # 2 URL 分支：看 top share 落哪
        if top_share >= dominant:
            return "low"
        if balanced_min <= top_share <= balanced_max:
            # 40-60 區間含 50:50，是最糟的分票情況
            return "high"
        return "medium"

    # 3+ URL 分支
    significant_count = sum(1 for s in url_shares if s >= significant)
    if significant_count >= 3:
        return "high"
    if top_share >= dominant:
        return "low"
    return "medium"


def _build_recommendation(
    severity: str,
    competing_urls: list[str],
    top_url: str,
) -> str:
    """產出繁中 recommendation；簡短、actionable、不超 500 字（schema 限制）。"""
    if severity == "low":
        losers = [u for u in competing_urls if u != top_url]
        losers_txt = "、".join(losers) if losers else "次要頁"
        return (
            f"主頁 {top_url} 佔大宗流量；建議將 {losers_txt} 內連指回主頁，"
            "或在次要頁加 canonical 指向主頁，避免稀釋 ranking signal。"
        )
    if severity == "medium":
        return (
            f"流量在 {len(competing_urls)} 個 URL 間分散（主頁 {top_url}）；"
            "建議評估合併內容成單一權威頁，或明確差異化各頁 intent "
            "（例：一頁給 how-to、一頁給 review）。"
        )
    # high
    return (
        f"{len(competing_urls)} 個 URL 在同一 query 嚴重互打；"
        f"建議選定 {top_url} 為主頁、其餘做 301 redirect 或合併後 de-index，"
        "並檢查 internal link 是否應全部指向主頁。"
    )


def detect_cannibalization(
    rows: list[dict[str, Any]],
    thresholds: dict[str, Any] | None = None,
) -> list[CannibalizationWarningV1]:
    """偵測 GSC rows 中的 keyword cannibalization。

    Args:
        rows: GSC raw rows（dimensions=["query", "page"]）。
        thresholds: 覆寫用。`None` 時走 `load_cannibalization_thresholds()`。

    Returns:
        `CannibalizationWarningV1` list，按 severity 降序（high → low）；
        同 severity 內依 keyword 字母序穩定排序。每個 keyword 最多 1 warning。

    不會 raise validation error — 缺 key 的 row 直接 skip。
    """
    if not rows:
        return []

    cfg = dict(thresholds) if thresholds is not None else load_cannibalization_thresholds()
    min_urls = int(cfg["min_urls_per_keyword"])
    min_impr = int(cfg["min_impressions_per_url"])

    # group by keyword
    by_keyword: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        keys = row.get("keys") or []
        if len(keys) < 2:
            continue
        keyword = keys[0]
        if not isinstance(keyword, str) or not keyword:
            continue
        by_keyword[keyword].append(row)

    warnings: list[CannibalizationWarningV1] = []
    for keyword, kw_rows in by_keyword.items():
        url_impressions = _aggregate_by_url(kw_rows)
        # 過濾低 impression URL
        significant = {url: impr for url, impr in url_impressions.items() if impr >= min_impr}
        if len(significant) < min_urls:
            continue

        total = sum(significant.values())
        if total <= 0:
            continue

        # 降序：impressions 高的在前
        sorted_urls = sorted(significant.items(), key=lambda kv: kv[1], reverse=True)
        shares = [impr / total for _, impr in sorted_urls]
        competing_urls = [url for url, _ in sorted_urls]
        top_url = competing_urls[0]

        severity = _classify_severity(shares, cfg)
        recommendation = _build_recommendation(severity, competing_urls, top_url)

        warnings.append(
            CannibalizationWarningV1(
                keyword=keyword,
                competing_urls=competing_urls,
                severity=severity,  # type: ignore[arg-type]
                recommendation=recommendation,
            )
        )

    # severity 降序 → high 在前；同 severity 內 keyword 穩定排序
    warnings.sort(key=lambda w: (_SEVERITY_ORDER[w.severity], w.keyword))
    return warnings
