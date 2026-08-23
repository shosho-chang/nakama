"""判定漏斗（機械層＋降級行為）與 template registry 的行為鎖定測試。"""

from __future__ import annotations

import pytest

from agents.sanji import judge, templates

# ── 漏斗①：機械判定 ──────────────────────────────────────────────


def test_media_means_mechanical_approve():
    d = judge.mechanical_precheck({"message": "", "media": [{"url": "x.jpg"}]})
    assert d is not None and d.action == "approve"


def test_short_text_no_media_goes_to_queue():
    d = judge.mechanical_precheck({"message": "打卡", "media": []})
    assert d is not None and d.action == "queue"


def test_long_text_no_media_defers_to_haiku():
    text = "今天睡前做了十分鐘的身體掃描，肩膀放鬆很多，記錄一下感受。"
    assert judge.mechanical_precheck({"message": text, "media": []}) is None


# ── 判定系統故障 → provisional（fail-open 精神） ─────────────────


def test_judge_degrades_to_provisional_on_llm_failure(monkeypatch: pytest.MonkeyPatch):
    async def boom(feed, theme):  # noqa: ARG001
        raise RuntimeError("quota dead")

    monkeypatch.setattr(judge, "_haiku_check", boom)
    text = "今天睡前做了十分鐘的身體掃描，肩膀放鬆很多，記錄一下感受。"
    d = judge.judge_feed({"message": text, "media": []}, "睡眠")
    assert d.action == "provisional"
    assert "haiku:error" in d.note


# ── Template registry ───────────────────────────────────────────


def test_reply_is_deterministic_for_same_user_day():
    kw = dict(user_id=42, day="2026-10-07", xp=10, berry=1, streak=3)
    assert templates.render_checkin_reply(**kw) == templates.render_checkin_reply(**kw)


def test_reply_contains_ledger_line_and_streak():
    text = templates.render_checkin_reply(user_id=42, day="2026-10-07", xp=10, berry=1, streak=3)
    assert "+10 XP" in text and "1 貝里" in text and "3" in text


def test_streak_bonus_line_appended_on_seventh_day():
    text = templates.render_checkin_reply(
        user_id=42, day="2026-10-07", xp=10, berry=1, streak=7, bonus_xp=30, bonus_berry=3
    )
    assert "+30 XP" in text and "+3 貝里" in text


def test_welcome_back_only_for_returnees_with_history():
    returned = templates.render_checkin_reply(
        user_id=42, day="2026-10-07", xp=10, berry=1, streak=1, returned_after_gap=True
    )
    fresh = templates.render_checkin_reply(
        user_id=42, day="2026-10-07", xp=10, berry=1, streak=1, returned_after_gap=False
    )
    assert returned != fresh  # 回歸池與一般池是不同文案

    # streak > 1 時即使標了 returned 也不用回歸語氣（已連續中，說「歡迎回來」很怪）
    ongoing = templates.render_checkin_reply(
        user_id=42, day="2026-10-07", xp=10, berry=1, streak=5, returned_after_gap=True
    )
    assert "歡迎回來" not in ongoing and "回來就好" not in ongoing


def test_all_template_pools_parse_and_render():
    for name, slots in (
        ("checkin_approved.txt", {"xp": 10, "berry": 1, "streak": 2}),
        ("welcome_back.txt", {"xp": 10, "berry": 1, "streak": 1}),
        ("streak_bonus.txt", {"bonus_xp": 30, "bonus_berry": 3, "streak": 7}),
    ):
        pool = templates._pool(name)
        assert pool, f"{name} empty"
        for variant in pool:
            rendered = variant.format(**slots)
            assert "{" not in rendered, f"unfilled slot in {name}: {variant!r}"
