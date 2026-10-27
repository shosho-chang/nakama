"""run_multicam_full：shot list、插話切回、剪輯段 coalesce、CLI 拒絕條件、Resolve 物化（fake）。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import run_multicam_full as mc  # noqa: E402

CAMS = {"0": "1_CAMERA 1.mp4", "1": "2_CAMERA 2.mp4"}
WIDE = "3_CAMERA 3.mp4"
FPS = 30.0
P = dict(mc.INTERJECT_DEFAULTS)


def _words(spans):
    """[(start, end, spk, text)] → tokens, speakers"""
    tokens = [{"word": w, "start": s, "end": e} for s, e, _k, w in spans]
    return tokens, [k for _s, _e, k, _w in spans]


def _conversation():
    """0–40s 主持人獨白（觸發節奏反應鏡頭）→ 40–70s 來賓，來賓講到 45s 時主持人「哈哈」。"""
    spans = [(t, t + 0.4, 0, "嗯") for t in [x * 0.5 for x in range(0, 80)]]
    spans += [
        (t, t + 0.4, 1, "對") for t in [40 + x * 0.5 for x in range(0, 60)] if not 45 <= t < 45.6
    ]
    spans.append((45.0, 45.5, 0, "哈哈"))
    spans.sort()
    return _words(spans)


def _quiet_env(seconds: float) -> np.ndarray:
    """兩軌都在 -60 dB（無聲）的 50ms 包絡。"""
    n = int(round(seconds / mc.FRAME_SEC))
    return np.full((2, n), 10 ** (-60 / 20), dtype=np.float32)


def _loud(env: np.ndarray, spk: int, s0: float, s1: float, db: float = -20.0) -> None:
    f0, f1 = int(round(s0 / mc.FRAME_SEC)), int(round(s1 / mc.FRAME_SEC))
    env[spk, f0:f1] = 10 ** (db / 20)


def _talk(s, e, spk, **extra):
    return {"s": s, "e": e, "spk": spk, "kind": "talk", **extra}


def _seamless(shots, end):
    for prev, cur in zip(shots, shots[1:]):
        assert cur["s"] == pytest.approx(prev["e"])
    assert shots[0]["s"] == 0.0
    assert shots[-1]["e"] == pytest.approx(end)


# --- shot list -------------------------------------------------------------------


def test_full_shots_opener_reactions_and_no_gaps():
    tokens, speakers = _conversation()
    shots = mc.build_full_shots(tokens, speakers, 70.0, dict(mc.DEFAULT_CFG))

    assert shots[0] == {"s": 0.0, "e": 5.0, "spk": 0, "cam": "wide", "kind": "opener", "zoom": 1.0}
    assert all(sh["s"] >= 5.0 for sh in shots[1:])
    # 主持人 40s 獨白 > reaction_every 20s → 節奏反應鏡頭（聽者 = 來賓）
    assert any(sh["kind"] == "reaction" and not sh.get("trigger") for sh in shots)
    # 來賓講話時主持人的「哈哈」→ 內容反應鏡頭切到主持人臉上（不是全景）
    content = [sh for sh in shots if sh.get("trigger")]
    assert len(content) == 1
    assert content[0]["spk"] == 0 and "cam" not in content[0]
    assert content[0]["s"] < 45.5 and content[0]["e"] > 45.0
    _seamless(shots, 70.0)

    clips = mc.plan_clips(shots, [{"t0": 0, "t1": 2100, "s0": 0}], FPS, CAMS, WIDE)
    assert clips[0] == {"rec": 0, "f0": 0, "f1": 150, "cam": WIDE, "kind": "opener", "spk": 0}
    assert clips[-1]["f1"] == 2100
    assert all(a["f1"] == b["f0"] and a["rec"] == a["f0"] for a, b in zip(clips, clips[1:]))


def test_opener_drops_inner_shots_and_clips_straddler():
    shots = [_talk(0.0, 3.0, 1), _talk(3.0, 8.0, 0), _talk(8.0, 9.0, 1)]
    out = mc.apply_opener(shots, 0.0, 5.0, 1.0)
    assert [(sh["s"], sh["e"], sh.get("cam")) for sh in out] == [
        (0.0, 5.0, "wide"),
        (5.0, 8.0, None),
        (8.0, 9.0, None),
    ]


def test_opener_at_later_source_start_keeps_earlier_shots():
    """已剪過的 timeline：第一段從來源 10s 開始，開場全景放在 10–15s；之前的 shot 保留
    （後面的剪輯段可能還會用到那段來源）。"""
    shots = [_talk(0.0, 12.0, 1), _talk(12.0, 20.0, 0)]
    out = mc.apply_opener(shots, 10.0, 5.0, 1.0)
    assert [(sh["s"], sh["e"], sh.get("cam")) for sh in out] == [
        (0.0, 10.0, None),
        (10.0, 15.0, "wide"),
        (15.0, 20.0, None),
    ]
    assert out[1]["spk"] == 1


def test_opener_zero_keeps_shots():
    shots = [_talk(0.0, 3.0, 1)]
    assert mc.apply_opener(shots, 0.0, 0, 1.0) is shots


def test_no_assigned_speaker_fails():
    tokens, _ = _words([(0.0, 1.0, 0, "嗯")])
    with pytest.raises(SystemExit, match="說話者"):
        mc.build_full_shots(tokens, [None], 10.0, dict(mc.DEFAULT_CFG))


# --- 插話切回 --------------------------------------------------------------------


def test_token_interjection_from_listener_only():
    shots = [_talk(0.0, 20.0, 1), _talk(20.0, 40.0, 0)]
    tokens, speakers = _words(
        [
            (5.0, 6.2, 0, "我也是阿伯"),  # 聽者 1.2s → 事件
            (8.0, 8.3, 0, "嗯"),  # 太短
            (10.0, 13.0, 0, "長句"),  # 太長
            (25.0, 26.0, 0, "說話者自己"),  # 跟當下 shot 同一人
        ]
    )
    events = mc.interjection_events(shots, tokens, speakers, _quiet_env(40), P)
    assert events == [{"start": 5.0, "end": 6.2, "spk": 0, "src": "token"}]


def test_energy_interjection_bridges_short_pauses():
    """「我也是阿伯」頭兩個音中間停 0.15s——hot frame 之間 ≤ event_bridge 要接成同一段，
    切點才不會晚；長度 < event_min 的不算。"""
    shots = [_talk(0.0, 30.0, 1)]
    env = _quiet_env(30)
    _loud(env, 1, 0.0, 30.0, db=-25.0)  # 來賓一直在講
    _loud(env, 0, 10.0, 10.4)  # 修修插話：0.4s
    _loud(env, 0, 10.55, 11.2)  # 停 0.15s 後接著講
    _loud(env, 0, 20.0, 20.5)  # 只有 0.5s，< event_min 0.9
    events = mc.interjection_events(shots, [], [], env, P)
    assert len(events) == 1
    assert events[0]["spk"] == 0 and events[0]["src"] == "energy"
    assert events[0]["start"] == pytest.approx(10.0)
    assert events[0]["end"] == pytest.approx(11.2)


def test_energy_requires_loud_and_near_speaker():
    shots = [_talk(0.0, 30.0, 1)]
    env = _quiet_env(30)
    _loud(env, 1, 0.0, 30.0, db=-20.0)
    _loud(env, 0, 5.0, 7.0, db=-40.0)  # bleed 等級：不夠大聲
    _loud(env, 0, 10.0, 12.0, db=-30.0)  # 夠大聲但比說話者小 10 dB：不是重疊講話
    assert mc.interjection_events(shots, [], [], env, P) == []


def test_energy_event_capped_and_ignored_in_non_talk_shots():
    shots = [_talk(0.0, 10.0, 1), {"s": 10.0, "e": 20.0, "spk": 1, "kind": "reaction"}]
    env = _quiet_env(20)
    _loud(env, 0, 2.0, 6.0)  # 4s → 封頂 event_cap 2.5s
    _loud(env, 0, 13.0, 15.0)  # 落在 reaction shot：不處理
    events = mc.interjection_events(shots, [], [], env, P)
    assert [(e["start"], e["end"]) for e in events] == [
        (pytest.approx(2.0), pytest.approx(4.5)),
    ]


def test_events_merge_same_speaker_within_gap():
    shots = [_talk(0.0, 30.0, 1)]
    env = _quiet_env(30)
    _loud(env, 1, 0.0, 30.0, db=-25.0)
    _loud(env, 0, 5.3, 6.5)  # 能量 5.3–6.5
    tokens, speakers = _words([(5.0, 5.9, 0, "比如說")])  # token 5.0–5.9，重疊
    events = mc.interjection_events(shots, tokens, speakers, env, P)
    assert len(events) == 1
    assert events[0]["src"] == "token+energy"
    assert events[0]["start"] == pytest.approx(5.0)
    assert events[0]["end"] == pytest.approx(6.5)


def test_inject_interjections_cuts_to_interjector_face():
    shots = [_talk(0.0, 20.0, 1, zoom=1.0)]
    out = mc.inject_interjections(
        shots, [{"start": 8.0, "end": 9.2, "spk": 0, "src": "token"}], dict(mc.DEFAULT_CFG)
    )
    assert [(sh["kind"], sh["spk"]) for sh in out] == [("talk", 1), ("interject", 0), ("talk", 1)]
    cut = out[1]
    assert "cam" not in cut  # 臉部特寫，不是全景
    assert cut["s"] == pytest.approx(7.85) and cut["e"] == pytest.approx(9.65)
    _seamless(out, 20.0)


def test_inject_interjections_enforces_min_sec_and_residual():
    shots = [_talk(0.0, 20.0, 1, zoom=1.0)]
    # 0.2s 事件 → 擴成 reaction_trigger_min_sec 1.5s；前段 0.8s < min_residual 1.0 併進去
    out = mc.inject_interjections(
        shots, [{"start": 1.4, "end": 1.6, "spk": 0, "src": "energy"}], dict(mc.DEFAULT_CFG)
    )
    assert out[0]["kind"] == "interject" and out[0]["s"] == 0.0
    assert out[0]["e"] - out[0]["s"] >= 1.5
    _seamless(out, 20.0)


def test_full_shots_with_interject_keeps_existing_reactions_and_seams():
    tokens, speakers = _conversation()
    tokens.append({"word": "我也是", "start": 60.0, "end": 61.2})
    speakers.append(0)
    env = _quiet_env(70)
    shots_off = mc.build_full_shots(tokens, speakers, 70.0, dict(mc.DEFAULT_CFG))
    shots_on = mc.build_full_shots(
        tokens, speakers, 70.0, dict(mc.DEFAULT_CFG), envelopes=env, interject=P
    )
    assert not any(sh["kind"] == "interject" for sh in shots_off)
    cuts = [sh for sh in shots_on if sh["kind"] == "interject"]
    assert len(cuts) == 1 and cuts[0]["spk"] == 0 and 59.0 < cuts[0]["s"] < 60.5
    # 哈/哇內容反應鏡頭還在
    assert any(sh.get("trigger") and sh["kind"] == "reaction" for sh in shots_on)
    _seamless(shots_on, 70.0)


def test_interject_requires_envelopes():
    tokens, speakers = _conversation()
    with pytest.raises(SystemExit, match="包絡"):
        mc.build_full_shots(tokens, speakers, 70.0, dict(mc.DEFAULT_CFG), interject=P)


def test_short_base_talk_shot_is_not_interjected():
    """一來一往的短 talk shot（< base_min 4s）不插：插話鏡頭補 pad + 併碎片會把說話者整支吃掉。
    程世嘉：來賓「謝謝 真的是太捧場了」(228.30–230.94, 2.6s) 被整支換成修修鏡頭。"""
    shots = [_talk(0.0, 10.0, 0), _talk(10.0, 12.6, 1), _talk(12.6, 30.0, 0)]
    tokens, speakers = _words([(10.8, 11.8, 0, "對對對"), (20.0, 21.0, 1, "嗯對")])
    events = mc.interjection_events(shots, tokens, speakers, _quiet_env(30), P)
    assert [(e["start"], e["spk"]) for e in events] == [(20.0, 1)]
    # 守門關掉（base_min 0）就會插進短 shot——這條規則確實是它擋下的
    loose = mc.interjection_events(shots, tokens, speakers, _quiet_env(30), {**P, "base_min": 0})
    assert [(e["start"], e["spk"]) for e in loose] == [(10.8, 0), (20.0, 1)]
    out = mc.inject_interjections(shots, events, dict(mc.DEFAULT_CFG))
    assert out[1] == shots[1]  # 短 shot 原樣保留


def test_short_base_guard_uses_merged_event_midpoint():
    """守門看的是**合併後**事件中點所在的 shot（同 mc_interject_cheng 定案版）。"""
    shots = [_talk(0.0, 10.0, 1), _talk(10.0, 13.0, 1)]
    # 兩句各自中點：9.3（10s 長 shot）、10.75（3s 短 shot）→ 合併成 9.0–11.5，中點 10.25 落在短 shot
    tokens, speakers = _words([(9.0, 9.6, 0, "我也是"), (10.0, 11.5, 0, "阿伯")])
    assert mc.interjection_events(shots, tokens, speakers, _quiet_env(13), P) == []
    loose = {**P, "base_min": 0}
    events = mc.interjection_events(shots, tokens, speakers, _quiet_env(13), loose)
    assert [(e["start"], e["end"]) for e in events] == [(9.0, 11.5)]


# --- 機位 audit ------------------------------------------------------------------


def test_audit_flags_camera_on_wrong_speaker():
    env = _quiet_env(20)
    _loud(env, 1, 3.0, 6.0, db=-20.0)  # 鏡頭在 spk 0，但 spk 1 明顯主導 3s
    shots = [_talk(0.0, 10.0, 0), _talk(10.0, 20.0, 1)]
    issues = mc.audit_dominance(shots, env, mc.AUDIT_DEFAULTS)
    assert len(issues) == 1
    it = issues[0]
    assert (it["camera_spk"], it["dominant_spk"]) == (0, 1)
    assert it["s"] == pytest.approx(3.0) and it["e"] == pytest.approx(6.0)


def test_audit_tolerates_short_gaps_and_ignores_intentional_shots():
    env = _quiet_env(40)
    # 1.2s + 0.4s 空白 + 1.2s = 2.8s：空白 ≤ gap_sec 0.5，接成一段 → 報
    _loud(env, 1, 2.0, 3.2)
    _loud(env, 1, 3.6, 4.8)
    # 1.5s：太短，不報
    _loud(env, 1, 7.0, 8.5)
    # 反應鏡頭／全景裡對方主導是刻意的，不報
    _loud(env, 1, 12.0, 16.0)
    _loud(env, 1, 22.0, 26.0)
    shots = [
        _talk(0.0, 10.0, 0),
        {"s": 10.0, "e": 20.0, "spk": 0, "kind": "reaction"},
        {"s": 20.0, "e": 30.0, "spk": 0, "kind": "talk", "cam": "wide"},
        _talk(30.0, 40.0, 1),
    ]
    issues = mc.audit_dominance(shots, env, mc.AUDIT_DEFAULTS)
    assert [(round(i["s"], 2), round(i["e"], 2)) for i in issues] == [(2.0, 4.8)]


def test_audit_requires_clear_margin():
    env = _quiet_env(10)
    _loud(env, 0, 0.0, 10.0, db=-25.0)
    _loud(env, 1, 2.0, 6.0, db=-20.0)  # 只大 5 dB < margin 8：重疊講話，不算主導
    assert mc.audit_dominance([_talk(0.0, 10.0, 0)], env, mc.AUDIT_DEFAULTS) == []


# --- 剪輯段 ----------------------------------------------------------------------


class Item:
    def __init__(self, name, start, end, left=0):
        self.name, self.start, self.end, self.left = name, start, end, left
        self.props = {"ZoomX": 1.0, "ZoomY": 1.0}

    def GetName(self):
        return self.name

    def GetStart(self):
        return self.start

    def GetEnd(self):
        return self.end

    def GetLeftOffset(self):
        return self.left

    def GetDuration(self):
        return self.end - self.start

    def GetSourceStartFrame(self):
        return self.left

    def GetSourceEndFrame(self):
        return self.left + self.end - self.start

    def SetProperty(self, key, value):
        self.props[key] = value
        return True

    def GetProperty(self, key):
        return self.props[key]


PROGRAM = "Default_2026-09-03_1.mp4"
ALLOWED = {PROGRAM, CAMS["0"], CAMS["1"], WIDE}


def test_coalesce_merges_source_continuous_camera_clips():
    items = [
        Item(WIDE, 0, 150, 0),
        Item(CAMS["1"], 150, 400, 150),  # 接續：同一段
        Item(CAMS["0"], 400, 600, 900),  # tl 連續但來源跳號：剪過一刀
        Item(CAMS["1"], 700, 800, 1100),  # tl 有空隙
    ]
    assert mc.coalesce_segments(items, ALLOWED) == [
        {"t0": 0, "t1": 400, "s0": 0},
        {"t0": 400, "t1": 600, "s0": 900},
        {"t0": 700, "t1": 800, "s0": 1100},
    ]


def test_coalesce_tolerates_one_frame_report_jitter_only():
    jitter = [Item(CAMS["0"], 0, 100, 0), Item(CAMS["1"], 100, 200, 101)]  # 回報差一格：沒剪
    assert mc.coalesce_segments(jitter, ALLOWED) == [{"t0": 0, "t1": 200, "s0": 0}]
    cut = [Item(CAMS["0"], 0, 100, 0), Item(CAMS["1"], 100, 200, 102)]  # 差兩格：真的剪了
    assert len(mc.coalesce_segments(cut, ALLOWED)) == 2


def test_coalesce_single_program_clip():
    assert mc.coalesce_segments([Item(PROGRAM, 86400, 86700)], ALLOWED) == [
        {"t0": 86400, "t1": 86700, "s0": 0}
    ]


def test_coalesce_refuses_non_camera_material_and_empty():
    with pytest.raises(SystemExit, match="Intro"):
        mc.coalesce_segments([Item("Intro.mov", 0, 90), Item(PROGRAM, 90, 300, 0)], ALLOWED)
    with pytest.raises(SystemExit, match="空的"):
        mc.coalesce_segments([], ALLOWED)


def test_plan_clips_maps_each_segment_to_its_record_position():
    shots = [_talk(0.0, 10.0, 0), _talk(10.0, 40.0, 1), _talk(40.0, 60.0, 0)]
    segs = [{"t0": 86400, "t1": 86700, "s0": 0}, {"t0": 86700, "t1": 87000, "s0": 1050}]
    clips = mc.plan_clips(shots, segs, FPS, CAMS, WIDE)
    assert [(c["rec"], c["f0"], c["f1"], c["cam"]) for c in clips] == [
        (86400, 0, 300, CAMS["0"]),
        (86700, 1050, 1200, CAMS["1"]),
        (86850, 1200, 1350, CAMS["0"]),
    ]


def test_plan_clips_skips_zero_frame_shot_without_gap():
    shots = [
        {"s": 0.0, "e": 5.0, "spk": 0, "cam": "wide", "kind": "opener"},
        _talk(5.0, 5.01, 1),  # 捨入成 0 格
        _talk(5.01, 10.0, 0),
    ]
    clips = mc.plan_clips(shots, [{"t0": 0, "t1": 300, "s0": 0}], FPS, CAMS, WIDE)
    assert [(c["f0"], c["f1"], c["cam"]) for c in clips] == [(0, 150, WIDE), (150, 300, CAMS["0"])]


def test_plan_clips_rejects_gap_and_short_coverage():
    seg = [{"t0": 0, "t1": 300, "s0": 0}]
    with pytest.raises(SystemExit, match="空隙"):
        mc.plan_clips([_talk(0.0, 4.0, 0), _talk(5.0, 10.0, 1)], seg, FPS, CAMS, WIDE)
    with pytest.raises(SystemExit, match="只蓋到"):
        mc.plan_clips([_talk(0.0, 9.0, 0)], seg, FPS, CAMS, WIDE)


# --- 全景反應鏡頭延長 --------------------------------------------------------------


def _clip(rec, f0, f1, cam, kind="talk", spk=0):
    return {"rec": rec, "f0": f0, "f1": f1, "cam": cam, "kind": kind, "spk": spk}


def _seamless_clips(clips):
    for a, b in zip(clips, clips[1:]):
        assert a["rec"] + a["f1"] - a["f0"] == b["rec"]


def test_wide_reaction_extends_forward_into_next_close_up():
    clips = [
        _clip(0, 0, 300, CAMS["0"]),
        _clip(300, 300, 360, WIDE, "reaction"),  # 2.0s
        _clip(360, 360, 600, CAMS["0"]),
    ]
    stats = mc.widen_wide_reactions(clips, FPS, WIDE, 3.5, 2.0)
    assert stats == {"forward": 1, "backward": 0, "skipped": 0}
    assert (clips[1]["f0"], clips[1]["f1"]) == (300, 405)  # 3.5s
    assert (clips[2]["rec"], clips[2]["f0"], clips[2]["f1"]) == (405, 405, 600)
    _seamless_clips(clips)


def test_wide_reaction_extends_backward_when_next_too_short():
    clips = [
        _clip(0, 0, 300, CAMS["0"]),
        _clip(300, 300, 360, WIDE, "reaction"),
        _clip(360, 360, 450, CAMS["0"]),  # 3.0s：吃 1.5s 後剩 1.5s < 2.0
    ]
    stats = mc.widen_wide_reactions(clips, FPS, WIDE, 3.5, 2.0)
    assert stats == {"forward": 0, "backward": 1, "skipped": 0}
    assert (clips[0]["f1"], clips[1]["rec"], clips[1]["f0"], clips[1]["f1"]) == (255, 255, 255, 360)
    _seamless_clips(clips)


def test_wide_reaction_not_extended_across_cut_or_into_short_or_wide_neighbours():
    clips = [
        _clip(0, 0, 90, CAMS["0"]),  # 前一支太短
        _clip(90, 90, 150, WIDE, "reaction"),
        _clip(150, 900, 1200, CAMS["0"]),  # 剪輯段交界：來源跳號
    ]
    assert mc.widen_wide_reactions(clips, FPS, WIDE, 3.5, 2.0)["skipped"] == 1
    assert (clips[1]["f0"], clips[1]["f1"]) == (90, 150)


def test_opener_and_listener_close_ups_keep_their_length():
    clips = [
        _clip(0, 0, 150, WIDE, "opener"),
        _clip(150, 150, 450, CAMS["1"]),
        _clip(450, 450, 510, CAMS["0"], "reaction"),  # 聽者特寫：維持 2.0s
        _clip(510, 510, 900, CAMS["1"]),
    ]
    before = [dict(c) for c in clips]
    assert mc.widen_wide_reactions(clips, FPS, WIDE, 3.5, 2.0) == {
        "forward": 0,
        "backward": 0,
        "skipped": 0,
    }
    assert clips == before


def test_wide_reaction_sec_at_or_below_current_length_is_noop():
    clips = [_clip(0, 0, 300, CAMS["0"]), _clip(300, 300, 360, WIDE, "reaction")]
    before = [dict(c) for c in clips]
    mc.widen_wide_reactions(clips, FPS, WIDE, 2.0, 2.0)
    assert clips == before


def test_full_plan_wide_reactions_are_longer_than_listener_reactions():
    tokens, speakers = _conversation()
    shots = mc.build_full_shots(tokens, speakers, 70.0, dict(mc.DEFAULT_CFG))
    clips = mc.plan_clips(shots, [{"t0": 0, "t1": 2100, "s0": 0}], FPS, CAMS, WIDE)
    mc.widen_wide_reactions(clips, FPS, WIDE, 3.5, 2.0)
    reactions = [c for c in clips if c["kind"] == "reaction"]
    wide = [c["f1"] - c["f0"] for c in reactions if c["cam"] == WIDE]
    close = [c["f1"] - c["f0"] for c in reactions if c["cam"] != WIDE]
    assert wide == [105]  # 全景 3.5s
    assert close and all(n <= 60 for n in close)  # 聽者特寫維持 ≤ 2.0s
    _seamless_clips(clips)
    assert clips[-1]["f1"] == 2100


def test_parse_cams():
    assert mc.parse_cams("0=1_CAMERA 1.mp4, 1=2_CAMERA 2.mp4") == CAMS
    for bad in ("0=a.mp4", "0=a.mp4,1=a.mp4", "0=a.mp4,2=b.mp4", "0a.mp4,1=b.mp4", "0=a,0=b,1=c"):
        with pytest.raises(argparse.ArgumentTypeError):
            mc.parse_cams(bad)


def test_alignment_rejects_offset_over_one_frame_and_unknown():
    cams = {WIDE: Path("w"), CAMS["0"]: Path("a")}
    assert mc.check_alignment(Path("p"), cams, FPS, measure=lambda _p, _c: 0.0) == {
        WIDE: 0.0,
        CAMS["0"]: 0.0,
    }
    with pytest.raises(SystemExit, match="偏移"):
        mc.check_alignment(
            Path("p"), cams, FPS, measure=lambda _p, c: 0.167 if c.name == "a" else 0.0
        )
    with pytest.raises(SystemExit, match="量測失敗"):
        mc.check_alignment(Path("p"), cams, FPS, measure=lambda _p, _c: None)


# --- fake Resolve ----------------------------------------------------------------


class FakeMediaItem:
    def __init__(self, path: Path, frames: int = 10_000):
        self.path = str(path)
        self.frames = frames

    def GetName(self):
        return Path(self.path).name

    def GetClipProperty(self):
        return {"File Path": self.path, "Start": "0", "Frames": str(self.frames), "FPS": FPS}


class FakeTimeline:
    def __init__(self, name, tracks, fps="30"):
        self.name, self.tracks, self.fps = name, tracks, fps
        self.duplicated = []

    def GetName(self):
        return self.name

    def GetSetting(self, key):
        assert key == "timelineFrameRate"
        return self.fps

    def GetTrackCount(self, kind):
        return max([i for k, i in self.tracks if k == kind] or [0])

    def GetItemListInTrack(self, kind, index):
        return list(self.tracks.get((kind, index), []))

    def DuplicateTimeline(self, name):
        copy = FakeTimeline(
            name,
            {
                key: [Item(x.name, x.start, x.end, x.left) for x in items]
                for key, items in self.tracks.items()
            },
            self.fps,
        )
        self.duplicated.append(copy)
        self.project.timelines.append(copy)
        copy.project = self.project
        return copy

    def DeleteClips(self, items):
        if self.project.current is not self:
            return False
        ids = {id(x) for x in items}
        self.tracks[("video", 1)] = [x for x in self.tracks[("video", 1)] if id(x) not in ids]
        return True


class FakeMediaPool:
    def __init__(self, project, items):
        self.project, self.items = project, items

    def GetRootFolder(self):
        items = self.items

        class Folder:
            def GetClipList(self):
                return items

            def GetSubFolderList(self):
                return []

        return Folder()

    def AppendToTimeline(self, specs):
        out = []
        for spec in specs:
            start, end = spec["startFrame"], spec["endFrame"]
            item = Item(
                spec["mediaPoolItem"].GetName(),
                spec["recordFrame"],
                spec["recordFrame"] + end - start,
                left=start,
            )
            self.project.current.tracks.setdefault(("video", 1), []).append(item)
            out.append(item)
        return out


class FakeProject:
    def __init__(self, name, timelines, media):
        self.name, self.timelines = name, timelines
        for tl in timelines:
            tl.project = self
        self.current = timelines[0] if timelines else None
        self.pool = FakeMediaPool(self, media)

    def GetName(self):
        return self.name

    def GetTimelineCount(self):
        return len(self.timelines)

    def GetTimelineByIndex(self, i):
        return self.timelines[i - 1]

    def SetCurrentTimeline(self, tl):
        self.current = tl
        return True

    def GetMediaPool(self):
        return self.pool


TOTAL = 300


def _source(v1=None):
    return FakeTimeline(
        "EP",
        {
            ("video", 1): v1 if v1 is not None else [Item(PROGRAM, 0, TOTAL)],
            ("video", 2): [Item("title.png", 30, 60)],
            ("audio", 1): [Item("Live-Mix.wav", 0, TOTAL)],
            ("subtitle", 1): [Item("cue1", 0, 90), Item("cue2", 90, 200)],
        },
    )


def _camera_files(root: Path):
    return {name: root / "Video" / name for name in (CAMS["0"], CAMS["1"], WIDE)}


def _project(tmp_path, *, extra=(), v1=None):
    media = [FakeMediaItem(p) for p in _camera_files(tmp_path).values()]
    return FakeProject("EP", [_source(v1), *extra], media)


def _preflight(project):
    return mc.preflight(
        project, project_name="EP", source="EP", new="EP（三機）", allowed=ALLOWED, fps=FPS
    )


def test_preflight_refuses_existing_new_timeline(tmp_path):
    project = _project(tmp_path, extra=[FakeTimeline("EP（三機）", {})])
    with pytest.raises(SystemExit, match="不覆寫"):
        _preflight(project)
    assert project.timelines[0].duplicated == []


def test_preflight_refuses_non_camera_material(tmp_path):
    with pytest.raises(SystemExit, match="非機位"):
        _preflight(_project(tmp_path, v1=[Item("Outro.mov", 0, 300)]))


def test_preflight_refuses_wrong_project_missing_source_and_fps(tmp_path):
    project = _project(tmp_path)
    with pytest.raises(SystemExit, match="不是「OTHER」"):
        mc.preflight(project, project_name="OTHER", source="EP", new="N", allowed=ALLOWED, fps=FPS)
    with pytest.raises(SystemExit, match="找不到原 timeline"):
        mc.preflight(project, project_name="EP", source="NOPE", new="N", allowed=ALLOWED, fps=FPS)
    with pytest.raises(SystemExit, match="frame rate"):
        mc.preflight(project, project_name="EP", source="EP", new="N", allowed=ALLOWED, fps=25.0)


def test_preflight_single_cam_is_one_segment(tmp_path):
    src, segs = _preflight(_project(tmp_path))
    assert src.GetName() == "EP" and segs == [{"t0": 0, "t1": TOTAL, "s0": 0}]


def _shots():
    return [
        {"s": 0.0, "e": 5.0, "spk": 0, "cam": "wide", "kind": "opener"},
        _talk(5.0, 7.0, 1),
        {"s": 7.0, "e": 8.0, "spk": 0, "kind": "reaction", "cam": "wide"},
        _talk(8.0, 10.0, 0),
        _talk(10.0, 40.0, 1),
        _talk(40.0, 60.0, 0),
    ]


def _materialize(project, src, clips, saved, tmp_path):
    return mc.materialize(
        project,
        src,
        new="EP（三機）",
        clips=clips,
        camera_files=_camera_files(tmp_path),
        wide_cam=WIDE,
        wide_zoom=1.4,
        fps=FPS,
        save=lambda: saved.append(True) or True,
    )


def test_materialize_single_cam_rebuilds_v1_only_and_verifies(tmp_path):
    project = _project(tmp_path)
    src, segs = _preflight(project)
    before = (mc._clip_sig(src.GetItemListInTrack("video", 1)), mc._snapshot(src))
    saved: list = []
    clips = mc.plan_clips(_shots(), segs, FPS, CAMS, WIDE)
    report = _materialize(project, src, clips, saved, tmp_path)
    new = project.timelines[-1]
    assert new.GetName() == "EP（三機）"
    v1 = new.GetItemListInTrack("video", 1)
    assert [(x.GetName(), x.GetStart(), x.GetEnd()) for x in v1] == [
        (WIDE, 0, 150),
        (CAMS["1"], 150, 210),
        (WIDE, 210, 240),
        (CAMS["0"], 240, 300),
    ]
    assert all(x.props == {"ZoomX": 1.4, "ZoomY": 1.4} for x in v1 if x.GetName() == WIDE)
    assert all(x.props == {"ZoomX": 1.0, "ZoomY": 1.0} for x in v1 if x.GetName() != WIDE)
    assert (mc._clip_sig(src.GetItemListInTrack("video", 1)), mc._snapshot(src)) == before
    assert new.GetItemListInTrack("video", 2)[0].GetName() == "title.png"
    assert report == {
        "v1_clips": 4,
        "v1_segments": 1,
        "audio_items": 1,
        "subtitle_items": 2,
        "wide_clips": 2,
        "wide_zoomed": 2,
    }
    assert saved == [True]


def test_materialize_edited_timeline_keeps_cuts(tmp_path):
    """已剪過的三機 timeline：兩段（來源 0–300、來源 1050–1350 接在 tl 300），中間剪掉一段。"""
    edited = [
        Item(WIDE, 0, 150, 0),
        Item(CAMS["1"], 150, 300, 150),
        Item(CAMS["0"], 300, 450, 1050),
        Item(CAMS["1"], 450, 600, 1200),
    ]
    project = _project(tmp_path, v1=edited)
    src, segs = _preflight(project)
    assert segs == [{"t0": 0, "t1": 300, "s0": 0}, {"t0": 300, "t1": 600, "s0": 1050}]
    clips = mc.plan_clips(_shots(), segs, FPS, CAMS, WIDE)
    saved: list = []
    report = _materialize(project, src, clips, saved, tmp_path)
    v1 = project.timelines[-1].GetItemListInTrack("video", 1)
    assert [(x.GetName(), x.GetStart(), x.GetEnd(), x.GetLeftOffset()) for x in v1] == [
        (WIDE, 0, 150, 0),
        (CAMS["1"], 150, 210, 150),
        (WIDE, 210, 240, 210),
        (CAMS["0"], 240, 300, 240),
        (CAMS["1"], 300, 450, 1050),
        (CAMS["0"], 450, 600, 1200),
    ]
    assert report["v1_segments"] == 1 and saved == [True]
    assert mc._clip_sig(src.GetItemListInTrack("video", 1)) == mc._clip_sig(edited)


def test_verify_failure_does_not_save(tmp_path, monkeypatch):
    project = _project(tmp_path)
    src, segs = _preflight(project)
    # Resolve 吃掉一次 zoom 設定 → 驗證要抓到，且不存檔
    real_set = Item.SetProperty
    monkeypatch.setattr(
        Item, "SetProperty", lambda self, k, v: True if self.start == 210 else real_set(self, k, v)
    )
    saved: list = []
    clips = mc.plan_clips(_shots(), segs, FPS, CAMS, WIDE)
    with pytest.raises(SystemExit, match="全景 zoom"):
        _materialize(project, src, clips, saved, tmp_path)
    assert saved == []


def test_verify_catches_misplaced_clip(tmp_path, monkeypatch):
    project = _project(tmp_path)
    src, segs = _preflight(project)
    real_append = FakeMediaPool.AppendToTimeline

    def shifted(self, specs):
        out = real_append(self, specs)
        if specs[0]["recordFrame"] == 150:
            out[0].start += 1  # Resolve 整段放歪一格（長度對，位置錯）
            out[0].end += 1
        return out

    monkeypatch.setattr(FakeMediaPool, "AppendToTimeline", shifted)
    saved: list = []
    clips = mc.plan_clips(_shots(), segs, FPS, CAMS, WIDE)
    with pytest.raises(SystemExit, match="跟計畫不符"):
        _materialize(project, src, clips, saved, tmp_path)
    assert saved == []


def test_materialize_refuses_missing_camera_in_media_pool(tmp_path):
    project = _project(tmp_path)
    project.pool.items = project.pool.items[:2]
    src, segs = _preflight(project)
    clips = mc.plan_clips(_shots(), segs, FPS, CAMS, WIDE)
    with pytest.raises(SystemExit, match="media pool 找不到"):
        _materialize(project, src, clips, [], tmp_path)
    assert src.duplicated == []


# --- CLI ---------------------------------------------------------------------------


@pytest.fixture
def episode(tmp_path, monkeypatch):
    ep = tmp_path / "EP"
    (ep / "Video").mkdir(parents=True)
    (ep / PROGRAM).write_bytes(b"")
    for name in (CAMS["0"], CAMS["1"], WIDE):
        (ep / "Video" / name).write_bytes(b"")
    monkeypatch.setattr(mc, "_probe", lambda _p: {"fps": FPS, "duration": 10.0})
    return ep


def _argv(ep, *extra):
    return [
        str(ep),
        "--source-timeline",
        "EP",
        "--new-timeline",
        "EP（三機）",
        "--wide-zoom",
        "1.4",
        "--cams",
        f"0={CAMS['0']},1={CAMS['1']}",
        "--wide-cam",
        WIDE,
        *extra,
    ]


def _fake_analysis(monkeypatch, seconds=10.0):
    tokens, speakers = _words([(0.0, 4.0, 0, "嗯"), (4.0, 9.5, 1, "對")])
    monkeypatch.setattr(mc, "check_alignment", lambda *_a: {WIDE: 0.0})
    monkeypatch.setattr(
        mc,
        "speaker_tokens",
        lambda _ep, _mics=None: (
            tokens,
            speakers,
            [Path("1_COMBO-1.wav"), Path("2_COMBO-2.wav")],
            _quiet_env(seconds),
        ),
    )


def test_cli_refuses_overwrite_before_any_analysis(episode, monkeypatch):
    project = _project(episode, extra=[FakeTimeline("EP（三機）", {})])

    def boom(*_a, **_k):
        raise AssertionError("拒絕覆寫應該在能量分析之前")

    monkeypatch.setattr(mc, "check_alignment", boom)
    monkeypatch.setattr(mc, "speaker_tokens", boom)

    class Resolve:
        def GetProjectManager(self):
            class PM:
                def GetCurrentProject(self):
                    return project

            return PM()

    with pytest.raises(SystemExit, match="不覆寫"):
        mc.run(mc._parser().parse_args(_argv(episode)), connect=Resolve)
    assert project.timelines[0].duplicated == []


def test_cli_dry_run_never_connects(episode, monkeypatch):
    _fake_analysis(monkeypatch)

    def no_resolve():
        raise AssertionError("dry run 不可連 Resolve")

    result = mc.run(mc._parser().parse_args(_argv(episode, "--dry-run")), connect=no_resolve)
    assert result["dry_run"] is True
    assert result["frames"] == 300 and result["segments"] == 1
    assert result["kinds"]["opener"] == 1


def test_cli_build_end_to_end_with_fake_resolve(episode, monkeypatch):
    _fake_analysis(monkeypatch)
    project = _project(episode)
    saved: list = []

    class Resolve:
        def GetProjectManager(self):
            class PM:
                def GetCurrentProject(self):
                    return project

                def SaveProject(self):
                    saved.append(True)
                    return True

            return PM()

    result = mc.run(mc._parser().parse_args(_argv(episode)), connect=Resolve)
    assert result["v1_clips"] == result["clips"] and saved == [True]
    assert project.timelines[-1].GetName() == "EP（三機）"


def test_cli_interject_flags():
    args = mc._parser().parse_args(
        _argv(Path("x"), "--no-interject", "--interject-event-min", "0.7")
    )
    assert args.interject is False and args.interject_event_min == 0.7
    args = mc._parser().parse_args(_argv(Path("x")))
    assert args.interject is True
    assert args.wide_reaction_sec == 3.5
    assert {k: getattr(args, f"interject_{k}") for k in mc.INTERJECT_DEFAULTS} == P


def test_cli_rejects_wide_zoom_below_one(episode):
    argv = _argv(episode, "--dry-run")
    argv[argv.index("--wide-zoom") + 1] = "0.9"
    with pytest.raises(SystemExit, match="黑邊"):
        mc.run(mc._parser().parse_args(argv))


def test_cli_requires_explicit_wide_zoom_and_cams(episode):
    with pytest.raises(SystemExit):
        mc._parser().parse_args([str(episode), "--source-timeline", "EP", "--new-timeline", "N"])


def test_speaker_tokens_checks_expected_mics_and_normalized(tmp_path, monkeypatch):
    (tmp_path / "Audio").mkdir()
    monkeypatch.setattr(
        mc, "load_speaker_timing_tokens", lambda _ep: [{"word": "嗯", "start": 0, "end": 1}]
    )
    found = [Path("1_COMBO-1.wav"), Path("3_BACKUP.wav")]
    monkeypatch.setattr(mc, "detect_mic_tracks", lambda _d: found)
    with pytest.raises(SystemExit, match="不是 --mics"):
        mc.speaker_tokens(tmp_path, ["1_COMBO-1.wav", "2_COMBO-2.wav"])
    with pytest.raises(SystemExit, match="normalized.wav"):
        mc.speaker_tokens(tmp_path)


def test_materialize_refuses_renamed_media_pool_clip(tmp_path):
    project = _project(tmp_path)
    renamed = project.pool.items[2]
    renamed.GetName = lambda: "全景.mp4"
    src, segs = _preflight(project)
    clips = mc.plan_clips(_shots(), segs, FPS, CAMS, WIDE)
    with pytest.raises(SystemExit, match="名稱是「全景.mp4」"):
        _materialize(project, src, clips, [], tmp_path)
    assert src.duplicated == []


def test_failure_after_duplicate_says_how_to_clean_up(tmp_path, monkeypatch):
    project = _project(tmp_path)
    src, segs = _preflight(project)

    def exhausted(*_a, **_k):
        raise SystemExit("shot 1: 上軌失敗，重試 6 次仍回 [None]")

    monkeypatch.setattr(mc, "append_checked", exhausted)
    clips = mc.plan_clips(_shots(), segs, FPS, CAMS, WIDE)
    with pytest.raises(SystemExit, match="刪掉它再重跑"):
        _materialize(project, src, clips, [], tmp_path)
    assert project.timelines[-1].GetName() == "EP（三機）"  # 留著給人看，不自動刪


def test_unexpected_error_after_duplicate_still_warns(tmp_path, monkeypatch, capsys):
    project = _project(tmp_path)
    src, segs = _preflight(project)

    def resolve_returned_none(*_a, **_k):
        raise TypeError("'NoneType' object is not subscriptable")

    monkeypatch.setattr(mc, "append_checked", resolve_returned_none)
    clips = mc.plan_clips(_shots(), segs, FPS, CAMS, WIDE)
    with pytest.raises(TypeError):
        _materialize(project, src, clips, [], tmp_path)
    assert "刪掉它再重跑" in capsys.readouterr().err


def test_verify_tolerates_one_frame_left_offset_report(tmp_path, monkeypatch):
    """Resolve 回報的來源格會因秒數捨入差一格；位置與長度對就是放對了，不能因此擋存檔。"""
    project = _project(tmp_path)
    src, segs = _preflight(project)
    real_append = FakeMediaPool.AppendToTimeline

    def rounded(self, specs):
        out = real_append(self, specs)
        if specs[0]["recordFrame"] == 240:
            out[0].left -= 1
        return out

    monkeypatch.setattr(FakeMediaPool, "AppendToTimeline", rounded)
    saved: list = []
    clips = mc.plan_clips(_shots(), segs, FPS, CAMS, WIDE)
    _materialize(project, src, clips, saved, tmp_path)
    assert saved == [True]


def test_materialize_edited_timeline_at_hour_timecode(tmp_path):
    """timeline 起點 01:00:00:00（108000 @30fps）：recordFrame 以段的 t0 為準。"""
    t0 = 108000
    edited = [Item(PROGRAM, t0, t0 + 300, 0), Item(PROGRAM, t0 + 300, t0 + 600, 1050)]
    project = _project(tmp_path, v1=edited)
    src, segs = _preflight(project)
    clips = mc.plan_clips(_shots(), segs, FPS, CAMS, WIDE)
    saved: list = []
    _materialize(project, src, clips, saved, tmp_path)
    v1 = project.timelines[-1].GetItemListInTrack("video", 1)
    assert v1[0].GetStart() == t0 and v1[-1].GetEnd() == t0 + 600
    assert [(x.GetStart() - t0, x.GetLeftOffset()) for x in v1][-2:] == [(300, 1050), (450, 1200)]
    assert saved == [True]


def test_speaker_tokens_requires_exactly_two_mics(tmp_path, monkeypatch):
    (tmp_path / "Audio").mkdir()
    monkeypatch.setattr(
        mc, "load_speaker_timing_tokens", lambda _ep: [{"word": "嗯", "start": 0, "end": 1}]
    )
    monkeypatch.setattr(mc, "detect_mic_tracks", lambda _d: [])
    with pytest.raises(SystemExit, match="剛好 2 軌"):
        mc.speaker_tokens(tmp_path)
