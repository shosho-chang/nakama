"""字卡的字數上限：讀得完所需的秒數不能撞破它自己的停留上限。

20260721 punch-L03 的 hero01 寫了 23 個字，`readable_floor_sec` 撐到 8.85 秒 >
8.0 秒上限，整條 run 在 preflight 就死。DP 怎麼挑 cue 都救不回來——秒數的下限由
display 長度決定，長度是 Director 寫的，而 Director 的 format_policy 從來沒被
告知這個上限。
"""

from __future__ import annotations

from agents.brook.script_video.finished_cut_production._derived_assets import (
    MAX_TITLE_OR_IDENTITY_SHOW_SEC,
    max_readable_display_chars,
    readable_floor_sec,
)


def test_a_display_at_the_limit_still_fits_under_the_ceiling():
    limit = max_readable_display_chars()
    assert readable_floor_sec("hero_title", "字" * limit) <= MAX_TITLE_OR_IDENTITY_SHOW_SEC


def test_one_more_character_breaks_the_ceiling():
    """上限必須是**緊的**——否則它擋不住真正會失敗的那一張卡。"""
    limit = max_readable_display_chars()
    assert readable_floor_sec("hero_title", "字" * (limit + 1)) > MAX_TITLE_OR_IDENTITY_SHOW_SEC


def test_the_real_punch_l03_hero_title_is_over_the_limit():
    """這就是那一張卡；換算出來 8.85 秒，比 8.0 秒多 0.85 秒。"""
    display = "螢幕時間不是一個絕對的事情，關鍵是你拿它來幹嘛"
    assert len(display) > max_readable_display_chars()
    assert readable_floor_sec("hero_title", display) > MAX_TITLE_OR_IDENTITY_SHOW_SEC


def test_the_limit_is_published_to_the_stage_policy():
    """Director 讀得到才算數——只寫在建置端等於沒說。"""
    from agents.brook.script_video.finished_cut_production._worker_packet import (
        expected_format_policy,
    )

    policy = expected_format_policy("long", "director")
    assert policy["editorial_brief"]["title_copy"]["max_display_chars"] == (
        max_readable_display_chars()
    )
