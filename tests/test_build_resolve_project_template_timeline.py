"""build_resolve_project 的模板 timeline——卡死的那條要丟掉重匯，不是一直對它重試。

2026-10-08 洪瀞集：建置當下匯入的模板 timeline 整條卡死，對它 append 幾分鐘後
仍回 `[None]`；同一 project 重新匯入一條就立刻成功（5/5）。之前兩次（8/05、9/03）
都被當成時序問題放寬重試，結果重試的對象永遠是同一條壞掉的 timeline。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "build_resolve_project", _REPO / "scripts" / "build_resolve_project.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_resolve_project"] = module
    spec.loader.exec_module(module)
    return module


class _Timeline:
    def __init__(self, index: int) -> None:
        self.index = index
        self.name = "subtitle-template"

    def SetName(self, name: str) -> bool:  # noqa: N802 - Resolve API
        self.name = name
        return True

    def GetName(self) -> str:  # noqa: N802 - Resolve API
        return self.name


class _Project:
    def __init__(self) -> None:
        self.current = None

    def SetCurrentTimeline(self, timeline) -> bool:  # noqa: N802 - Resolve API
        self.current = timeline
        return True


class _MediaPool:
    def __init__(self, *, imports_ok: int = 99, delete_ok: bool = True) -> None:
        self.imported: list[_Timeline] = []
        self.deleted: list[_Timeline] = []
        self._imports_ok = imports_ok
        self._delete_ok = delete_ok

    def ImportTimelineFromFile(self, path: str, options: dict):  # noqa: N802 - Resolve API
        if len(self.imported) >= self._imports_ok:
            return None
        timeline = _Timeline(len(self.imported) + 1)
        self.imported.append(timeline)
        return timeline

    def DeleteTimelines(self, timelines) -> bool:  # noqa: N802 - Resolve API
        self.deleted.extend(timelines)
        return self._delete_ok


def _fill_failing_on(*bad_indexes: int):
    filled: list[_Timeline] = []

    def fill(timeline: _Timeline) -> None:
        if timeline.index in bad_indexes:
            raise SystemExit("主影片（純視訊）: 上軌失敗，重試 6 次仍回 [None]")
        filled.append(timeline)

    return fill, filled


def test_healthy_template_timeline_is_used_as_is():
    module = _load()
    project, pool = _Project(), _MediaPool()
    fill, filled = _fill_failing_on()

    timeline = module.timeline_from_template(project, pool, Path("t.drt"), "20261001 洪瀞", fill)

    assert timeline is pool.imported[0]
    assert timeline.GetName() == "20261001 洪瀞"
    assert project.current is timeline
    assert filled == [timeline]
    assert pool.deleted == []


def test_stuck_template_timeline_is_dropped_and_reimported():
    """第一條卡死 → 刪掉、重匯、改名、填入第二條。"""
    module = _load()
    project, pool = _Project(), _MediaPool()
    fill, filled = _fill_failing_on(1)

    timeline = module.timeline_from_template(project, pool, Path("t.drt"), "20261001 洪瀞", fill)

    assert len(pool.imported) == 2
    assert pool.deleted == [pool.imported[0]]
    assert timeline is pool.imported[1]
    assert timeline.GetName() == "20261001 洪瀞"
    assert project.current is timeline
    assert filled == [timeline]


def test_second_stuck_timeline_fails_loud():
    """只重匯一次；第二條也卡就照原樣 raise，不無限重來。"""
    module = _load()
    project, pool = _Project(), _MediaPool()
    fill, _ = _fill_failing_on(1, 2)

    with pytest.raises(SystemExit, match="上軌失敗"):
        module.timeline_from_template(project, pool, Path("t.drt"), "x", fill)
    assert len(pool.imported) == 2


def test_stuck_timeline_that_cannot_be_deleted_fails_loud():
    """刪不掉就不能用同名重匯——直接停，不留兩條同名 timeline。"""
    module = _load()
    project, pool = _Project(), _MediaPool(delete_ok=False)
    fill, _ = _fill_failing_on(1)

    with pytest.raises(SystemExit, match="刪除失敗"):
        module.timeline_from_template(project, pool, Path("t.drt"), "x", fill)
    assert len(pool.imported) == 1


def test_first_import_failure_falls_back_to_caller():
    """模板一開始就匯不進來：回 None，讓呼叫端走無樣式建立（既有行為）。"""
    module = _load()
    project, pool = _Project(), _MediaPool(imports_ok=0)
    fill, filled = _fill_failing_on()

    assert module.timeline_from_template(project, pool, Path("t.drt"), "x", fill) is None
    assert filled == []


def test_reimport_failure_after_a_stuck_timeline_fails_loud():
    module = _load()
    project, pool = _Project(), _MediaPool(imports_ok=1)
    fill, _ = _fill_failing_on(1)

    with pytest.raises(SystemExit, match="重新匯入失敗"):
        module.timeline_from_template(project, pool, Path("t.drt"), "x", fill)
