"""Tests for queue_processor.py --watch mode (FU-3).

TDD red → green: tests are written before the implementation.
"""

from __future__ import annotations

import importlib.util
import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

# Load queue_processor via importlib so the hyphenated skill path doesn't
# require sys.path pollution (same pattern as test_parse_book.py).
_SKILL_SCRIPTS = (
    Path(__file__).resolve().parents[3] / ".claude" / "skills" / "textbook-ingest" / "scripts"
)
_spec = importlib.util.spec_from_file_location(
    "queue_processor", _SKILL_SCRIPTS / "queue_processor.py"
)
queue_processor = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
sys.modules["queue_processor"] = queue_processor
_spec.loader.exec_module(queue_processor)  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def test_watch_subcommand_registered():
    """``watch`` is a recognised subcommand with default interval=60."""
    args = queue_processor._build_parser().parse_args(["watch"])
    assert args.command == "watch"
    assert args.interval == 60


def test_watch_subcommand_custom_interval():
    args = queue_processor._build_parser().parse_args(["watch", "--interval", "30"])
    assert args.interval == 30


# ---------------------------------------------------------------------------
# _cmd_watch behaviour (driven by injectable _stop event)
# ---------------------------------------------------------------------------


class _FakeProc:
    returncode = 0

    def wait(self):
        pass


def test_watch_dispatches_subprocess_when_book_queued(monkeypatch):
    """When next_queued() returns a book_id, Popen is called with claude CLI args."""
    stop = threading.Event()
    dispatched = threading.Event()
    popen_calls: list = []

    call_count = 0

    def fake_next_queued():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return "my-book"
        stop.set()  # stop after processing the first item
        return None

    def fake_popen(args, **kwargs):
        popen_calls.append(args)
        dispatched.set()
        return _FakeProc()

    monkeypatch.setattr("shared.book_queue.next_queued", fake_next_queued)
    with patch("subprocess.Popen", fake_popen):
        queue_processor._cmd_watch(interval=0, _stop=stop)

    assert popen_calls == [["claude", "-p", "/textbook-ingest --from-queue"]]


def test_watch_no_subprocess_when_queue_empty(monkeypatch):
    """When next_queued() returns None, Popen is never called."""
    stop = threading.Event()
    popen_calls: list = []
    call_count = 0

    def fake_next_queued():
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            stop.set()
        return None

    def fake_popen(args, **kwargs):
        popen_calls.append(args)
        return _FakeProc()

    monkeypatch.setattr("shared.book_queue.next_queued", fake_next_queued)
    with patch("subprocess.Popen", fake_popen):
        queue_processor._cmd_watch(interval=0, _stop=stop)

    assert popen_calls == []


def test_watch_exits_when_stop_set(monkeypatch):
    """Loop exits promptly once _stop is set."""
    stop = threading.Event()
    stop.set()  # already stopped

    monkeypatch.setattr("shared.book_queue.next_queued", lambda: None)
    with patch("subprocess.Popen", MagicMock()):
        result = queue_processor._cmd_watch(interval=0, _stop=stop)

    assert result == 0


def test_watch_continues_polling_after_ingest(monkeypatch):
    """After finishing one ingest, the loop polls again before stopping."""
    stop = threading.Event()
    popen_calls: list = []
    call_count = 0

    def fake_next_queued():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return "book-a"
        if call_count == 2:
            return "book-b"
        stop.set()
        return None

    def fake_popen(args, **kwargs):
        popen_calls.append(args)
        return _FakeProc()

    monkeypatch.setattr("shared.book_queue.next_queued", fake_next_queued)
    with patch("subprocess.Popen", fake_popen):
        queue_processor._cmd_watch(interval=0, _stop=stop)

    assert len(popen_calls) == 2
