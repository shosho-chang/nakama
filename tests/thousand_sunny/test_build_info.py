"""Tests for thousand_sunny.build_info — 「我看到的是哪一版」。

背景見 build_info.py 的模組 docstring：2026-09-14 兩個已合併的修正都沒有到達
修修面前，因為 Bridge 的 process 是三天前啟動的。這裡鎖的是那個判斷的邊界。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from thousand_sunny import build_info


def _boot(monkeypatch, *, commit, started_at=None):
    monkeypatch.setattr(
        build_info,
        "BOOT",
        build_info.BootIdentity(
            commit=commit,
            branch="main",
            committed_at="2026-09-11T10:00:00+08:00",
            started_at=started_at or datetime(2026, 9, 11, 2, 0, tzinfo=UTC),
        ),
    )


def test_same_commit_is_not_stale(monkeypatch):
    _boot(monkeypatch, commit="a" * 40)
    monkeypatch.setattr(build_info, "_git", lambda *args: "a" * 40)

    stamp = build_info.build_stamp()

    assert stamp["stale"] is False
    assert stamp["known"] is True
    assert stamp["commit_short"] == "aaaaaaa"


def test_disk_moved_ahead_of_running_process_is_stale(monkeypatch):
    """這就是 9/11 那個 process 的處境：磁碟上已經是新碼，它還跑在舊的。"""
    _boot(monkeypatch, commit="a" * 40)
    monkeypatch.setattr(build_info, "_git", lambda *args: "b" * 40)

    stamp = build_info.build_stamp()

    assert stamp["stale"] is True
    assert stamp["commit_short"] == "aaaaaaa"
    assert stamp["current_commit_short"] == "bbbbbbb"


def test_unknown_commit_never_claims_stale_or_fresh(monkeypatch):
    """讀不到 commit 是「不知道」，不是「沒問題」。

    非 git 部署、或 git 不在 PATH 上時 `_git` 全回 None。如果那時把 stale 算成
    False，header 就會安靜地宣稱一切正常——那正是這個功能要消滅的那種沉默。
    """
    _boot(monkeypatch, commit=None)
    monkeypatch.setattr(build_info, "_git", lambda *args: None)

    stamp = build_info.build_stamp()

    assert stamp["known"] is False
    assert stamp["stale"] is False
    assert stamp["commit"] is None
    assert stamp["commit_short"] is None


def test_half_known_commit_is_not_treated_as_a_mismatch(monkeypatch):
    """啟動時讀得到、現在讀不到（或反過來）也只是不知道，不能報成需重啟。"""
    _boot(monkeypatch, commit="a" * 40)
    monkeypatch.setattr(build_info, "_git", lambda *args: None)

    stamp = build_info.build_stamp()

    assert stamp["known"] is False
    assert stamp["stale"] is False


def test_uptime_counts_from_boot(monkeypatch):
    started = datetime(2026, 9, 11, 2, 0, tzinfo=UTC)
    _boot(monkeypatch, commit="a" * 40, started_at=started)
    monkeypatch.setattr(build_info, "_git", lambda *args: "a" * 40)

    stamp = build_info.build_stamp(now=started + timedelta(days=3, hours=2))

    assert stamp["uptime_seconds"] == (3 * 86400) + (2 * 3600)
    assert stamp["started_at"] == started.isoformat()


def test_git_failure_is_swallowed(monkeypatch):
    """git 回非 0 或根本不存在時回 None，不能讓例外冒到頁面上。"""

    class _Failed:
        returncode = 128
        stdout = ""

    monkeypatch.setattr(build_info.subprocess, "run", lambda *a, **k: _Failed())
    assert build_info._git("rev-parse", "HEAD") is None

    def _explode(*args, **kwargs):
        raise OSError("git not found")

    monkeypatch.setattr(build_info.subprocess, "run", _explode)
    assert build_info._git("rev-parse", "HEAD") is None
