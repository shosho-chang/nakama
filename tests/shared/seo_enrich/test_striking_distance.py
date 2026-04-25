"""Tests for `shared.seo_enrich.striking_distance` — ADR-009 T6 contract.

Key invariant（T6）：range 外 row 用 `drop`，**不 raise**。
"""

from __future__ import annotations

import pytest

from shared.schemas.publishing import StrikingDistanceV1
from shared.seo_enrich.striking_distance import filter_striking_distance


def _row(keyword: str, url: str, position: float, impressions: int = 100) -> dict:
    """Build a GSC raw row with dimensions=["query", "page"]."""
    return {
        "keys": [keyword, url],
        "clicks": 1,
        "impressions": impressions,
        "ctr": 0.01,
        "position": position,
    }


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_happy_path_all_in_range_returns_three() -> None:
    rows = [
        _row("睡眠品質", "https://shosho.tw/sleep-quality", 10.5, impressions=120),
        _row("深層睡眠", "https://shosho.tw/deep-sleep", 15.2, impressions=340),
        _row("失眠", "https://shosho.tw/insomnia", 20.8, impressions=500),
    ]

    result = filter_striking_distance(rows)

    assert len(result) == 3
    assert all(isinstance(item, StrikingDistanceV1) for item in result)
    assert [item.keyword for item in result] == ["睡眠品質", "深層睡眠", "失眠"]
    assert [item.impressions_last_28d for item in result] == [120, 340, 500]
    # Phase 2 升級：目前一律 empty list
    assert all(item.suggested_actions == [] for item in result)


# ---------------------------------------------------------------------------
# Drop path — T6 contract: filter silently, do NOT raise
# ---------------------------------------------------------------------------


def test_drop_path_only_one_in_range_does_not_raise() -> None:
    rows = [
        _row("too-high", "https://shosho.tw/a", 3.2),  # < 10.0 drop
        _row("striking", "https://shosho.tw/b", 14.0),  # keep
        _row("too-deep", "https://shosho.tw/c", 55.7),  # > 21.0 drop
    ]

    result = filter_striking_distance(rows)

    assert len(result) == 1
    assert result[0].keyword == "striking"
    assert result[0].current_position == 14.0


def test_drop_path_all_out_of_range_returns_empty() -> None:
    rows = [
        _row("k1", "https://shosho.tw/a", 2.0),
        _row("k2", "https://shosho.tw/b", 99.0),
    ]
    assert filter_striking_distance(rows) == []


# ---------------------------------------------------------------------------
# Boundary parametrize — 10.0/10.5/20.9/21.0 keep；9.99/21.01 drop
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("position", "should_keep"),
    [
        (9.99, False),
        (10.0, True),
        (10.5, True),
        (20.9, True),
        (21.0, True),
        (21.01, False),
    ],
)
def test_boundary_inclusive_range(position: float, should_keep: bool) -> None:
    rows = [_row("boundary", "https://shosho.tw/b", position)]
    result = filter_striking_distance(rows)
    assert len(result) == (1 if should_keep else 0)
    if should_keep:
        assert result[0].current_position == position


# ---------------------------------------------------------------------------
# Empty input
# ---------------------------------------------------------------------------


def test_empty_input_returns_empty_list() -> None:
    assert filter_striking_distance([]) == []


# ---------------------------------------------------------------------------
# T6 shape contract — missing keys[1] must surface (caller bug)
# ---------------------------------------------------------------------------


def test_missing_page_dimension_raises_index_error() -> None:
    """dimensions=["query"] only → `keys[1]` 不存在 = caller 違反 T6 契約。"""
    bad_row = {
        "keys": ["only-keyword"],  # 缺 URL
        "clicks": 1,
        "impressions": 100,
        "ctr": 0.01,
        "position": 15.0,
    }
    with pytest.raises(IndexError):
        filter_striking_distance([bad_row])


def test_missing_position_field_raises_key_error() -> None:
    """Schema 本身 position 是必要欄位 — missing 必須 surface。"""
    bad_row = {
        "keys": ["k", "https://shosho.tw/x"],
        "clicks": 1,
        "impressions": 100,
        "ctr": 0.01,
        # 缺 position
    }
    with pytest.raises(KeyError):
        filter_striking_distance([bad_row])


# ---------------------------------------------------------------------------
# Schema round-trip — JSON serialize ↔ validate 還原相等
# ---------------------------------------------------------------------------


def test_output_json_round_trip_equal() -> None:
    rows = [
        _row("round-trip", "https://shosho.tw/rt", 12.75, impressions=888),
    ]
    [original] = filter_striking_distance(rows)

    dumped = original.model_dump_json()
    restored = StrikingDistanceV1.model_validate_json(dumped)

    assert restored == original
    # sanity：frozen=True 的前提下 equality 由所有欄位決定
    assert restored.keyword == "round-trip"
    assert restored.url == "https://shosho.tw/rt"
    assert restored.current_position == 12.75
    assert restored.impressions_last_28d == 888
    assert restored.suggested_actions == []


# ---------------------------------------------------------------------------
# Observability — logger records kept/dropped totals
# ---------------------------------------------------------------------------


def test_logger_info_records_kept_and_dropped(
    caplog: pytest.LogCaptureFixture,
) -> None:
    rows = [
        _row("keep", "https://shosho.tw/a", 15.0),
        _row("drop-low", "https://shosho.tw/b", 3.0),
        _row("drop-high", "https://shosho.tw/c", 50.0),
    ]
    with caplog.at_level("INFO", logger="nakama.seo_enrich.striking_distance"):
        filter_striking_distance(rows)

    assert any(
        "kept=1" in rec.message and "dropped=2" in rec.message and "total=3" in rec.message
        for rec in caplog.records
    )
