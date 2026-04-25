"""Tests for shared/log.py JSONFormatter."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import pytest

from shared.log import JSONFormatter


def _make_record(
    *,
    name: str = "nakama.test",
    level: int = logging.INFO,
    msg: str = "hello",
    extra: dict | None = None,
) -> logging.LogRecord:
    """Build a LogRecord with optional extra fields, mimicking logger.info(..., extra=)."""
    record = logging.LogRecord(
        name=name,
        level=level,
        pathname="x.py",
        lineno=1,
        msg=msg,
        args=(),
        exc_info=None,
    )
    if extra:
        for k, v in extra.items():
            setattr(record, k, v)
    return record


def test_json_formatter_emits_required_fields():
    record = _make_record(level=logging.INFO, msg="backup ok")
    out = json.loads(JSONFormatter().format(record))

    assert out["msg"] == "backup ok"
    assert out["level"] == "INFO"
    assert out["logger"] == "nakama.test"
    assert "ts" in out
    # Parseable as ISO8601 UTC
    parsed = datetime.fromisoformat(out["ts"])
    assert parsed.tzinfo is timezone.utc


def test_json_formatter_includes_extra_fields():
    record = _make_record(
        msg="upload",
        extra={"job": "nakama-backup", "tier": "daily", "size": 12345},
    )
    out = json.loads(JSONFormatter().format(record))

    assert out["job"] == "nakama-backup"
    assert out["tier"] == "daily"
    assert out["size"] == 12345


def test_json_formatter_omits_internal_fields():
    record = _make_record(extra={"_private": "should-not-appear"})
    out = json.loads(JSONFormatter().format(record))

    assert "_private" not in out
    # Standard LogRecord fields also stay out of the user-extra surface.
    assert "args" not in out
    assert "pathname" not in out
    assert "filename" not in out


def test_json_formatter_includes_exception():
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        record = logging.LogRecord(
            name="nakama.test",
            level=logging.ERROR,
            pathname="x.py",
            lineno=1,
            msg="caught",
            args=(),
            exc_info=sys.exc_info(),
        )
    out = json.loads(JSONFormatter().format(record))

    assert "exc" in out
    assert "ValueError" in out["exc"]
    assert "boom" in out["exc"]


def test_json_formatter_levels_round_trip():
    for level, name in [
        (logging.DEBUG, "DEBUG"),
        (logging.INFO, "INFO"),
        (logging.WARNING, "WARNING"),
        (logging.ERROR, "ERROR"),
        (logging.CRITICAL, "CRITICAL"),
    ]:
        record = _make_record(level=level)
        out = json.loads(JSONFormatter().format(record))
        assert out["level"] == name


def test_json_formatter_handles_non_string_extra(caplog):
    """`default=str` ensures datetime/Path/etc. never crash json.dumps."""
    from pathlib import Path

    record = _make_record(extra={"path": Path("/tmp/state.db"), "when": datetime.now(timezone.utc)})
    out_text = JSONFormatter().format(record)

    parsed = json.loads(out_text)
    assert parsed["path"] == "/tmp/state.db"
    assert isinstance(parsed["when"], str)


def test_get_logger_uses_text_format_by_default(monkeypatch, capsys):
    """Default `NAKAMA_LOG_FORMAT` (unset) → human-readable text format."""
    import shared.log as log_mod

    monkeypatch.delenv("NAKAMA_LOG_FORMAT", raising=False)
    monkeypatch.setattr(log_mod, "_initialized", False)
    # Strip prior handlers so we re-init cleanly
    root = logging.getLogger("nakama")
    root.handlers.clear()

    logger = log_mod.get_logger("nakama.test_text")
    logger.info("text-format")

    captured = capsys.readouterr().out
    assert "text-format" in captured
    # Text format includes the dash separator unique to the human format
    assert "—" in captured


def test_get_logger_uses_json_format_when_env_set(monkeypatch, capsys):
    import shared.log as log_mod

    monkeypatch.setenv("NAKAMA_LOG_FORMAT", "json")
    monkeypatch.setattr(log_mod, "_initialized", False)
    root = logging.getLogger("nakama")
    root.handlers.clear()

    logger = log_mod.get_logger("nakama.test_json")
    logger.info("json-format", extra={"job": "tx"})

    line = capsys.readouterr().out.strip()
    parsed = json.loads(line)
    assert parsed["msg"] == "json-format"
    assert parsed["job"] == "tx"


@pytest.fixture(autouse=True)
def _reset_logger_handlers():
    """Each test in this file may toggle the JSON env; reset handlers after."""
    yield
    root = logging.getLogger("nakama")
    root.handlers.clear()
    import shared.log as log_mod

    log_mod._initialized = False
