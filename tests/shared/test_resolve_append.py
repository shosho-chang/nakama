"""shared.resolve_append 的安全包裝測試（append_checked + delete_checked）。"""

from __future__ import annotations

import pytest

from shared.resolve_append import append_checked, delete_checked


class _FakeMediaPool:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def AppendToTimeline(self, specs):
        self.calls += 1
        return self._responses.pop(0)


def test_append_checked_succeeds_first_try():
    mp = _FakeMediaPool([["item"]])
    assert append_checked(mp, ["spec"], "label", retries=3, delay=0) == ["item"]
    assert mp.calls == 1


def test_append_checked_retries_through_none_then_succeeds():
    mp = _FakeMediaPool([[None], [None], ["item"]])
    assert append_checked(mp, ["spec"], "label", retries=3, delay=0) == ["item"]
    assert mp.calls == 3


@pytest.mark.parametrize("bad_result", [[None], [], None])
def test_append_checked_raises_loud_after_exhausting_retries(bad_result):
    mp = _FakeMediaPool([bad_result, bad_result])
    with pytest.raises(SystemExit, match="上軌失敗"):
        append_checked(mp, ["spec"], "主影片", retries=2, delay=0)


class _FakeProject:
    def __init__(self, *, set_current_ok=True):
        self.set_current_ok = set_current_ok
        self.set_current_calls = []

    def SetCurrentTimeline(self, timeline):
        self.set_current_calls.append(timeline)
        return self.set_current_ok


class _FakeTimeline:
    def __init__(self, *, delete_ok=True):
        self.delete_ok = delete_ok
        self.delete_calls = []

    def DeleteClips(self, items):
        self.delete_calls.append(items)
        return self.delete_ok


def test_delete_checked_sets_current_timeline_before_deleting():
    project = _FakeProject()
    timeline = _FakeTimeline()
    delete_checked(project, timeline, ["item1", "item2"], "字幕軌")
    assert project.set_current_calls == [timeline]
    assert timeline.delete_calls == [["item1", "item2"]]


def test_delete_checked_raises_when_set_current_timeline_fails():
    project = _FakeProject(set_current_ok=False)
    timeline = _FakeTimeline()
    with pytest.raises(SystemExit, match="SetCurrentTimeline 失敗"):
        delete_checked(project, timeline, ["item"], "字幕軌")
    # SetCurrentTimeline 失敗就不該去呼叫 DeleteClips
    assert timeline.delete_calls == []


def test_delete_checked_raises_when_delete_clips_returns_false():
    project = _FakeProject()
    timeline = _FakeTimeline(delete_ok=False)
    with pytest.raises(SystemExit, match="DeleteClips 失敗"):
        delete_checked(project, timeline, ["item"], "字幕軌")
