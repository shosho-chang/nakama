"""Editorial Master 交付檔（MP3 320k + 燒字幕 NVENC MP4）的回歸測試。

2026-10-23 20260930 Manny 第一次跑就踩到兩個坑，這裡用假的 Resolve project 鎖住：
1. Deliver 頁停在 Audio Only 時，把 ExportVideo=True 跟其他 key 塞在同一個
   SetRenderSettings 裡不會生效 → .mp4 只有 AAC，job 照樣 Complete。
2. 緊接著上一支 render 就 StartRendering，job 會停在 Ready（根本沒開始）。
另外 MP4 沒有影像軌時必須先被規格檢查擋下，而不是在抽格那一步炸掉。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import export_editorial_master_deliverables as ex  # noqa: E402

EXPECTED = 6492.0
FPS = 30.0


def _mp3_info(**over):
    audio = {
        "codec_type": "audio",
        "codec_name": "mp3",
        "bit_rate": "320000",
        "sample_rate": "48000",
        "channels": 2,
    }
    audio.update(over)
    return {"streams": [audio], "format": {"duration": str(EXPECTED)}}


def _mp4_info(*, video=True, encoder="H.264 NVIDIA", duration=EXPECTED):
    streams = [{"codec_type": "audio", "codec_name": "aac"}, {"codec_type": "data"}]
    if video:
        streams.insert(
            0,
            {
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1920,
                "height": 1080,
                "r_frame_rate": "30/1",
                "tags": {"encoder": encoder},
            },
        )
    return {"streams": streams, "format": {"duration": str(duration)}}


def test_check_mp3_accepts_spec():
    assert ex.check_mp3(_mp3_info(), expected_sec=EXPECTED, fps=FPS) == []


@pytest.mark.parametrize(
    "over, needle",
    [
        ({"bit_rate": "192000"}, "bit_rate"),
        ({"sample_rate": "44100"}, "sample_rate"),
        ({"channels": 1}, "channels"),
    ],
)
def test_check_mp3_rejects_wrong_spec(over, needle):
    problems = ex.check_mp3(_mp3_info(**over), expected_sec=EXPECTED, fps=FPS)
    assert any(needle in p for p in problems)


def test_check_mp3_rejects_short_file():
    info = _mp3_info()
    info["format"]["duration"] = str(EXPECTED - 1.0)
    assert any("長度" in p for p in ex.check_mp3(info, expected_sec=EXPECTED, fps=FPS))


def test_check_mp4_accepts_nvenc_1080p():
    assert ex.check_mp4(_mp4_info(), expected_sec=EXPECTED, fps=FPS, width=1920, height=1080) == []


def test_check_mp4_rejects_audio_only_file():
    # 2026-10-23 實際產出的就是這個形狀：只有 aac + timecode data
    problems = ex.check_mp4(
        _mp4_info(video=False), expected_sec=EXPECTED, fps=FPS, width=1920, height=1080
    )
    assert problems == ["MP4 必須同時有影像與音訊"]


def test_check_mp4_rejects_software_encoder():
    problems = ex.check_mp4(
        _mp4_info(encoder="H.264"), expected_sec=EXPECTED, fps=FPS, width=1920, height=1080
    )
    assert any("NVENC" in p for p in problems)


def test_subtitle_midpoints_skip_short_cues(tmp_path):
    srt = tmp_path / "m.srt"
    srt.write_text(
        "1\n00:00:01,000 --> 00:00:01,500\n短\n\n"
        "2\n00:00:10,000 --> 00:00:12,000\n夠長的一句\n\n"
        "3\n00:00:20,000 --> 00:00:22,000\n另一句\n",
        encoding="utf-8",
    )
    points = ex._subtitle_midpoints(srt, 6)
    assert 1.25 not in points
    assert set(points) <= {11.0, 21.0} and points


class FakeProject:
    def __init__(
        self,
        out: Path,
        *,
        ready_rounds: int = 0,
        final_status: str = "Complete",
        writes: bool = True,
    ):
        self.out = out
        self.calls: list[tuple] = []
        self.ready_rounds = ready_rounds
        self.final_status = final_status
        self.writes = writes
        self.starts = 0

    def LoadRenderPreset(self, name):
        self.calls.append(("preset", name))
        return True

    def SetCurrentRenderFormatAndCodec(self, fmt, codec):
        self.calls.append(("format", fmt, codec))
        return True

    def SetRenderSettings(self, settings):
        self.calls.append(("settings", dict(settings)))
        return True

    def AddRenderJob(self):
        return "job-1"

    def StartRendering(self, jobs, isInteractiveMode=False):
        self.starts += 1
        if self.starts > self.ready_rounds and self.writes:
            self.out.write_bytes(b"x")
            later = self.out.stat().st_mtime + 5
            os.utime(self.out, (later, later))
        return True

    def IsRenderingInProgress(self):
        return False

    def GetRenderJobStatus(self, job):
        if self.starts <= self.ready_rounds:
            return {"JobStatus": "Ready"}
        return {"JobStatus": self.final_status}

    def DeleteRenderJob(self, job):
        self.calls.append(("delete", job))
        return True

    def StopRendering(self):
        return True


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(ex.time, "sleep", lambda s: None)


def test_render_loads_preset_and_sends_export_flags_first(tmp_path):
    out = tmp_path / "x.mp4"
    project = FakeProject(out)
    settings = {
        "MarkIn": 0,
        "MarkOut": 10,
        "ExportVideo": True,
        "ExportAudio": True,
        "TargetDir": str(tmp_path),
    }
    ex._render(project, settings, "mp4", "H264_NVIDIA", out, 60)
    kinds = [c[0] for c in project.calls]
    assert kinds[:4] == ["preset", "format", "settings", "settings"]
    assert project.calls[0] == ("preset", ex.BASE_PRESET)
    assert project.calls[2] == ("settings", {"ExportVideo": True, "ExportAudio": True})
    assert project.calls[3] == ("settings", settings)


def test_render_retries_when_job_stays_ready(tmp_path):
    out = tmp_path / "x.mov"
    project = FakeProject(out, ready_rounds=1)
    assert ex._render(project, {"ExportAudio": True}, "mov", "ProRes422", out, 60) == out
    assert project.starts == 2


def test_render_fails_on_non_complete_job(tmp_path):
    out = tmp_path / "x.mp4"
    with pytest.raises(SystemExit, match="Failed"):
        ex._render(FakeProject(out, final_status="Failed"), {}, "mp4", "H264_NVIDIA", out, 60)


def test_render_fails_when_file_not_refreshed(tmp_path):
    out = tmp_path / "x.mp4"
    out.write_bytes(b"old")
    with pytest.raises(SystemExit, match="沒有更新"):
        ex._render(FakeProject(out, writes=False), {}, "mp4", "H264_NVIDIA", out, 60)
