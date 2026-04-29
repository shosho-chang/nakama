"""Tests for shared/schemas/seo.py — `target-keywords.yaml` + GSC row contracts.

Coverage:
- TargetKeywordV1 happy path + frozen + extra forbidden
- TargetKeywordV1 site Literal validation
- TargetKeywordListV1 round-trip via YAML
- GSCRowV1 happy path + site pattern validation
- GSCRowV1 device Literal validation
- GSCRowV1 ctr / position bounds
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
import yaml
from pydantic import ValidationError

from shared.schemas.seo import GSCRowV1, TargetKeywordListV1, TargetKeywordV1


def _kw(**overrides):
    base = {
        "keyword": "肌酸 功效",
        "site": "shosho.tw",
        "added_by": "zoro",
        "added_at": datetime.now(timezone.utc),
    }
    base.update(overrides)
    return TargetKeywordV1(**base)


def _gsc(**overrides):
    base = {
        "site": "sc-domain:shosho.tw",
        "date": date(2026, 4, 25),
        "query": "肌酸 功效",
        "page": "https://shosho.tw/blog/creatine",
        "country": "twn",
        "device": "desktop",
        "clicks": 5,
        "impressions": 120,
        "ctr": 0.041,
        "position": 11.4,
    }
    base.update(overrides)
    return GSCRowV1(**base)


# ---------------------------------------------------------------------------
# TargetKeywordV1
# ---------------------------------------------------------------------------


def test_target_keyword_v1_happy():
    kw = _kw(goal_rank=5, keyword_en="creatine effects")
    assert kw.keyword == "肌酸 功效"
    assert kw.site == "shosho.tw"
    assert kw.added_by == "zoro"
    assert kw.goal_rank == 5
    assert kw.keyword_en == "creatine effects"


def test_target_keyword_v1_frozen():
    kw = _kw()
    with pytest.raises((ValidationError, TypeError)):
        kw.keyword = "different"  # type: ignore[misc]


def test_target_keyword_v1_extra_forbidden():
    with pytest.raises(ValidationError):
        TargetKeywordV1(
            keyword="x",
            site="shosho.tw",
            added_by="zoro",
            added_at=datetime.now(timezone.utc),
            unknown_field="should_fail",
        )


def test_target_keyword_v1_unknown_site_rejected():
    with pytest.raises(ValidationError):
        _kw(site="example.com")


def test_target_keyword_v1_unknown_added_by_rejected():
    with pytest.raises(ValidationError):
        _kw(added_by="hacker")


def test_target_keyword_v1_naive_datetime_rejected():
    with pytest.raises(ValidationError):
        _kw(added_at=datetime(2026, 4, 25, 10, 0))  # no tz


def test_target_keyword_v1_goal_rank_must_be_positive():
    with pytest.raises(ValidationError):
        _kw(goal_rank=0)
    with pytest.raises(ValidationError):
        _kw(goal_rank=-1)


# ---------------------------------------------------------------------------
# TargetKeywordListV1
# ---------------------------------------------------------------------------


def test_target_keyword_list_v1_round_trip(tmp_path):
    """Write the model out as YAML, read it back, round-trip equal."""
    lst = TargetKeywordListV1(
        updated_at=datetime(2026, 4, 29, 0, 0, 0, tzinfo=timezone.utc),
        keywords=[
            _kw(keyword="肌酸 功效", goal_rank=5),
            _kw(keyword="褪黑激素 副作用", site="fleet.shosho.tw", added_by="usopp"),
        ],
    )
    p = tmp_path / "target-keywords.yaml"
    p.write_text(yaml.safe_dump(lst.model_dump(mode="json"), allow_unicode=True), encoding="utf-8")

    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    parsed = TargetKeywordListV1.model_validate(raw)
    assert len(parsed.keywords) == 2
    assert parsed.keywords[0].keyword == "肌酸 功效"
    assert parsed.keywords[1].site == "fleet.shosho.tw"


def test_target_keyword_list_v1_empty():
    lst = TargetKeywordListV1(updated_at=datetime.now(timezone.utc))
    assert lst.keywords == []


# ---------------------------------------------------------------------------
# GSCRowV1
# ---------------------------------------------------------------------------


def test_gsc_row_v1_happy():
    row = _gsc()
    assert row.site == "sc-domain:shosho.tw"
    assert row.date == date(2026, 4, 25)
    assert row.device == "desktop"


def test_gsc_row_v1_site_pattern_enforced():
    """Only `sc-domain:<host>` format accepted."""
    with pytest.raises(ValidationError):
        _gsc(site="https://shosho.tw/")
    with pytest.raises(ValidationError):
        _gsc(site="shosho.tw")


def test_gsc_row_v1_unknown_device_rejected():
    with pytest.raises(ValidationError):
        _gsc(device="smart_fridge")


def test_gsc_row_v1_position_lower_bound():
    """Schema requires position ≥ 1.0."""
    with pytest.raises(ValidationError):
        _gsc(position=0.5)


def test_gsc_row_v1_ctr_bounds():
    with pytest.raises(ValidationError):
        _gsc(ctr=1.5)
    with pytest.raises(ValidationError):
        _gsc(ctr=-0.1)


def test_gsc_row_v1_negative_clicks_rejected():
    with pytest.raises(ValidationError):
        _gsc(clicks=-1)


def test_gsc_row_v1_extra_forbidden():
    with pytest.raises(ValidationError):
        GSCRowV1(
            site="sc-domain:shosho.tw",
            date=date(2026, 4, 25),
            query="x",
            page="https://shosho.tw/x",
            country="twn",
            device="desktop",
            clicks=0,
            impressions=0,
            ctr=0.0,
            position=1.0,
            unknown="extra",
        )


def test_gsc_row_v1_frozen():
    row = _gsc()
    with pytest.raises((ValidationError, TypeError)):
        row.clicks = 999  # type: ignore[misc]
