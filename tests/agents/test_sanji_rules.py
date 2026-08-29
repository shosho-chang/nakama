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
        "checkin_day": 20,
        "streak_7": 30,
        "full_attendance": 500,
        "like_received": 10,
        "comment_received": 30,
        "bookmark_received": 100,
        "lesson_completed": 50,
        "course_completed": 500,
        "quiz_passed": 50,
        "event_hosted": 500,
        "session_hosted": 300,
        "event_cohosted": 200,
    }


def test_berry_is_xp_over_ten_including_negatives():
    for xp in rules.XP_TABLE.values():
        assert rules.berry_of(xp) * 10 == xp
    assert rules.berry_of(-100) == -10  # 沖正的貝里同步為負


# 曲線 v1（2026-08-24 前）。門檻**只准調低**——調高會讓既有成員掉級，
# 那是不可逆的信任破壞。這張表是天花板，不是要回去的目標。
# （例外註記：2026-08-24 Lv.15 140k→150k 經修修裁決，當時無人超過 20 XP、
#   玩法未公告，仍低於本天花板。**公告日後不再有任何上調**。）
# （2026-08-25 v5：插入空島成 16 階——只新增 3,000 一格、無任何上調；
#   16 階的天花板承接舊 Lv.15 的 200k。）
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
    16: 200_000,
}


def test_level_curve_shape():
    table = dict(rules.LEVEL_THRESHOLDS)
    assert sorted(table) == list(range(1, 17)), "等級數固定 16 階"
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
    assert len(set(rules.LEVEL_LABELS.values())) == 16, "島名不可重複"


def test_tier_ladder():
    """位階線 v3（2026-08-26 定稿）：整條航路只換七次稱呼，頂點是海賊王。"""
    assert rules.TIER_OF_LEVEL == {
        5: "超新星",
        8: "最惡世代",
        11: "王下七武海",
        13: "霸王色",
        14: "傳說船長",
        15: "四皇",
        16: "海賊王",
    }
    assert rules.tier_for(1) == ""
    assert rules.tier_for(4) == ""
    assert rules.tier_for(5) == "超新星"
    assert rules.tier_for(10) == "最惡世代"
    assert rules.tier_for(12) == "王下七武海"
    assert rules.tier_for(14) == "傳說船長"
    assert rules.tier_for(16) == "海賊王"


def test_first_like_levels_up():
    """上船期的硬需求：第一個讚就要看到升級。"""
    assert rules.level_for(rules.XP_TABLE["like_received"]) == 2


@pytest.mark.parametrize(
    ("xp", "level"),
    [
        (0, 1),
        (9, 1),
        (10, 2),
        (49, 2),
        (50, 3),
        (2_999, 7),
        (3_000, 8),
        (149_999, 15),
        (150_000, 16),
    ],
)
def test_level_for_boundaries(xp: int, level: int):
    assert rules.level_for(xp) == level


@pytest.mark.parametrize(
    ("xp", "expected"),
    [(0, (1, 0, 10)), (10, (2, 10, 50)), (3_000, (8, 3_000, 4_000)), (150_000, (16, 150_000, 0))],
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


def test_comment_scores_unique_per_post_and_skips_self_and_sanji():
    # 正常：貼文 900 被 user 7 留言 → 作者(42) +30，鍵 = 一文一人
    ok = rules.grant_for_event(
        _ev("comment_received", oid=900, meta={"actor_id": 7, "comment_id": 501}),
        sanji_user_id=SANJI_UID,
    )
    assert ok is not None
    assert (ok["xp"], ok["berry"]) == (30, 3)
    assert ok["idempotency_key"] == "comment:900:7"  # 同人再留 → 同鍵 → DB 冪等擋掉

    # 自己留言不計
    self_c = rules.grant_for_event(
        _ev("comment_received", oid=900, meta={"actor_id": 42, "comment_id": 502}),
        sanji_user_id=SANJI_UID,
    )
    assert self_c is None

    # Sanji 的祝賀留言不計（否則每篇得分文自動 +30，經濟就假了）
    sanji_c = rules.grant_for_event(
        _ev("comment_received", oid=900, meta={"actor_id": SANJI_UID, "comment_id": 503}),
        sanji_user_id=SANJI_UID,
    )
    assert sanji_c is None

    # 缺 actor 或缺 feed 參照 → 無法保證「一文一人一次」→ 不入帳
    assert (
        rules.grant_for_event(
            _ev("comment_received", oid=900, meta={"comment_id": 504}),
            sanji_user_id=SANJI_UID,
        )
        is None
    )
    assert (
        rules.grant_for_event(
            _ev("comment_received", oid=0, meta={"actor_id": 7, "comment_id": 505}),
            sanji_user_id=SANJI_UID,
        )
        is None
    )

    # 舊觀測型別 comment_added（留言者為主體）永不入帳
    assert (
        rules.grant_for_event(
            _ev("comment_added", oid=501, meta={"feed_id": 900}),
            sanji_user_id=SANJI_UID,
        )
        is None
    )


def test_checkin_and_quiz_never_auto_grant():
    assert rules.grant_for_event(_ev("checkin_submitted"), sanji_user_id=SANJI_UID) is None
    assert rules.grant_for_event(_ev("quiz_submitted"), sanji_user_id=SANJI_UID) is None


def test_like_row_same_key_as_hook_path():
    """掃描路徑與 hook 路徑同鍵——歷史認列與即時捕捉永不重複入帳。"""
    g = rules.grant_for_like_row(
        {"id": 555, "user_id": 7, "object_id": 900}, feed_owner_id=42, sanji_user_id=SANJI_UID
    )
    assert g is not None
    assert g["idempotency_key"] == "like:react:555"  # 與 hook 的 like:{dedupe} 完全同格式
    assert (g["xp"], g["reason"]) == (10, "feed:900")
    # 自讚不計
    assert (
        rules.grant_for_like_row(
            {"id": 556, "user_id": 42, "object_id": 900}, feed_owner_id=42, sanji_user_id=SANJI_UID
        )
        is None
    )


def test_comment_row_grants_owner_and_skips_self_sanji_deleted():
    g = rules.grant_for_comment_row(
        {"id": 1065, "post_id": 288, "user_id": 2, "owner_id": 12}, sanji_user_id=SANJI_UID
    )
    assert g is not None
    assert g["user_id"] == 12  # 受益人 = 貼文作者
    assert g["idempotency_key"] == "comment:288:2"  # 與 hook 同鍵：一文一人跨歷史成立
    assert (g["xp"], g["reason"]) == (30, "feed:288")

    # 自留 / Sanji 留言 / 貼文已刪（owner 0）都不計
    assert (
        rules.grant_for_comment_row(
            {"id": 1, "post_id": 288, "user_id": 12, "owner_id": 12}, sanji_user_id=SANJI_UID
        )
        is None
    )
    assert (
        rules.grant_for_comment_row(
            {"id": 2, "post_id": 288, "user_id": SANJI_UID, "owner_id": 12}, sanji_user_id=SANJI_UID
        )
        is None
    )
    assert (
        rules.grant_for_comment_row(
            {"id": 3, "post_id": 288, "user_id": 2, "owner_id": 0}, sanji_user_id=SANJI_UID
        )
        is None
    )


def test_bookmark_grant_from_scan_row():
    g = rules.grant_for_bookmark(
        {"id": 88, "user_id": 7, "object_id": 900}, feed_owner_id=42, sanji_user_id=SANJI_UID
    )
    assert g is not None
    assert (g["xp"], g["idempotency_key"]) == (100, "bookmark:react:88")
    assert g["reason"] == "feed:900"  # 貼文參照——按內容彙整的歸戶依據

    # 缺 feed 參照仍入帳（錢不能因顯示需求而漏），reason 留空
    g2 = rules.grant_for_bookmark(
        {"id": 90, "user_id": 7}, feed_owner_id=42, sanji_user_id=SANJI_UID
    )
    assert g2 is not None and g2["reason"] == ""
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
