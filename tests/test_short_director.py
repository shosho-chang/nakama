"""run_short_director 純函數測試（雙機位導播 shot 規劃 + panel 幾何）。"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from run_short_director import (
    DEFAULT_CFG,
    FIT,
    TILT_SCALE,
    _configure_timeline,
    _find_media_item_by_path,
    _pan,
    _panel_props,
    _validate_appended_source_range,
    _validate_media_source_range,
    build_shots,
)


class _FakeMediaItem:
    def __init__(self, path: str, *, frames: int = 191_890, fps: float = 30.0):
        self.path = path
        self.frames = frames
        self.fps = fps

    def GetName(self):
        return Path(self.path).name

    def GetClipProperty(self):
        return {
            "File Path": self.path,
            "Start": "0",
            "End": str(self.frames - 1),
            "Frames": str(self.frames),
            "FPS": self.fps,
        }


class _FakeTimelineItem:
    def __init__(self, source_start: int, source_end: int):
        self.source_start = source_start
        self.source_end = source_end

    def GetSourceStartFrame(self):
        return self.source_start

    def GetSourceEndFrame(self):
        return self.source_end


class _FakeTimeline:
    def __init__(self, *, set_ok: bool = True, frame_rate: float = 30.0):
        self.set_ok = set_ok
        self.frame_rate = frame_rate
        self.calls = []

    def SetSetting(self, key, value):
        self.calls.append((key, value))
        return self.set_ok

    def GetSetting(self, key):
        assert key == "timelineFrameRate"
        return self.frame_rate


def test_configure_timeline_sets_project_fps_before_append():
    timeline = _FakeTimeline()

    _configure_timeline(timeline, fmt="long", fps=30.0)

    assert timeline.calls[0] == ("timelineFrameRate", "30")


def test_configure_timeline_rejects_template_fps_mismatch():
    timeline = _FakeTimeline(set_ok=False, frame_rate=24.0)

    with pytest.raises(SystemExit, match="Timeline frame rate mismatch"):
        _configure_timeline(timeline, fmt="long", fps=30.0)


def test_media_lookup_uses_full_path_when_basename_collides():
    wrong = _FakeMediaItem(r"G:\Footages\episode\2_CAMERA 2.mp4", frames=2_037)
    correct = _FakeMediaItem(r"G:\Footages\episode\Video\2_CAMERA 2.mp4")

    selected = _find_media_item_by_path(
        [wrong, correct],
        Path(r"G:\Footages\episode\Video\2_CAMERA 2.mp4"),
    )

    assert selected is correct


def test_media_source_range_rejects_same_name_short_clip():
    wrong = _FakeMediaItem(r"G:\Footages\episode\2_CAMERA 2.mp4", frames=2_037)

    with pytest.raises(SystemExit, match="source range.*exceeds media bounds"):
        _validate_media_source_range(wrong, 22_032, 22_341, project_fps=30.0)


def test_appended_source_range_rejects_resolve_clamp_to_last_frame():
    clamped = _FakeTimelineItem(2_036, 2_036)

    with pytest.raises(SystemExit, match="Resolve clamped source range"):
        _validate_appended_source_range(clamped, 22_032, 22_341)


def _words(*runs):
    """runs = (start, end, spk, n_words) → 均分 n 個詞。"""
    out = []
    for s, e, spk, n in runs:
        step = (e - s) / n
        for i in range(n):
            out.append((s + i * step, s + (i + 1) * step, spk))
    return out


def test_shots_switch_on_speaker_change():
    words = _words((0, 5, 1, 10), (5, 10, 0, 10))
    shots = build_shots([(0.0, 10.0)], words, DEFAULT_CFG)
    # 五輪後不再機械切分：一人一 shot、全 base zoom
    assert [s["spk"] for s in shots] == [1, 0]
    assert all(s["zoom"] == DEFAULT_CFG["zoom_base"] for s in shots)
    for a, b in zip(shots, shots[1:]):
        assert a["e"] == b["s"]  # 邊界相接無縫


def test_shots_short_interjection_absorbed():
    # 0.5s 的附和（min_shot=1.0）不切鏡；吸收後 10s 長 run 插一個反應鏡頭
    words = _words((0, 4, 1, 8), (4, 4.5, 0, 2), (4.5, 10, 1, 10))
    shots = build_shots([(0.0, 10.0)], words, DEFAULT_CFG)
    reactions = [s for s in shots if s.get("kind") == "reaction"]
    assert len(reactions) == 1
    assert reactions[0]["spk"] == 0  # 聽者 = 另一人
    assert abs((reactions[0]["e"] - reactions[0]["s"]) - DEFAULT_CFG["reaction_sec"]) < 1e-6
    assert all(s["spk"] == 1 for s in shots if s.get("kind") != "reaction")


def test_punch_keys_full_ramp_inside_shot():
    from pytest import approx
    from run_short_director import _punch_keys

    keys = _punch_keys(20.0, 30.0, 23.0, 27.0, 0.4, 1.25)
    assert [t for t, _ in keys] == approx([3.0, 3.4, 6.6, 7.0])
    assert [v for _, v in keys] == [1.0, 1.25, 1.25, 1.0]


def test_punch_keys_span_crossing_boundary():
    from run_short_director import _punch_keys

    # span 26–34 跨 shot(20–30)/shot(30–40)：前段只 ramp-in、後段只 ramp-out
    front = _punch_keys(20.0, 30.0, 26.0, 34.0, 0.4, 1.25)
    assert front[0] == (6.0, 1.0) and front[-1] == (10.0, 1.25)
    back = _punch_keys(30.0, 40.0, 26.0, 34.0, 0.4, 1.25)
    assert back[0] == (0.0, 1.25) and back[-1] == (4.0, 1.0)


def test_punch_keys_no_overlap_returns_none():
    from run_short_director import _punch_keys

    assert _punch_keys(20.0, 30.0, 31.0, 35.0, 0.4, 1.25) is None


def test_shots_long_monologue_cadence():
    # 20s 獨白：2 個反應鏡頭；覆蓋不多不少
    words = _words((0, 20, 1, 40))
    shots = build_shots([(0.0, 20.0)], words, DEFAULT_CFG)
    reactions = [s for s in shots if s.get("kind") == "reaction"]
    assert len(reactions) == 2
    total = sum(s["e"] - s["s"] for s in shots)
    assert abs(total - 20.0) < 1e-6  # 不多不少覆蓋整段


def test_pan_centers_face():
    # 臉在源片中心右側 → 影像要往左移（Pan 負）
    assert _pan(DEFAULT_CFG, 1, 3.2) < 0  # face_x 1165 > 960
    assert _pan(DEFAULT_CFG, 0, 3.2) > 0  # face_x 880 < 960


def test_panel_fills_half_frame():
    # 窗框放大後恰為 1080×960 半屏（Resolve 語意見 script 常數註解）
    p = _panel_props(DEFAULT_CFG, 1, top=True)
    win_w_fit = 1080 / p["ZoomX"]
    win_h_fit = win_w_fit * (720 / 810)
    assert abs(win_w_fit * p["ZoomX"] - 1080) < 1e-6
    assert abs(win_h_fit * p["ZoomX"] - 960) < 1e-6
    # crop 總和 = fit 畫布減窗框
    assert abs((p["CropLeft"] + p["CropRight"]) - (1920 - 810) * FIT) < 1e-6
    assert abs((p["CropTop"] + p["CropBottom"]) - (1080 - 720) * FIT) < 1e-6


def test_panel_top_bottom_tilt_signs():
    top = _panel_props(DEFAULT_CFG, 1, top=True)
    bottom = _panel_props(DEFAULT_CFG, 0, top=False)
    # Tilt 正值向上：上 panel 要向上移 → Tilt 正；下 panel 反之
    assert top["Tilt"] > 0 > bottom["Tilt"]
    # TILT_SCALE 換算後的螢幕位移量：上下各 ~480±窗框補償
    assert abs(top["Tilt"] * TILT_SCALE - 360) < 5
    assert abs(bottom["Tilt"] * TILT_SCALE + 600) < 5


def test_scurve_expand_rampin_no_overshoot():
    from run_short_director import _scurve_expand

    keys = _scurve_expand([(0.0, 1.0), (1.0, 1.25)], samples=7)
    assert keys[0] == (0.0, 1.0) and keys[-1] == (1.0, 1.25)
    vals = [v for _, v in keys]
    # 十二輪：放大直接放大，不過衝回彈——單調遞增、不超過目標
    assert max(vals) <= 1.25 + 1e-9
    assert vals == sorted(vals)
    assert [x for x, _ in keys] == sorted(x for x, _ in keys)


def test_scurve_expand_rampout_no_overshoot():
    from run_short_director import _scurve_expand

    keys = _scurve_expand([(0.0, 1.25), (0.5, 1.0)], samples=7)
    vals = [v for _, v in keys]
    assert min(vals) >= 1.0 - 1e-9  # 縮回不下衝
    assert vals == sorted(vals, reverse=True)


def test_scurve_expand_cut_not_sampled():
    from run_short_director import _scurve_expand

    # 1 frame 硬切（間距 <0.1s）不取樣——保持階梯（style=cut 直接放大）
    keys = _scurve_expand([(0.0, 1.0), (0.033, 1.25)], samples=7)
    assert keys == [(0.0, 1.0), (0.033, 1.25)]


def test_scurve_expand_hold_segments_untouched():
    from run_short_director import _scurve_expand

    keys = _scurve_expand([(0.0, 1.15), (3.0, 1.15)], samples=7)
    assert keys == [(0.0, 1.15), (3.0, 1.15)]  # hold 段不取樣


# ── 長片格式（修修 2026-08-03：Step 7 選項 B，從原始機位重導播）────────────


def test_long_cfg_overrides_short_defaults(tmp_path):
    from run_short_director import _load_cfg

    short, long_ = _load_cfg(tmp_path, "short"), _load_cfg(tmp_path, "long")
    assert short == DEFAULT_CFG  # 短片 = identity，已驗收行為不得漂移
    assert long_["reframe"] is False and long_["zoom_base"] == 1.0
    assert long_["opener_style"] == "wide" and long_["reaction_style"] == "alternate"
    assert long_["fine_subs"] is False  # Q4b：長片只上 CC，細切會讓 CC 破碎
    assert long_["face_x"] == DEFAULT_CFG["face_x"]  # 未覆蓋的沿用共通值


def test_director_json_per_format_section(tmp_path):
    import json as _json

    from run_short_director import TIGHTEN_DIR, _load_cfg

    d = tmp_path / TIGHTEN_DIR
    d.mkdir(parents=True)
    (d / "director.json").write_text(
        _json.dumps({"face_x": {"0": 900, "1": 1100}, "long": {"min_shot": 2.5}}),
        encoding="utf-8",
    )
    # 平鋪鍵套用到所有格式；分格式區塊只套到該格式
    assert _load_cfg(tmp_path, "short")["face_x"] == {"0": 900, "1": 1100}
    assert _load_cfg(tmp_path, "short")["min_shot"] == DEFAULT_CFG["min_shot"]
    assert _load_cfg(tmp_path, "long")["min_shot"] == 2.5
    assert _load_cfg(tmp_path, "long")["face_x"] == {"0": 900, "1": 1100}


def test_speaker_tokens_use_hash_bound_memo_release_without_legacy(tmp_path, monkeypatch):
    import run_short_director as director

    evidence = {
        "contract": "memo-recognition-evidence-v1",
        "normalized_audio_sha256": "audio-sha",
        "tokens": [
            {"text": "第一句", "start_ms": 1000, "end_ms": 2000},
            {"text": "第二句", "start_ms": 2000, "end_ms": 3500},
        ],
    }
    evidence_raw = json.dumps(evidence, ensure_ascii=False).encode()
    evidence_path = tmp_path / "subtitle-v2" / "memo-recognition.v1.json"
    evidence_path.parent.mkdir()
    evidence_path.write_bytes(evidence_raw)
    ledger = {
        "normalized_audio_sha256": "audio-sha",
        "inputs": {
            "memo_recognition_evidence": {
                "path": "subtitle-v2/memo-recognition.v1.json",
                "sha256": hashlib.sha256(evidence_raw).hexdigest(),
                "size_bytes": len(evidence_raw),
            }
        },
    }
    ledger_path = tmp_path / "subtitle-release" / "memo-dual-audit-v1" / "release-ledger.json"
    ledger_path.parent.mkdir(parents=True)
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
    selection = SimpleNamespace(
        mode="memo-dual-audit-v1",
        handoff=SimpleNamespace(release_ledger_path=ledger_path),
    )
    monkeypatch.setattr(
        director.Stage5SubtitleRequest,
        "open",
        lambda self, episode_dir: selection,
    )

    assert director._speaker_timing_tokens(tmp_path) == [
        {"word": "第一句", "start": 1.0, "end": 2.0},
        {"word": "第二句", "start": 2.0, "end": 3.5},
    ]


def test_speaker_tokens_reject_tampered_memo_evidence(tmp_path, monkeypatch):
    import pytest
    import run_short_director as director

    evidence_path = tmp_path / "memo.json"
    evidence_path.write_text("{}", encoding="utf-8")
    ledger_path = tmp_path / "ledger.json"
    ledger_path.write_text(
        json.dumps(
            {
                "normalized_audio_sha256": "audio-sha",
                "inputs": {
                    "memo_recognition_evidence": {
                        "path": "memo.json",
                        "sha256": "0" * 64,
                        "size_bytes": 2,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    selection = SimpleNamespace(
        mode="memo-dual-audit-v1",
        handoff=SimpleNamespace(release_ledger_path=ledger_path),
    )
    monkeypatch.setattr(
        director.Stage5SubtitleRequest,
        "open",
        lambda self, episode_dir: selection,
    )

    with pytest.raises(SystemExit, match="bytes"):
        director._speaker_timing_tokens(tmp_path)


def test_long_reactions_alternate_listener_and_wide(tmp_path):
    from run_short_director import _load_cfg

    cfg = _load_cfg(tmp_path, "long")
    # 60s 單人 run、reaction_every=12 → 多個反應鏡頭，交替切全景
    words = _words((0, 60, 1, 120))
    shots = build_shots([(0.0, 60.0)], words, cfg)
    reactions = [s for s in shots if s.get("kind") == "reaction"]
    assert len(reactions) >= 3
    assert [r.get("cam") for r in reactions[:4]] == [None, "wide", None, "wide"]
    assert all(r["spk"] == 0 for r in reactions)  # 聽者仍是另一人
    assert all(s["zoom"] == 1.0 for s in shots)  # 長片滿幀，不裁切


def test_short_reactions_never_use_wide(tmp_path):
    from run_short_director import _load_cfg

    words = _words((0, 60, 1, 120))
    shots = build_shots([(0.0, 60.0)], words, _load_cfg(tmp_path, "short"))
    assert [s for s in shots if s.get("cam") == "wide"] == []


# --- inject_reaction_cuts（修修 2026-09-03：哈/哇 內容驅動反應鏡頭）---------

from run_short_director import inject_reaction_cuts  # noqa: E402


def _cfg(**over):
    return {**DEFAULT_CFG, "zoom_base": 1.0, **over}


class TestInjectReactionCuts:
    def test_pure_reaction_from_other_speaker_splits_into_three_pieces(self):
        shots = [{"s": 0.0, "e": 10.0, "spk": 1, "kind": "talk", "zoom": 1.0}]
        words = [{"start": 5.0, "end": 5.5, "spk": 0, "word": "哇~"}]
        out = inject_reaction_cuts(shots, words, _cfg())
        assert [sh["kind"] for sh in out] == ["talk", "reaction", "talk"]
        assert out[1]["spk"] == 0  # 反應者是聽者（0），不是原說話者（1）
        assert out[0]["spk"] == 1 and out[2]["spk"] == 1
        assert out[0]["e"] == out[1]["s"] and out[1]["e"] == out[2]["s"]
        assert out[0]["s"] == 0.0 and out[2]["e"] == 10.0
        assert all(sh["zoom"] == 1.0 for sh in out)  # zoom 從原 shot 帶過來

    def test_same_speaker_own_reaction_does_not_split(self):
        """說話者自己講到一半笑出來——鏡頭本來就在他臉上，不算聽者反應。"""
        shots = [{"s": 0.0, "e": 10.0, "spk": 1, "kind": "talk", "zoom": 1.0}]
        words = [{"start": 5.0, "end": 5.5, "spk": 1, "word": "哈哈哈哈"}]
        out = inject_reaction_cuts(shots, words, _cfg())
        assert out == shots

    def test_content_word_containing_trigger_char_does_not_split(self):
        """「哇這個非常常見」是語意內容，不是純反應——不觸發。"""
        shots = [{"s": 0.0, "e": 10.0, "spk": 1, "kind": "talk", "zoom": 1.0}]
        words = [{"start": 5.0, "end": 6.0, "spk": 0, "word": "哇這個非常常見"}]
        out = inject_reaction_cuts(shots, words, _cfg())
        assert out == shots

    def test_reaction_near_shot_edge_absorbs_short_residual(self):
        """反應詞緊貼 shot 開頭——前段太短就不留碎片，直接併進反應鏡頭。"""
        shots = [{"s": 0.0, "e": 10.0, "spk": 1, "kind": "talk", "zoom": 1.0}]
        words = [{"start": 0.1, "end": 0.3, "spk": 0, "word": "哇"}]
        out = inject_reaction_cuts(shots, words, _cfg())
        assert [sh["kind"] for sh in out] == ["reaction", "talk"]
        assert out[0]["s"] == 0.0

    def test_non_talk_shots_and_opener_override_are_untouched(self):
        shots = [
            {"s": 0.0, "e": 5.0, "spk": 1, "kind": "talk", "cam": "wide", "zoom": 1.0},
            {"s": 5.0, "e": 8.0, "spk": 0, "kind": "reaction", "zoom": 1.0},
        ]
        words = [
            {"start": 1.0, "end": 1.5, "spk": 0, "word": "哇~"},
            {"start": 6.0, "end": 6.3, "spk": 1, "word": "哈哈"},
        ]
        out = inject_reaction_cuts(shots, words, _cfg())
        assert out == shots

    def test_no_trigger_words_returns_shots_unchanged(self):
        shots = [{"s": 0.0, "e": 10.0, "spk": 1, "kind": "talk", "zoom": 1.0}]
        words = [{"start": 5.0, "end": 5.3, "spk": 0, "word": "嗯"}]
        assert inject_reaction_cuts(shots, words, _cfg()) == shots

    def test_multiple_triggers_in_one_shot_each_get_a_cut(self):
        shots = [{"s": 0.0, "e": 10.0, "spk": 1, "kind": "talk", "zoom": 1.0}]
        words = [
            {"start": 2.0, "end": 2.3, "spk": 0, "word": "哈哈"},
            {"start": 7.0, "end": 7.3, "spk": 0, "word": "哇"},
        ]
        out = inject_reaction_cuts(shots, words, _cfg())
        kinds = [sh["kind"] for sh in out]
        assert kinds.count("reaction") == 2
        assert kinds == ["talk", "reaction", "talk", "reaction", "talk"]
        total = sum(sh["e"] - sh["s"] for sh in out)
        assert total == pytest.approx(10.0)
