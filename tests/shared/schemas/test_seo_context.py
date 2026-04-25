"""SEOContextV1 family schema tests — ADR-009 §D3 不變式守護。

覆蓋：
- extra="forbid" 拒絕未定義欄位
- frozen=True 禁 mutation
- schema_version Literal[1] 嚴格
- confloat / conint range 邊界
- StrikingDistanceV1 position range 10.0-21.0（T6 契約邊界）
- model_dump_json round-trip（Slice B skill 輸出用）
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from shared.schemas.publishing import (
    CannibalizationWarningV1,
    KeywordMetricV1,
    SEOContextV1,
    StrikingDistanceV1,
)

# ---------------------------------------------------------------------------
# KeywordMetricV1
# ---------------------------------------------------------------------------


def test_keyword_metric_happy() -> None:
    m = KeywordMetricV1(
        keyword="晨間咖啡 睡眠",
        clicks=12,
        impressions=890,
        ctr=0.013,
        avg_position=14.3,
    )
    assert m.source == "gsc"  # default
    assert m.schema_version == 1


def test_keyword_metric_extra_forbidden() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        KeywordMetricV1(
            keyword="x",
            clicks=0,
            impressions=0,
            ctr=0.0,
            avg_position=1.0,
            unknown_field="oops",
        )


def test_keyword_metric_frozen() -> None:
    m = KeywordMetricV1(keyword="x", clicks=0, impressions=0, ctr=0.0, avg_position=1.0)
    with pytest.raises(ValidationError):
        m.clicks = 99  # type: ignore[misc]


def test_keyword_metric_ctr_range() -> None:
    with pytest.raises(ValidationError):
        KeywordMetricV1(keyword="x", clicks=0, impressions=0, ctr=1.5, avg_position=1.0)
    with pytest.raises(ValidationError):
        KeywordMetricV1(keyword="x", clicks=0, impressions=0, ctr=-0.1, avg_position=1.0)


def test_keyword_metric_position_range() -> None:
    with pytest.raises(ValidationError):
        KeywordMetricV1(keyword="x", clicks=0, impressions=0, ctr=0.0, avg_position=0.5)
    with pytest.raises(ValidationError):
        KeywordMetricV1(keyword="x", clicks=0, impressions=0, ctr=0.0, avg_position=201.0)


def test_keyword_metric_source_literal() -> None:
    with pytest.raises(ValidationError):
        KeywordMetricV1(
            keyword="x",
            clicks=0,
            impressions=0,
            ctr=0.0,
            avg_position=1.0,
            source="ahrefs",
        )


def test_keyword_metric_schema_version_strict() -> None:
    with pytest.raises(ValidationError):
        KeywordMetricV1(
            schema_version=2,
            keyword="x",
            clicks=0,
            impressions=0,
            ctr=0.0,
            avg_position=1.0,
        )


def test_keyword_metric_clicks_non_negative() -> None:
    with pytest.raises(ValidationError):
        KeywordMetricV1(keyword="x", clicks=-1, impressions=0, ctr=0.0, avg_position=1.0)


# ---------------------------------------------------------------------------
# StrikingDistanceV1 — T6 契約邊界
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("pos", [10.0, 10.5, 15.0, 20.9, 21.0])
def test_striking_distance_in_range(pos: float) -> None:
    sd = StrikingDistanceV1(
        keyword="x",
        url="https://shosho.tw/x/",
        current_position=pos,
        impressions_last_28d=100,
    )
    assert sd.current_position == pos


@pytest.mark.parametrize("pos", [9.99, 21.01, 0.5, 50.0])
def test_striking_distance_out_of_range_rejected(pos: float) -> None:
    """T6 契約：skill 層應先 filter；raw row 給 schema 應 fail 非 silent pass。"""
    with pytest.raises(ValidationError):
        StrikingDistanceV1(
            keyword="x",
            url="https://shosho.tw/x/",
            current_position=pos,
            impressions_last_28d=100,
        )


def test_striking_distance_suggested_actions_default_empty() -> None:
    sd = StrikingDistanceV1(
        keyword="x",
        url="https://shosho.tw/x/",
        current_position=15.0,
        impressions_last_28d=100,
    )
    assert sd.suggested_actions == []


def test_striking_distance_extra_forbidden() -> None:
    with pytest.raises(ValidationError):
        StrikingDistanceV1(
            keyword="x",
            url="https://shosho.tw/x/",
            current_position=15.0,
            impressions_last_28d=100,
            new_field="x",
        )


# ---------------------------------------------------------------------------
# CannibalizationWarningV1
# ---------------------------------------------------------------------------


def test_cannibalization_min_two_urls() -> None:
    with pytest.raises(ValidationError):
        CannibalizationWarningV1(
            keyword="x",
            competing_urls=["https://shosho.tw/only/"],
            severity="low",
            recommendation="merge",
        )


def test_cannibalization_severity_literal() -> None:
    with pytest.raises(ValidationError):
        CannibalizationWarningV1(
            keyword="x",
            competing_urls=["https://a/", "https://b/"],
            severity="critical",
            recommendation="merge",
        )


def test_cannibalization_happy() -> None:
    w = CannibalizationWarningV1(
        keyword="x",
        competing_urls=["https://a/", "https://b/"],
        severity="medium",
        recommendation="合併 A 進 B",
    )
    assert w.schema_version == 1


# ---------------------------------------------------------------------------
# SEOContextV1
# ---------------------------------------------------------------------------


def _sample_ctx(**overrides) -> SEOContextV1:
    base = dict(
        target_site="wp_shosho",
        generated_at=datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc),
    )
    base.update(overrides)
    return SEOContextV1(**base)


def test_seo_context_happy_minimal() -> None:
    ctx = _sample_ctx()
    assert ctx.target_site == "wp_shosho"
    assert ctx.primary_keyword is None
    assert ctx.related_keywords == []
    assert ctx.striking_distance == []
    assert ctx.cannibalization_warnings == []
    assert ctx.competitor_serp_summary is None


def test_seo_context_target_site_literal() -> None:
    with pytest.raises(ValidationError):
        _sample_ctx(target_site="wp_unknown")


def test_seo_context_generated_at_must_be_aware() -> None:
    with pytest.raises(ValidationError):
        SEOContextV1(
            target_site="wp_shosho",
            generated_at=datetime(2026, 4, 25, 12, 0),  # naive
        )


def test_seo_context_round_trip_json() -> None:
    """Slice B skill 輸出會走 model_dump_json，Brook compose 走 model_validate_json。"""
    ctx = _sample_ctx(
        primary_keyword=KeywordMetricV1(
            keyword="晨間咖啡",
            clicks=12,
            impressions=890,
            ctr=0.013,
            avg_position=14.3,
        ),
        striking_distance=[
            StrikingDistanceV1(
                keyword="晨間咖啡 睡眠",
                url="https://shosho.tw/coffee-sleep/",
                current_position=15.0,
                impressions_last_28d=200,
                suggested_actions=["加段 H2 討論咖啡因半衰期"],
            )
        ],
        cannibalization_warnings=[
            CannibalizationWarningV1(
                keyword="睡眠",
                competing_urls=["https://shosho.tw/a/", "https://shosho.tw/b/"],
                severity="medium",
                recommendation="合併 A 進 B",
            )
        ],
        competitor_serp_summary="三個 top 競品主打 X Y Z",
        source_keyword_research_path="KB/Research/keywords/morning-coffee.md",
    )
    json_blob = ctx.model_dump_json()
    recovered = SEOContextV1.model_validate_json(json_blob)
    assert recovered == ctx


def test_seo_context_extra_forbidden() -> None:
    with pytest.raises(ValidationError):
        _sample_ctx(unknown_field="x")


def test_seo_context_frozen() -> None:
    ctx = _sample_ctx()
    with pytest.raises(ValidationError):
        ctx.target_site = "wp_fleet"  # type: ignore[misc]
