"""第 11 類他人影片引用的交件：只接核准且已下載的片段、長度規則、收據。不渲、不連網。"""

from __future__ import annotations

from pathlib import Path

import pytest

from agents.brook.youtube_video import clips
from agents.brook.youtube_video import visual_plan as vp
from agents.brook.youtube_video.visuals import delivery
from agents.brook.youtube_video.visuals import others_clip as oc

META = {
    "video_id": "wTBhRVkOSJM",
    "title": "RR 376",
    "channel": "Rational Reminder",
    "duration": 3846,
}


def _plan() -> dict:
    cues = [
        {"id": i, "start": i * 4.0, "end": i * 4.0 + 3.5, "text": f"第 {i} 句"}
        for i in range(1, 11)
    ]
    row = {
        "id": "clip",
        "origin": "director",
        "kind": "overlay",
        "category": "others_clip",
        "start_cue": 2,
        "end_cue": 3,
        "before_cue": None,
        "insert_sec": None,
        "layout": "full",
        "description": "Nick 訪談",
        "dp_instruction": "Nick",
        "onscreen_text": "",
        "source": "各節目",
        "status": "approved",
        "note": "",
        "program_state": None,
    }
    row["original"] = vp.snapshot(row)
    return {"schema": vp.SCHEMA, "episode": "ep", "cues": cues, "rows": [row]}


@pytest.fixture
def episode(tmp_path) -> Path:
    vp.save_plan(tmp_path / vp.PLAN_FILE, _plan())
    return tmp_path


def _picks(episode: Path, spans, statuses) -> None:
    picks = clips.new_picks("clip")
    for (a, b), status in zip(spans, statuses, strict=True):
        cand = clips.add_candidate(picks, META, a, b)
        cand["status"] = status
    clips.save_picks(clips.picks_path(episode, "clip"), picks)


def _download_all(episode: Path) -> None:
    for cand in clips.approved(clips.load_picks(clips.picks_path(episode, "clip"))):
        path = clips.clip_path(episode, "clip", cand)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")


def _row(episode: Path):
    plan = vp.load_plan(vp.plan_path(episode))
    return plan, plan["rows"][0]


def test_only_approved_downloaded_clips_in_order(episode):
    _picks(episode, [(10, 13), (20, 22), (30, 32.5)], ["approved", "rejected", "approved"])
    _download_all(episode)
    plan, row = _row(episode)
    segs, start, total = oc.plan_segments(episode, plan, row)
    # 片段從 0.5 秒（橘底、碎片進場後窗拉開）開始播，播完再 0.3 秒退場
    assert [s["at"] for s in segs] == [0.5, 3.5]
    assert total == 6.3
    assert start == 8.0  # 這一列從第 2 句開頭
    assert (
        oc.label_variables(segs, total)["segments"]
        == '[[0.5, "Rational Reminder"], [3.5, "Rational Reminder"]]'
    )


def test_blocks_when_nothing_approved_or_not_downloaded(episode):
    plan, row = _row(episode)
    with pytest.raises(delivery.DeliveryError, match="候選片段"):
        oc.plan_segments(episode, plan, row)
    _picks(episode, [(10, 13)], ["proposed"])
    with pytest.raises(delivery.DeliveryError, match="標「要」"):
        oc.plan_segments(episode, plan, row)
    _picks(episode, [(10, 13)], ["approved"])
    with pytest.raises(delivery.DeliveryError, match="還沒下載"):
        oc.plan_segments(episode, plan, row)


def test_blocks_when_clips_longer_than_the_row(episode):
    # 這一列 4.0–15.5 秒＝7.5 秒；片段 9 秒超過 1 秒容許
    _picks(episode, [(10, 15), (20, 24)], ["approved", "approved"])
    _download_all(episode)
    plan, row = _row(episode)
    with pytest.raises(delivery.DeliveryError, match="這一列只有"):
        oc.plan_segments(episode, plan, row)


def test_deliver_row_writes_receipt_and_reuses(episode):
    _picks(episode, [(10, 13), (30, 32.5)], ["approved", "approved"])
    _download_all(episode)
    plan, row = _row(episode)
    calls = []

    def runner(argv, cwd):
        calls.append(argv[0])
        if argv[0] == "ffmpeg" and "prores_ks" in argv:
            Path(argv[-1]).write_bytes(b"mov")

    receipt, rendered = oc.deliver_row(episode, plan, row, runner=runner, cut_check=lambda p: [])
    assert rendered and calls == ["ffmpeg", "npx", "ffmpeg"]
    assert receipt["duration_sec"] == 6.3 and receipt["start_sec"] == 8.0
    assert receipt["variables"]["clips"] == [["wTBhRVkOSJM", 10, 13], ["wTBhRVkOSJM", 30, 32.5]]
    _, again = oc.deliver_row(episode, plan, row, runner=runner, cut_check=lambda p: [])
    assert not again


def test_blocks_clip_whose_source_cuts_to_a_flash_shot(episode):
    # Rational Reminder 4:30.25 起頭 0.3 秒是四格畫面才切到三人畫面：觀眾會覺得畫面閃一下
    _picks(episode, [(10, 13)], ["approved"])
    _download_all(episode)
    plan, row = _row(episode)
    with pytest.raises(delivery.DeliveryError, match="0:10.30–0:13.00"):
        oc.deliver_row(episode, plan, row, runner=lambda a, c: None, cut_check=lambda p: [0.3])


def test_others_clip_is_a_ready_category_with_a_dp_component():
    # 元件做好了：規劃表不再標「待製作」，DP 總調度找得到它
    from agents.brook.youtube_video import dp
    from agents.brook.youtube_video.categories import load_categories

    assert load_categories()["others_clip"].readiness == "ready"
    assert dp.RENDERERS["others_clip"].endswith("visuals.others_clip")
