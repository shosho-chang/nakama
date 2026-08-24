"""Sanji 規則引擎的行為鎖定測試。

這些測試鎖的是**經濟契約**：分數表、等級門檻、冪等鍵格式。
改動任何一項都必須 bump RULE_VERSION——測試逼你正視這件事。
"""

from __future__ import annotations

from datetime import date

import pytest

from agents.sanji import rules

SANJI_UID = 999


def _ev(etype: str, uid: int = 42, oid: int = 7, meta: dict | None = None, **kw) -> dict:
    return {
        "id": 1001,
        "event_type": etype,
        "user_id": uid,
        "object_id": oid,
        "meta": meta or {},
        "created_at": "2026-08-23 10:00:00",
        "dedupe_key": kw.get("dedupe_key"),
    }


# ── 經濟契約 ────────────────────────────────────────────────────


def test_xp_table_locked():
    assert rules.XP_TABLE == {
        "presence_day": 10,
        "checkin_day": 10,
        "streak_7": 30,
        "full_attendance": 200,
        "like_received": 10,
        "bookmark_received": 100,
        "lesson_completed": 50,
        "course_completed": 300,
        "quiz_passed": 50,
    }


def test_berry_is_xp_over_ten_including_negatives():
    for xp in rules.XP_TABLE.values():
        assert rules.berry_of(xp) * 10 == xp
    assert rules.berry_of(-100) == -10  # 沖正的貝里同步為負


# 曲線 v1（2026-08-24 前）。門檻**只准調低**——調高會讓既有成員掉級，
# 那是不可逆的信任破壞。這張表是天花板，不是要回去的目標。
# （例外註記：2026-08-24 Lv.15 140k→150k 經修修裁決，當時無人超過 20 XP、
#   玩法未公告，仍低於本天花板。**公告日後不再有任何上調**。）
_V1_CEILING = {
    1: 0,
    2: 100,
    3: 300,
    4: 1_000,
    5: 2_500,
    6: 5_000,
    7: 9_000,
    8: 15_000,
    9: 24_000,
    10: 35_000,
    11: 50_000,
    12: 70_000,
    13: 100_000,
    14: 140_000,
    15: 200_000,
}


def test_level_curve_shape():
    table = dict(rules.LEVEL_THRESHOLDS)
    assert sorted(table) == list(range(1, 16)), "等級數固定 15 階"
    assert table[1] == 0
    values = [table[n] for n in range(1, 16)]
    assert values == sorted(set(values)), "門檻必須嚴格遞增"


def test_thresholds_never_rise():
    """校準只能往下。往上調 = 有人今天是 Lv.7 明天變 Lv.6。"""
    table = dict(rules.LEVEL_THRESHOLDS)
    for n, ceiling in _V1_CEILING.items():
        assert table[n] <= ceiling, f"Lv.{n} 門檻調高了（{table[n]} > {ceiling}）"


def test_every_level_has_a_title():
    for n, _ in rules.LEVEL_THRESHOLDS:
        label = rules.level_label(n)
        assert label and not label.startswith("Lv."), f"Lv.{n} 沒有稱號"
    assert len(set(rules.LEVEL_LABELS.values())) == 15, "稱號不可重複"


def test_first_like_levels_up():
    """上船期的硬需求：第一個讚就要看到升級。"""
    assert rules.level_for(rules.XP_TABLE["like_received"]) == 2


@pytest.mark.parametrize(
    ("xp", "level"),
    [(0, 1), (9, 1), (10, 2), (49, 2), (50, 3), (149_999, 14), (150_000, 15)],
)
def test_level_for_boundaries(xp: int, level: int):
    assert rules.level_for(xp) == level


@pytest.mark.parametrize(
    ("xp", "expected"),
    [(0, (1, 0, 10)), (10, (2, 10, 50)), (49, (2, 10, 50)), (150_000, (15, 150_000, 0))],
)
def test_level_band(xp: int, expected: tuple[int, int, int]):
    assert rules.level_band(xp) == expected


def test_season_label():
    assert rules.season_of(date(2026, 8, 23)) == "2026Q3"
    assert rules.season_of(date(2027, 1, 1)) == "2027Q1"


# ── 確定性事件 → 授予 ───────────────────────────────────────────


def test_presence_day_grant_and_idempotency_key():
    g = rules.grant_for_event(_ev("presence_day"), sanji_user_id=SANJI_UID)
    assert g is not None
    assert (g["xp"], g["berry"]) == (10, 1)
    assert g["idempotency_key"] == "presence:42:2026-08-23"
    assert g["rule_version"] == rules.RULE_VERSION


def test_sanji_itself_never_earns():
    g = rules.grant_for_event(_ev("presence_day", uid=SANJI_UID), sanji_user_id=SANJI_UID)
    assert g is None


def test_like_requires_react_row_dedupe_and_skips_self_like():
    ok = rules.grant_for_event(
        _ev("reaction_added", meta={"type": "like", "actor_id": 7}, dedupe_key="react:555"),
        sanji_user_id=SANJI_UID,
    )
    assert ok is not None and ok["idempotency_key"] == "like:react:555"

    self_like = rules.grant_for_event(
        _ev("reaction_added", meta={"type": "like", "actor_id": 42}, dedupe_key="react:556"),
        sanji_user_id=SANJI_UID,
    )
    assert self_like is None

    no_dedupe = rules.grant_for_event(
        _ev("reaction_added", meta={"type": "like", "actor_id": 7}),
        sanji_user_id=SANJI_UID,
    )
    assert no_dedupe is None  # 無法保證冪等就不入帳


def test_checkin_and_quiz_never_auto_grant():
    assert rules.grant_for_event(_ev("checkin_submitted"), sanji_user_id=SANJI_UID) is None
    assert rules.grant_for_event(_ev("quiz_submitted"), sanji_user_id=SANJI_UID) is None


def test_bookmark_grant_from_scan_row():
    g = rules.grant_for_bookmark(
        {"id": 88, "user_id": 7}, feed_owner_id=42, sanji_user_id=SANJI_UID
    )
    assert g is not None
    assert (g["xp"], g["idempotency_key"]) == (100, "bookmark:react:88")
    # 自藏不計
    assert (
        rules.grant_for_bookmark(
            {"id": 89, "user_id": 42}, feed_owner_id=42, sanji_user_id=SANJI_UID
        )
        is None
    )


# ── 挑戰計分 ────────────────────────────────────────────────────


def test_streak_bonus_every_seventh_day_keyed_by_date():
    assert rules.streak_bonus_if_due(42, "2026-10-07", "2026Q4", 6) is None
    g7 = rules.streak_bonus_if_due(42, "2026-10-07", "2026Q4", 7)
    assert g7 is not None and g7["xp"] == 30
    assert g7["idempotency_key"] == "streak7:42:2026-10-07"
    # 斷後重建到 7：日期不同 → 新鍵，可再得（「斷了重新數」的裁決）
    g7b = rules.streak_bonus_if_due(42, "2026-11-20", "2026Q4", 7)
    assert g7b is not None and g7b["idempotency_key"] == "streak7:42:2026-11-20"
    # 14 天 = 第二次滿七
    assert rules.streak_bonus_if_due(42, "2026-10-14", "2026Q4", 14) is not None


def test_checkin_grant_one_per_day():
    a = rules.grant_for_checkin(42, 1234, "2026-10-07", "2026Q4")
    b = rules.grant_for_checkin(42, 9999, "2026-10-07", "2026Q4")
    assert a["idempotency_key"] == b["idempotency_key"] == "checkin:42:2026-10-07"
    assert a["season"] == "2026Q4"


def test_challenge_sources_locked():
    assert rules.CHALLENGE_SOURCES == {"checkin_day", "streak_7", "full_attendance"}


# ── 沖正 ────────────────────────────────────────────────────────


def test_reversal_negates_and_links():
    original = {"user_id": 42, "xp": 100, "berry": 10, "source": "bookmark_received", "season": ""}
    r = rules.reversal(original, reverses_grant_id=777, reason="bookmark removed")
    assert (r["xp"], r["berry"]) == (-100, -10)
    assert r["reverses_grant_id"] == 777
    assert r["idempotency_key"] == "reversal:777"
    assert r["source"] == "reversal"
