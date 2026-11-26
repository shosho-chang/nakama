"""第 11 類他人影片引用：候選清單規則、字幕定位、下載只下核准的那一段。不連網。"""

from __future__ import annotations

import pytest

from agents.brook.youtube_video import clips

META = {
    "video_id": "wTBhRVkOSJM",
    "title": "Rational Reminder 376",
    "channel": "The Rational Reminder Podcast",
    "duration": 3846,
}

VTT = """WEBVTT
Kind: captions
Language: en

00:02:44.120 --> 00:02:45.910 align:start position:0%
Killers

00:02:45.920 --> 00:02:49.280 align:start position:0%
Killers
that's<00:02:46.400><c> quite</c><00:02:46.800><c> a</c><00:02:47.000><c> brain</c> you've got Craig

00:02:49.280 --> 00:02:51.000 align:start position:0%
of course it is it's all I've got
"""


def test_raw_cues_keep_each_caption_line_with_its_own_time():
    cues = clips.raw_cues(VTT)
    assert [c["text"] for c in cues] == [
        "Killers",
        "that's quite a brain you've got Craig",
        "of course it is it's all I've got",
    ]
    assert cues[1]["start"] == pytest.approx(165.92)
    assert cues[1]["end"] == pytest.approx(169.28)


def test_locate_finds_aliases_case_insensitively():
    cues = clips.raw_cues(VTT)
    # 自動字幕把 Krang 聽成 Craig：DP 要帶別名找
    assert clips.locate(cues, ["krang"]) == []
    assert clips.locate(cues, ["Krang", "craig"]) == [1]


def test_window_extends_on_line_boundaries_to_target():
    cues = clips.raw_cues(VTT)
    assert clips.window(cues, 1, 3.0) == (165.92, 169.28)
    assert clips.window(cues, 1, 5.0) == (165.92, 171.0)


def test_add_and_update_candidate_rules():
    picks = clips.new_picks("r1")
    cand = clips.add_candidate(picks, META, 270.25, 273.8, "Nick 在回答")
    assert cand["id"] == "c1"
    assert cand["source_label"] == "The Rational Reminder Podcast"
    assert cand["status"] == "proposed"
    clips.update_candidate(picks, "c1", {"start": 271.004, "status": "approved"})
    assert picks["candidates"][0]["start"] == 271.0
    assert clips.approved(picks) == [picks["candidates"][0]]
    with pytest.raises(clips.ClipError, match="終點要在起點之後"):
        clips.update_candidate(picks, "c1", {"end": 271.2})
    with pytest.raises(clips.ClipError, match="超過影片長度"):
        clips.update_candidate(picks, "c1", {"start": 3840, "end": 3850})
    with pytest.raises(clips.ClipError, match="最長"):
        clips.update_candidate(picks, "c1", {"end": 400})
    with pytest.raises(clips.ClipError, match="不能改"):
        clips.update_candidate(picks, "c1", {"video_id": "xxxxxxxxxxx"})
    with pytest.raises(clips.ClipError, match="來源"):
        clips.update_candidate(picks, "c1", {"source_label": "  "})
    # 失敗的修改不會留下半套
    assert picks["candidates"][0]["end"] == 273.8


def test_picks_round_trip(tmp_path):
    path = clips.picks_path(tmp_path, "r1")
    picks = clips.load_picks(path, "r1")
    clips.add_candidate(picks, META, 10, 14)
    clips.save_picks(path, picks)
    again = clips.load_picks(path)
    assert again["candidates"][0]["video_id"] == META["video_id"]
    with pytest.raises(clips.ClipError):
        clips.picks_path(tmp_path, "../r1")


def test_download_cuts_only_the_marked_section_and_filename_follows_marks(tmp_path):
    cand = {"id": "c2", "video_id": META["video_id"], "start": 178.89, "end": 180.6}
    argv = clips.download_argv(cand, tmp_path / "x.mp4")
    assert argv[argv.index("--download-sections") + 1] == "*178.89-180.60"
    assert "--force-keyframes-at-cuts" in argv
    a = clips.clip_path(tmp_path, "r1", cand)
    b = clips.clip_path(tmp_path, "r1", {**cand, "start": 179.0})
    assert a != b  # 改了起訖就是另一個檔，不會沿用舊的下載


def test_shot_problem_flags_short_shots_and_suggests_longest_shot():
    cand = {"id": "c1", "start": 270.25, "end": 273.0}
    assert clips.shots([0.3], 2.75) == [(0.0, 0.3), (0.3, 2.75)]
    msg = clips.shot_problem(cand, [0.3])
    assert "0.30 秒切畫面" in msg and "4:30.55–4:33.00" in msg
    assert clips.shot_problem(cand, []) is None
    assert "換一段" in clips.shot_problem({"id": "c2", "start": 178.89, "end": 180.6}, [0.63])
    # 兩個鏡頭都夠長（卡通對話：Shredder 說完換 Krang）→ 不擋
    assert clips.shot_problem({"id": "k", "start": 1601.24, "end": 1606.6}, [2.8]) is None
