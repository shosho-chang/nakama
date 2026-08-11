"""`_render_master` 的三道驗證（2026-08-11 安吉 SL7 事故回歸）。

事故：舊版只檢查「檔案存在嗎」，而舊檔本來就在 → Resolve render job 明明
Failed（`A read-only file SL7.mp4 already exists.`，起因是審核頁正在預覽那支
影片、FileResponse 握著 handle），script 照樣回報成功並把**上一版**重新登錄
進 DB。這裡用假的 Resolve project 物件鎖住：job 狀態要讀（且必須在
DeleteRenderJob 之前）、檔案要在、mtime 要真的變新。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from publish_prep import _render_master  # noqa: E402


class FakeTimeline:
    def GetSetting(self, key):
        return {"timelineResolutionWidth": "1920", "timelineResolutionHeight": "1080"}[key]

    def GetStartFrame(self):
        return 0

    def GetEndFrame(self):
        return 240


class FakeProject:
    """只實作 _render_master 會呼叫的方法。"""

    def __init__(self, out_path: Path, *, job_status: dict | None, writes_file: bool):
        self.out_path = out_path
        self.job_status = job_status
        self.writes_file = writes_file
        self.calls: list[str] = []

    def SetCurrentRenderFormatAndCodec(self, fmt, codec):
        return True

    def SetRenderSettings(self, settings):
        self.settings = settings
        return True

    def AddRenderJob(self):
        return "job-1"

    def StartRendering(self, jids, isInteractiveMode=False):
        if self.writes_file:
            self.out_path.write_bytes(b"new render")
            st = self.out_path.stat()
            os.utime(self.out_path, (st.st_atime + 60, st.st_mtime + 60))
        return True

    def IsRenderingInProgress(self):
        return False

    def GetRenderJobStatus(self, jid):
        self.calls.append("status")
        return self.job_status

    def DeleteRenderJob(self, jid):
        self.calls.append("delete")
        return True


def _existing(tmp_path: Path) -> Path:
    out_dir = tmp_path / "exports"
    out_dir.mkdir()
    old = out_dir / "SL7.mp4"
    old.write_bytes(b"old render from last week")
    return out_dir


def test_failed_job_fails_loud_even_when_old_file_exists(tmp_path):
    out_dir = _existing(tmp_path)
    proj = FakeProject(
        out_dir / "SL7.mp4",
        job_status={
            "JobStatus": "Failed",
            "Error": "A read-only file SL7.mp4 already exists.",
        },
        writes_file=False,
    )
    with pytest.raises(SystemExit) as exc:
        _render_master(proj, FakeTimeline(), out_dir, "SL7")
    msg = str(exc.value)
    assert "Failed" in msg
    assert "read-only file" in msg
    assert "握著這個檔" in msg  # 提示真正的成因，不要讓人再查一次


def test_status_is_read_before_job_is_deleted(tmp_path):
    out_dir = _existing(tmp_path)
    proj = FakeProject(out_dir / "SL7.mp4", job_status={"JobStatus": "Complete"}, writes_file=True)
    _render_master(proj, FakeTimeline(), out_dir, "SL7")
    assert proj.calls == ["status", "delete"]


def test_stale_file_fails_loud(tmp_path):
    """job 回報 Complete 但檔案沒被動過 → 不可以拿舊檔當新成品。"""
    out_dir = _existing(tmp_path)
    proj = FakeProject(out_dir / "SL7.mp4", job_status={"JobStatus": "Complete"}, writes_file=False)
    with pytest.raises(SystemExit) as exc:
        _render_master(proj, FakeTimeline(), out_dir, "SL7")
    assert "沒有更新" in str(exc.value)


def test_happy_path_returns_path(tmp_path):
    out_dir = _existing(tmp_path)
    proj = FakeProject(out_dir / "SL7.mp4", job_status={"JobStatus": "Complete"}, writes_file=True)
    got = _render_master(proj, FakeTimeline(), out_dir, "SL7")
    assert got == out_dir / "SL7.mp4"
    assert got.read_bytes() == b"new render"


def test_first_render_no_previous_file(tmp_path):
    out_dir = tmp_path / "exports"
    out_dir.mkdir()
    proj = FakeProject(out_dir / "SL3.mp4", job_status={"JobStatus": "Complete"}, writes_file=True)
    assert _render_master(proj, FakeTimeline(), out_dir, "SL3") == out_dir / "SL3.mp4"


def test_missing_status_api_falls_back_to_mtime(tmp_path):
    """舊版 Resolve API 沒給狀態時不硬擋，仍靠 mtime 把關。"""
    out_dir = _existing(tmp_path)
    ok = FakeProject(out_dir / "SL7.mp4", job_status=None, writes_file=True)
    assert _render_master(ok, FakeTimeline(), out_dir, "SL7") == out_dir / "SL7.mp4"

    bad = FakeProject(out_dir / "SL7.mp4", job_status=None, writes_file=False)
    with pytest.raises(SystemExit):
        _render_master(bad, FakeTimeline(), out_dir, "SL7")
