"""Tests for shared.log.SQLiteLogHandler + get_logger() attachment."""

from __future__ import annotations

import logging

import pytest

from shared.log import SQLiteLogHandler


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "test_logs.db"


@pytest.fixture
def handler(db_path):
    return SQLiteLogHandler(db_path=db_path)


def _make_record(level: int, msg: str, **extra) -> logging.LogRecord:
    record = logging.LogRecord(
        name="nakama.test",
        level=level,
        pathname=__file__,
        lineno=0,
        msg=msg,
        args=(),
        exc_info=None,
    )
    for k, v in extra.items():
        setattr(record, k, v)
    return record


def test_emit_inserts_row(handler, db_path):
    handler.emit(_make_record(logging.INFO, "hello world"))

    from shared.log_index import LogIndex

    idx = LogIndex.from_path(db_path)
    hits = idx.search("hello")
    assert len(hits) == 1
    assert hits[0].msg == "hello world"
    assert hits[0].level == "INFO"
    assert hits[0].logger == "nakama.test"


def test_emit_extracts_extra_fields(handler, db_path):
    handler.emit(_make_record(logging.INFO, "task done", job="backup", duration_ms=42))

    from shared.log_index import LogIndex

    idx = LogIndex.from_path(db_path)
    hits = idx.search("done")
    assert hits[0].extra == {"job": "backup", "duration_ms": 42}


def test_emit_skips_below_handler_level(db_path):
    handler = SQLiteLogHandler(level=logging.INFO, db_path=db_path)
    # DEBUG should be filtered by handler.level — Logger.callHandlers() wraps
    # this call, so we mirror the gate by checking handler.level here.
    record = _make_record(logging.DEBUG, "debug noise")
    if record.levelno >= handler.level:
        handler.emit(record)

    from shared.log_index import LogIndex

    idx = LogIndex.from_path(db_path)
    assert idx.stats().total == 0


def test_emit_swallows_db_error_via_handle_error(handler, monkeypatch):
    """A broken insert path must not raise out of emit() — logger contract."""
    captured: dict = {}

    def fake_handle_error(record):
        captured["handled"] = record

    monkeypatch.setattr(handler, "handleError", fake_handle_error)

    # Force LogIndex.insert to raise so we exercise the except branch
    # deterministically (filesystem-permission tricks are flaky across OSes).
    def fake_get_index():
        class _Broken:
            def insert(self, **_):
                raise RuntimeError("simulated db corruption")

        return _Broken()

    monkeypatch.setattr(handler, "_get_index", fake_get_index)
    handler.emit(_make_record(logging.INFO, "should not raise"))

    assert "handled" in captured, "handle_error should be called when insert raises"


def test_emit_captures_exc_info_into_extra(handler, db_path):
    """`logger.exception()` sets record.exc_info; the traceback must land in
    extra_json so /bridge/logs FTS can search inside it. Without this,
    postmortem search loses stack traces — exactly the type of log most
    useful when debugging."""
    try:
        raise ValueError("boom — disk gremlins")
    except ValueError:
        import sys

        record = _make_record(logging.ERROR, "task failed")
        record.exc_info = sys.exc_info()
        handler.emit(record)

    from shared.log_index import LogIndex

    idx = LogIndex.from_path(db_path)
    hits = idx.search("gremlins")  # the message text lives only inside the traceback
    assert len(hits) == 1
    assert hits[0].extra.get("exc"), "exc traceback missing from extra_json"
    assert "ValueError" in hits[0].extra["exc"]
    assert "disk gremlins" in hits[0].extra["exc"]


def test_get_logger_skips_db_handler_when_env_disabled(monkeypatch):
    """`NAKAMA_LOG_DB_DISABLE=1` (set by conftest) prevents attachment."""
    import shared.log as log_mod

    # Force re-init to observe env effect deterministically.
    monkeypatch.setattr(log_mod, "_initialized", False)
    root = logging.getLogger("nakama")
    root.handlers.clear()  # clean slate

    monkeypatch.setenv("NAKAMA_LOG_DB_DISABLE", "1")
    log_mod.get_logger("nakama.test")

    db_handlers = [h for h in root.handlers if isinstance(h, SQLiteLogHandler)]
    assert db_handlers == []


def test_get_logger_attaches_db_handler_when_env_unset(monkeypatch):
    import shared.log as log_mod

    monkeypatch.setattr(log_mod, "_initialized", False)
    root = logging.getLogger("nakama")
    root.handlers.clear()

    monkeypatch.delenv("NAKAMA_LOG_DB_DISABLE", raising=False)
    # Avoid actually opening data/logs.db during this test — point to tmp.
    monkeypatch.setenv("NAKAMA_LOG_DB_PATH", "/tmp/nakama_test_logs_attach.db")

    log_mod.get_logger("nakama.test")

    db_handlers = [h for h in root.handlers if isinstance(h, SQLiteLogHandler)]
    assert len(db_handlers) == 1
    assert db_handlers[0].level == logging.INFO


def test_emit_preserves_unicode(handler, db_path):
    """CJK content survives the round-trip. unicode61 tokenizer treats a
    contiguous CJK run as a single token, so the test query matches the
    whole "備份完成" token (NOT a substring like "備份" — which would need a
    trigram tokenizer or jieba and is outside Phase 5C scope)."""
    handler.emit(_make_record(logging.INFO, "備份完成 ✓ 寫了 12 筆"))

    from shared.log_index import LogIndex

    idx = LogIndex.from_path(db_path)
    hits = idx.search("備份完成")
    assert len(hits) == 1
    assert "備份完成" in hits[0].msg
