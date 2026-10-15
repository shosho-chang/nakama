"""run_multicam_full：shot list 組裝 + CLI 拒絕條件 + Resolve 物化（fake 物件）。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import run_multicam_full as mc  # noqa: E402

CAMS = {"0": "1_CAMERA 1.mp4", "1": "2_CAMERA 2.mp4"}
WIDE = "3_CAMERA 3.mp4"
FPS = 30.0


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
    # 無縫：每段起點 = 前一段終點，蓋滿 [0, 70]
    for prev, cur in zip(shots, shots[1:]):
        assert cur["s"] == pytest.approx(prev["e"])
    assert shots[-1]["e"] == pytest.approx(70.0)

    clips = mc.shot_frames(shots, FPS, 2100, CAMS, WIDE)
    assert clips[0] == {"f0": 0, "f1": 150, "cam": WIDE, "kind": "opener"}
    assert clips[-1]["f1"] == 2100
    assert all(a["f1"] == b["f0"] for a, b in zip(clips, clips[1:]))


def test_opener_drops_inner_shots_and_clips_straddler():
    shots = [
        {"s": 0.0, "e": 3.0, "spk": 1, "kind": "talk"},
        {"s": 3.0, "e": 8.0, "spk": 0, "kind": "talk"},
        {"s": 8.0, "e": 9.0, "spk": 1, "kind": "talk"},
    ]
    out = mc.apply_opener(shots, 5.0, 1.0)
    assert [(sh["s"], sh["e"], sh.get("cam")) for sh in out] == [
        (0.0, 5.0, "wide"),
        (5.0, 8.0, None),
        (8.0, 9.0, None),
    ]


def test_opener_zero_keeps_shots():
    shots = [{"s": 0.0, "e": 3.0, "spk": 1, "kind": "talk"}]
    assert mc.apply_opener(shots, 0, 1.0) is shots


def test_no_assigned_speaker_fails():
    tokens, _ = _words([(0.0, 1.0, 0, "嗯")])
    with pytest.raises(SystemExit, match="說話者"):
        mc.build_full_shots(tokens, [None], 10.0, dict(mc.DEFAULT_CFG))


def test_shot_frames_skips_zero_frame_shot_without_gap():
    shots = [
        {"s": 0.0, "e": 5.0, "spk": 0, "cam": "wide", "kind": "opener"},
        {"s": 5.0, "e": 5.01, "spk": 1, "kind": "talk"},  # 捨入成 0 格
        {"s": 5.01, "e": 10.0, "spk": 0, "kind": "talk"},
    ]
    clips = mc.shot_frames(shots, FPS, 300, CAMS, WIDE)
    assert [(c["f0"], c["f1"], c["cam"]) for c in clips] == [(0, 150, WIDE), (150, 300, CAMS["0"])]


def test_shot_frames_rejects_gap_and_short_coverage():
    gap = [{"s": 0.0, "e": 4.0, "spk": 0}, {"s": 5.0, "e": 10.0, "spk": 1}]
    with pytest.raises(SystemExit, match="空隙"):
        mc.shot_frames(gap, FPS, 300, CAMS, WIDE)
    short = [{"s": 0.0, "e": 9.0, "spk": 0}]
    with pytest.raises(SystemExit, match="只蓋到"):
        mc.shot_frames(short, FPS, 300, CAMS, WIDE)


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


class FakeItem:
    def __init__(self, name, start, end, left=0, src0=None):
        self.name, self.start, self.end, self.left = name, start, end, left
        self.src0 = left if src0 is None else src0
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
        return self.src0

    def GetSourceEndFrame(self):
        return self.src0 + self.end - self.start

    def SetProperty(self, key, value):
        self.props[key] = value
        return True

    def GetProperty(self, key):
        return self.props[key]


class FakeTimeline:
    def __init__(self, name, tracks, start=0, fps="30"):
        self.name, self.tracks, self.start, self.fps = name, tracks, start, fps
        self.duplicated = []

    def GetName(self):
        return self.name

    def GetStartFrame(self):
        return self.start

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
                k: [FakeItem(x.name, x.start, x.end, x.left) for x in v]
                for k, v in self.tracks.items()
            },
            self.start,
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
            tl = self.project.current
            item = FakeItem(
                spec["mediaPoolItem"].GetName(),
                spec["recordFrame"],
                spec["recordFrame"] + spec["endFrame"] - spec["startFrame"],
                src0=spec["startFrame"],
            )
            tl.tracks.setdefault(("video", 1), []).append(item)
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


PROGRAM = "Default_2026-09-03_1.mp4"
TOTAL = 300


def _source(v1=None):
    return FakeTimeline(
        "EP",
        {
            ("video", 1): v1 if v1 is not None else [FakeItem(PROGRAM, 0, TOTAL)],
            ("audio", 1): [FakeItem("Live-Mix.wav", 0, TOTAL)],
            ("subtitle", 1): [FakeItem("cue1", 0, 90), FakeItem("cue2", 90, 200)],
        },
    )


def _camera_files(root: Path):
    return {name: root / "Video" / name for name in (CAMS["0"], CAMS["1"], WIDE)}


def _project(tmp_path, *, extra=(), v1=None):
    media = [FakeMediaItem(p) for p in _camera_files(tmp_path).values()]
    return FakeProject("EP", [_source(v1), *extra], media)


def _preflight(project):
    return mc.preflight(
        project, project_name="EP", source="EP", new="EP（三機）", program_name=PROGRAM, fps=FPS
    )


def test_preflight_refuses_existing_new_timeline(tmp_path):
    project = _project(tmp_path, extra=[FakeTimeline("EP（三機）", {})])
    with pytest.raises(SystemExit, match="不覆寫"):
        _preflight(project)
    assert project.timelines[0].duplicated == []


def test_preflight_refuses_already_cut_timeline(tmp_path):
    cut = [FakeItem(PROGRAM, 0, 100), FakeItem(PROGRAM, 100, 300, left=150)]
    with pytest.raises(SystemExit, match="不是單機直切"):
        _preflight(_project(tmp_path, v1=cut))
    trimmed = [FakeItem(PROGRAM, 0, 300, left=30)]
    with pytest.raises(SystemExit, match="不是單機直切"):
        _preflight(_project(tmp_path, v1=trimmed))
    three_cam = [FakeItem(CAMS["0"], 0, 300)]
    with pytest.raises(SystemExit, match="不是單機直切"):
        _preflight(_project(tmp_path, v1=three_cam))


def test_preflight_refuses_wrong_project_and_missing_source(tmp_path):
    project = _project(tmp_path)
    with pytest.raises(SystemExit, match="不是「OTHER」"):
        mc.preflight(
            project, project_name="OTHER", source="EP", new="N", program_name=PROGRAM, fps=FPS
        )
    with pytest.raises(SystemExit, match="找不到原 timeline"):
        mc.preflight(
            project, project_name="EP", source="NOPE", new="N", program_name=PROGRAM, fps=FPS
        )


def test_preflight_ok_returns_program_length(tmp_path):
    src, total = _preflight(_project(tmp_path))
    assert src.GetName() == "EP" and total == TOTAL


def _clips():
    shots = [
        {"s": 0.0, "e": 5.0, "spk": 0, "cam": "wide", "kind": "opener"},
        {"s": 5.0, "e": 7.0, "spk": 1, "kind": "talk"},
        {"s": 7.0, "e": 8.0, "spk": 0, "kind": "reaction", "cam": "wide"},
        {"s": 8.0, "e": 10.0, "spk": 0, "kind": "talk"},
    ]
    return mc.shot_frames(shots, FPS, TOTAL, CAMS, WIDE)


def test_materialize_rebuilds_v1_only_and_verifies(tmp_path):
    project = _project(tmp_path)
    src, _ = _preflight(project)
    before = (mc._v1_sig(src), mc._a1_sig(src), mc._st_sig(src))
    saved = []
    report = mc.materialize(
        project,
        src,
        new="EP（三機）",
        clips=_clips(),
        camera_files=_camera_files(tmp_path),
        wide_cam=WIDE,
        wide_zoom=1.4,
        fps=FPS,
        save=lambda: saved.append(True) or True,
    )
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
    assert (mc._v1_sig(src), mc._a1_sig(src), mc._st_sig(src)) == before
    assert report == {
        "v1_clips": 4,
        "v1_holes": 0,
        "a1_items": 1,
        "subtitle_items": 2,
        "wide_clips": 2,
        "wide_zoomed": 2,
    }
    assert saved == [True]


def test_verify_failure_does_not_save(tmp_path, monkeypatch):
    project = _project(tmp_path)
    src, _ = _preflight(project)
    # Resolve 吃掉一次 zoom 設定 → 驗證要抓到，且不存檔
    real_set = FakeItem.SetProperty
    monkeypatch.setattr(
        FakeItem,
        "SetProperty",
        lambda self, k, v: True if self.start == 210 else real_set(self, k, v),
    )
    saved = []
    with pytest.raises(SystemExit, match="全景 zoom"):
        mc.materialize(
            project,
            src,
            new="EP（三機）",
            clips=_clips(),
            camera_files=_camera_files(tmp_path),
            wide_cam=WIDE,
            wide_zoom=1.4,
            fps=FPS,
            save=lambda: saved.append(True) or True,
        )
    assert saved == []


def test_materialize_refuses_missing_camera_in_media_pool(tmp_path):
    project = _project(tmp_path)
    project.pool.items = project.pool.items[:2]
    src, _ = _preflight(project)
    with pytest.raises(SystemExit, match="media pool 找不到"):
        mc.materialize(
            project,
            src,
            new="EP（三機）",
            clips=_clips(),
            camera_files=_camera_files(tmp_path),
            wide_cam=WIDE,
            wide_zoom=1.4,
            fps=FPS,
            save=lambda: True,
        )
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


def test_cli_refuses_overwrite_before_any_analysis(episode, tmp_path, monkeypatch):
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
    tokens, speakers = _words([(0.0, 4.0, 0, "嗯"), (4.0, 9.5, 1, "對")])
    monkeypatch.setattr(mc, "check_alignment", lambda *_a: {WIDE: 0.0})
    monkeypatch.setattr(
        mc,
        "speaker_tokens",
        lambda _ep: (tokens, speakers, [Path("1_COMBO-1.wav"), Path("2_COMBO-2.wav")]),
    )

    def no_resolve():
        raise AssertionError("dry run 不可連 Resolve")

    result = mc.run(mc._parser().parse_args(_argv(episode, "--dry-run")), connect=no_resolve)
    assert result["dry_run"] is True
    assert result["frames"] == 300
    assert result["kinds"]["opener"] == 1


def test_cli_rejects_wide_zoom_below_one(episode):
    argv = _argv(episode, "--dry-run")
    argv[argv.index("--wide-zoom") + 1] = "0.9"
    with pytest.raises(SystemExit, match="黑邊"):
        mc.run(mc._parser().parse_args(argv))


def test_cli_requires_explicit_wide_zoom_and_cams(episode):
    with pytest.raises(SystemExit):
        mc._parser().parse_args([str(episode), "--source-timeline", "EP", "--new-timeline", "N"])


def test_speaker_tokens_requires_exactly_two_mics(tmp_path, monkeypatch):
    (tmp_path / "Audio").mkdir()
    monkeypatch.setattr(
        mc, "load_speaker_timing_tokens", lambda _ep: [{"word": "嗯", "start": 0, "end": 1}]
    )
    monkeypatch.setattr(mc, "detect_mic_tracks", lambda _d: [])
    with pytest.raises(SystemExit, match="剛好 2 軌"):
        mc.speaker_tokens(tmp_path)
