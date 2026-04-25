"""Tests for `shared.seo_enrich.cannibalization`.

覆蓋 P9 六要素 §5 驗收清單：severity 分級、threshold override、YAML round-trip、
缺檔 fallback、empty / single URL skip 等。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from shared.seo_enrich.cannibalization import (
    _CONFIG_PATH,
    _DEFAULT_THRESHOLDS,
    detect_cannibalization,
    load_cannibalization_thresholds,
)


def _row(keyword: str, url: str, impressions: int, **extra: Any) -> dict[str, Any]:
    """Build a GSC-shape row（dimensions=["query", "page"]）。"""
    return {
        "keys": [keyword, url],
        "clicks": extra.get("clicks", 0),
        "impressions": impressions,
        "ctr": extra.get("ctr", 0.0),
        "position": extra.get("position", 15.0),
    }


# ---------------------------------------------------------------------------
# Severity 分級 happy paths
# ---------------------------------------------------------------------------


def test_two_urls_70_30_is_low() -> None:
    """單 keyword 兩 URL 70:30 → severity=low（主從分明）。"""
    rows = [
        _row("咖啡 睡眠", "https://a.example.com/main", 700),
        _row("咖啡 睡眠", "https://a.example.com/side", 300),
    ]
    warnings = detect_cannibalization(rows)
    assert len(warnings) == 1
    w = warnings[0]
    assert w.keyword == "咖啡 睡眠"
    assert w.severity == "low"
    assert len(w.competing_urls) == 2
    # impressions 高的排前面
    assert w.competing_urls[0] == "https://a.example.com/main"
    assert w.recommendation  # 非空


def test_two_urls_50_50_is_high() -> None:
    """單 keyword 兩 URL 50:50 → severity=high（最糟分票，落在 40-60 區間）。"""
    rows = [
        _row("慢跑 減脂", "https://a.example.com/one", 500),
        _row("慢跑 減脂", "https://a.example.com/two", 500),
    ]
    warnings = detect_cannibalization(rows)
    assert len(warnings) == 1
    assert warnings[0].severity == "high"


def test_two_urls_55_45_is_high() -> None:
    """邊界：55:45 仍在 [40, 60] 區間 → high。"""
    rows = [
        _row("kw", "https://a.example.com/one", 550),
        _row("kw", "https://a.example.com/two", 450),
    ]
    warnings = detect_cannibalization(rows)
    assert warnings[0].severity == "high"


def test_two_urls_65_35_is_medium() -> None:
    """65:35 既不是 dominant（≥70），也不在 40-60 balanced 區間 → medium。"""
    rows = [
        _row("kw", "https://a.example.com/one", 650),
        _row("kw", "https://a.example.com/two", 350),
    ]
    warnings = detect_cannibalization(rows)
    assert warnings[0].severity == "medium"


def test_three_urls_40_30_30_is_high() -> None:
    """3 URL 各 ≥ 20% share → severity=high。"""
    rows = [
        _row("kw", "https://a.example.com/a", 400),
        _row("kw", "https://a.example.com/b", 300),
        _row("kw", "https://a.example.com/c", 300),
    ]
    warnings = detect_cannibalization(rows)
    assert len(warnings) == 1
    w = warnings[0]
    assert w.severity == "high"
    assert len(w.competing_urls) == 3


def test_three_urls_dominant_top_is_low() -> None:
    """3 URL 但 top 佔 80% → low（主導明確，次要頁是長尾）。"""
    rows = [
        _row("kw", "https://a.example.com/main", 800),
        _row("kw", "https://a.example.com/b", 100),
        _row("kw", "https://a.example.com/c", 100),
    ]
    warnings = detect_cannibalization(rows)
    assert warnings[0].severity == "low"


def test_three_urls_medium_mixed() -> None:
    """3 URL 但只 2 個 significant（35:45:20） → medium。

    share: 45%、35%、20%。significant_count = 3（都 ≥20%），所以 high。
    為確保是 medium 分支，讓第三個 <20%。
    """
    rows = [
        _row("kw", "https://a.example.com/a", 450),
        _row("kw", "https://a.example.com/b", 400),
        _row("kw", "https://a.example.com/c", 150),  # 15% < 20%
    ]
    warnings = detect_cannibalization(rows)
    # top 45% 既不 dominant (<70) 也非 3 significant → medium
    assert warnings[0].severity == "medium"


# ---------------------------------------------------------------------------
# Skip cases
# ---------------------------------------------------------------------------


def test_single_url_no_warning() -> None:
    """單 keyword 只 1 URL → 不產 warning。"""
    rows = [_row("kw", "https://a.example.com/only", 500)]
    assert detect_cannibalization(rows) == []


def test_low_impression_urls_filtered_out() -> None:
    """URL impressions < min_impressions_per_url 被濾掉後剩 1 URL → 不產 warning。"""
    rows = [
        _row("kw", "https://a.example.com/main", 500),
        _row("kw", "https://a.example.com/tail", 3),  # < 10 預設門檻
    ]
    warnings = detect_cannibalization(rows)
    assert warnings == []


def test_empty_input() -> None:
    """Empty rows → []。"""
    assert detect_cannibalization([]) == []


def test_malformed_row_skipped() -> None:
    """Row 缺 keys 或 keys 長度不足 → skip，不 raise。"""
    rows = [
        {"impressions": 100},  # no keys
        {"keys": ["only-one"], "impressions": 100},  # keys 太短
        _row("kw", "https://a.example.com/one", 500),
        _row("kw", "https://a.example.com/two", 500),
    ]
    warnings = detect_cannibalization(rows)
    assert len(warnings) == 1
    assert warnings[0].severity == "high"  # 50:50


# ---------------------------------------------------------------------------
# Multi-keyword sort
# ---------------------------------------------------------------------------


def test_multi_keyword_sorted_by_severity_desc() -> None:
    """多 keyword 混合不同 severity → 輸出按 severity 降序（high → medium → low）。"""
    rows = [
        # kw-low: 70:30
        _row("kw-low", "https://a.example.com/l1", 700),
        _row("kw-low", "https://a.example.com/l2", 300),
        # kw-high: 50:50
        _row("kw-high", "https://a.example.com/h1", 500),
        _row("kw-high", "https://a.example.com/h2", 500),
        # kw-medium: 65:35
        _row("kw-medium", "https://a.example.com/m1", 650),
        _row("kw-medium", "https://a.example.com/m2", 350),
    ]
    warnings = detect_cannibalization(rows)
    assert len(warnings) == 3
    assert [w.severity for w in warnings] == ["high", "medium", "low"]
    assert [w.keyword for w in warnings] == ["kw-high", "kw-medium", "kw-low"]


def test_one_warning_per_keyword() -> None:
    """同 keyword 多筆 rows（同一 URL 多列）不該產生重複 warning。"""
    rows = [
        # 同 (kw, url1) 出兩次模擬 upstream dedupe 失效 — 應 aggregate
        _row("kw", "https://a.example.com/one", 300),
        _row("kw", "https://a.example.com/one", 200),
        _row("kw", "https://a.example.com/two", 500),
    ]
    warnings = detect_cannibalization(rows)
    assert len(warnings) == 1
    # aggregate 後 url1=500, url2=500 → 50:50 → high
    assert warnings[0].severity == "high"


# ---------------------------------------------------------------------------
# Threshold override
# ---------------------------------------------------------------------------


def test_threshold_override_changes_classification() -> None:
    """自訂 thresholds：把 dominant_share 降到 0.60 → 原本 medium 的 65:35 變 low。"""
    rows = [
        _row("kw", "https://a.example.com/one", 650),
        _row("kw", "https://a.example.com/two", 350),
    ]
    # 預設：65:35 → medium
    default_warnings = detect_cannibalization(rows)
    assert default_warnings[0].severity == "medium"

    # 覆寫：降 dominant_share 到 0.60 → 65% 足以 dominant → low
    custom = dict(_DEFAULT_THRESHOLDS)
    custom["dominant_share"] = 0.60
    override_warnings = detect_cannibalization(rows, thresholds=custom)
    assert override_warnings[0].severity == "low"


def test_threshold_override_min_impressions() -> None:
    """自訂 min_impressions_per_url：升高後原本算進的 URL 被 filter → skip。"""
    rows = [
        _row("kw", "https://a.example.com/main", 500),
        _row("kw", "https://a.example.com/side", 50),
    ]
    # 預設 min_impressions_per_url=10 → 兩 URL 都算 → 產 warning
    default_warnings = detect_cannibalization(rows)
    assert len(default_warnings) == 1

    # 把門檻拉到 100 → side 只剩 50 被濾掉 → 只剩 main → skip
    custom = dict(_DEFAULT_THRESHOLDS)
    custom["min_impressions_per_url"] = 100
    override_warnings = detect_cannibalization(rows, thresholds=custom)
    assert override_warnings == []


# ---------------------------------------------------------------------------
# YAML config loading
# ---------------------------------------------------------------------------


def test_load_thresholds_from_real_yaml() -> None:
    """Round-trip：讀 repo 內 config/seo-enrich.yaml → key 集合對齊 defaults。"""
    loaded = load_cannibalization_thresholds()
    assert set(loaded.keys()) == set(_DEFAULT_THRESHOLDS.keys())
    # 數值型態正確（yaml 解析不該回 str）
    for key, default_val in _DEFAULT_THRESHOLDS.items():
        assert type(loaded[key]) is type(default_val), (
            f"{key}: expected {type(default_val)}, got {type(loaded[key])}"
        )


def test_load_thresholds_yaml_matches_baked_defaults() -> None:
    """Repo 內 yaml 的值應該等同於 `_DEFAULT_THRESHOLDS`（CLAUDE.md 寫進檔頭）。

    任何一方改動 drift 都要同步更新另一方。
    """
    loaded = load_cannibalization_thresholds()
    for key, default_val in _DEFAULT_THRESHOLDS.items():
        assert loaded[key] == default_val, f"{key}: yaml={loaded[key]} vs default={default_val}"


def test_load_thresholds_missing_file_returns_defaults(tmp_path: Path) -> None:
    """傳入不存在 path → 回 baked-in defaults，不 raise。"""
    missing = tmp_path / "nope.yaml"
    assert not missing.exists()
    loaded = load_cannibalization_thresholds(missing)
    assert loaded == _DEFAULT_THRESHOLDS


def test_load_thresholds_partial_yaml_merges(tmp_path: Path) -> None:
    """YAML 只覆寫部分 key → 其他 key 用 default。"""
    partial = tmp_path / "partial.yaml"
    partial.write_text("dominant_share: 0.85\n", encoding="utf-8")
    loaded = load_cannibalization_thresholds(partial)
    assert loaded["dominant_share"] == 0.85
    # 其他 key 不受影響
    assert loaded["min_impressions_per_url"] == _DEFAULT_THRESHOLDS["min_impressions_per_url"]
    assert loaded["balanced_share_min"] == _DEFAULT_THRESHOLDS["balanced_share_min"]


def test_load_thresholds_malformed_yaml_falls_back(tmp_path: Path) -> None:
    """YAML parse error → warn + fallback defaults，不 raise。"""
    bad = tmp_path / "bad.yaml"
    bad.write_text("::: not valid yaml :::\n  - [unclosed", encoding="utf-8")
    loaded = load_cannibalization_thresholds(bad)
    assert loaded == _DEFAULT_THRESHOLDS


def test_load_thresholds_non_mapping_yaml_falls_back(tmp_path: Path) -> None:
    """YAML 頂層不是 dict（例：list） → fallback defaults。"""
    bad = tmp_path / "list.yaml"
    bad.write_text("- a\n- b\n", encoding="utf-8")
    loaded = load_cannibalization_thresholds(bad)
    assert loaded == _DEFAULT_THRESHOLDS


def test_config_path_points_to_repo_yaml() -> None:
    """`_CONFIG_PATH` 解到 repo 內的 config/seo-enrich.yaml（sanity check）。"""
    assert _CONFIG_PATH.name == "seo-enrich.yaml"
    assert _CONFIG_PATH.parent.name == "config"


# ---------------------------------------------------------------------------
# Output schema invariants
# ---------------------------------------------------------------------------


def test_output_urls_sorted_by_impressions_desc() -> None:
    """`competing_urls` 按 impressions 降序排，top 在第一位。"""
    rows = [
        _row("kw", "https://a.example.com/small", 200),
        _row("kw", "https://a.example.com/big", 800),
    ]
    warnings = detect_cannibalization(rows)
    assert warnings[0].competing_urls[0] == "https://a.example.com/big"
    assert warnings[0].competing_urls[1] == "https://a.example.com/small"


def test_recommendation_within_schema_limit() -> None:
    """Recommendation 在 schema `max_length=500` 內（pydantic 自己會檢查，這裡 defensive）。"""
    rows = [
        _row("kw", "https://example.com/very-long-url-that-might-push-limits", 400),
        _row("kw", "https://example.com/another-long-url-here", 300),
        _row("kw", "https://example.com/third-competing-page", 300),
    ]
    warnings = detect_cannibalization(rows)
    assert all(len(w.recommendation) <= 500 for w in warnings)


def test_recommendation_low_topic_cluster_long_tail() -> None:
    """Long-tail topic cluster（1 dominant + N tail，全部 share 都過 min_impressions
    但 < significant_urls_share）會走 'low' 分支；所有 loser URL 串接時長度若無上限
    會撞上 schema max_length=500（regression for ultrareview bug_003 — 早期版本把
    `losers_txt` 全 join，topic cluster shape 直接讓 pydantic ValidationError 中斷
    enrich pipeline）。"""
    base_url = "https://shosho.tw/2024/01/morning-coffee-and-sleep-quality-deep-dive"
    # top_share = 1000/1400 ≈ 0.71 ≥ dominant (0.70); each loser 50/1400 ≈ 0.036
    # < significant (0.20) → significant_count == 1 < 3 → severity == "low".
    rows = [_row("morning coffee sleep", base_url, 1000)]
    for i in range(8):
        rows.append(
            _row(
                "morning coffee sleep",
                f"https://shosho.tw/2023/{i:02d}/competing-page-with-similar-topic-{i}",
                50,
            )
        )
    warnings = detect_cannibalization(rows)
    assert len(warnings) == 1
    w = warnings[0]
    assert w.severity == "low"
    assert len(w.recommendation) <= 500
    # competing_urls 仍保留完整列表（資訊不丟）；recommendation 只截顯示文字。
    assert len(w.competing_urls) == 9
    # 截斷文案應提到還有更多頁
    assert "等" in w.recommendation


@pytest.mark.parametrize(
    ("share_top", "share_other", "expected"),
    [
        (0.90, 0.10, "low"),
        (0.75, 0.25, "low"),
        (0.70, 0.30, "low"),  # 邊界：=0.70 算 dominant
        (0.65, 0.35, "medium"),
        (0.60, 0.40, "high"),  # 邊界：top=0.60 在 [0.40, 0.60]
        (0.50, 0.50, "high"),
    ],
)
def test_two_url_severity_boundaries(share_top: float, share_other: float, expected: str) -> None:
    """2-URL 分級邊界 parametrize。"""
    rows = [
        _row("kw", "https://a.example.com/one", int(share_top * 1000)),
        _row("kw", "https://a.example.com/two", int(share_other * 1000)),
    ]
    warnings = detect_cannibalization(rows)
    assert warnings[0].severity == expected
