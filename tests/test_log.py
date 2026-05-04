"""Tests for shared.log.force_utf8_console + Chinese log message survival.

Regression for the Windows cp1252 stack-trace flood: when uvicorn /
thousand_sunny boots on Windows, sys.stdout defaults to cp1252; any
Chinese log message via ``logging.StreamHandler`` raises
``UnicodeEncodeError: 'charmap' codec can't encode characters`` per
record, then logging falls back to printing a stack trace to stderr and
silently drops the message.

These tests build cp1252 stream fakes and pass them to
``force_utf8_console`` directly (instead of monkeypatching
``sys.stdout``) — pytest's capture replaces ``sys.stdout`` per test,
which would mask the reconfigure effect.
"""

from __future__ import annotations

import io
import logging

import pytest


def _make_cp1252_stream() -> io.TextIOWrapper:
    """Build a TextIOWrapper that mirrors Windows default sys.stdout encoding."""
    return io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict", write_through=True)


def test_force_utf8_console_flips_cp1252_to_utf8():
    """The helper must change a cp1252 stream's encoding to UTF-8."""
    from shared.log import force_utf8_console

    fake_stdout = _make_cp1252_stream()
    fake_stderr = _make_cp1252_stream()
    assert fake_stdout.encoding.lower() == "cp1252"
    assert fake_stderr.encoding.lower() == "cp1252"

    force_utf8_console(streams=(fake_stdout, fake_stderr))

    assert fake_stdout.encoding.lower() == "utf-8"
    assert fake_stderr.encoding.lower() == "utf-8"


def test_force_utf8_console_is_idempotent():
    """Calling twice must not raise (e.g. if both an entry point and
    get_logger() invoke it, behavior must remain stable)."""
    from shared.log import force_utf8_console

    fake = _make_cp1252_stream()
    force_utf8_console(streams=(fake,))
    force_utf8_console(streams=(fake,))  # already utf-8 — no-op
    assert fake.encoding.lower() == "utf-8"


def test_force_utf8_console_skips_streams_without_reconfigure():
    """Wrapped sinks that lack reconfigure() must not break the helper.

    StringIO is the canonical example — pytest's capsys + various test
    rigs install streams without TextIOWrapper.reconfigure. The helper
    must degrade gracefully, not raise AttributeError.
    """
    from shared.log import force_utf8_console

    force_utf8_console(streams=(io.StringIO(), io.StringIO()))  # must not raise


def test_force_utf8_console_swallows_reconfigure_errors():
    """Some IDE-wrapped streams expose reconfigure() but raise when called.

    The helper's per-stream try/except guarantees logging setup never
    aborts startup over a flaky stream wrapper.
    """
    from shared.log import force_utf8_console

    class _FlakyStream:
        encoding = "cp1252"

        def reconfigure(self, **_kwargs):
            raise OSError("simulated wrapped-stream failure")

    force_utf8_console(streams=(_FlakyStream(),))  # must not raise


def test_cp1252_stream_raises_on_chinese_without_fix():
    """Demonstrates the bug: writing Chinese to a cp1252 TextIOWrapper
    raises UnicodeEncodeError. This is what logging.StreamHandler.emit
    hits on Windows uvicorn before the fix.

    Acts as a baseline alongside the 'after fix' test below — together
    they form the red-then-green regression contract.
    """
    fake = _make_cp1252_stream()
    with pytest.raises(UnicodeEncodeError):
        fake.write("本地方式均失敗，改用 Firecrawl\n")
        fake.flush()


def test_chinese_log_message_survives_after_force_utf8():
    """End-to-end regression: a logger built on top of a cp1252 stream
    must NOT raise UnicodeEncodeError after force_utf8_console flips
    the encoding. The original Chinese text must round-trip through
    the underlying byte buffer as UTF-8.

    Mirrors the smoke-session repro: Robin emits "本地方式均失敗，改用
    Firecrawl：…" and the bug currently spams a stack trace per record
    while dropping the message.
    """
    from shared.log import force_utf8_console

    # Build a fresh nakama-namespaced logger with a StreamHandler bound to
    # a cp1252-default fake stdout — exactly what get_logger does in
    # production, minus the global module-level state.
    fake_stdout = _make_cp1252_stream()
    backing_buf: io.BytesIO = fake_stdout.buffer  # type: ignore[assignment]

    logger = logging.getLogger("nakama.regression.utf8_survive")
    logger.handlers.clear()
    logger.propagate = False
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler(fake_stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)

    # Apply the fix.
    force_utf8_console(streams=(fake_stdout,))

    chinese_msg = "本地方式均失敗，改用 Firecrawl：reader.example.com"
    logger.info(chinese_msg)
    handler.flush()
    fake_stdout.flush()

    emitted_bytes = backing_buf.getvalue()
    assert emitted_bytes, "logger.info produced zero output bytes"
    decoded = emitted_bytes.decode("utf-8")
    assert chinese_msg in decoded, f"Chinese message lost in transit. Got: {decoded!r}"


def test_chinese_log_message_fails_without_force_utf8(caplog):
    """Without the fix, the same Chinese-message emission triggers
    UnicodeEncodeError inside StreamHandler.emit. Python's logging
    framework swallows it via Handler.handleError (which prints to
    stderr) — the message itself NEVER lands in the byte buffer.

    The empty buffer assertion is the load-bearing part: it proves
    the bug exists when force_utf8_console is NOT applied.
    """
    fake_stdout = _make_cp1252_stream()
    backing_buf: io.BytesIO = fake_stdout.buffer  # type: ignore[assignment]

    logger = logging.getLogger("nakama.regression.utf8_no_fix")
    logger.handlers.clear()
    logger.propagate = False
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler(fake_stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    # Suppress the noisy "--- Logging error ---" output during the test —
    # we expect handleError to fire.
    handler.handleError = lambda record: None  # type: ignore[method-assign]
    logger.addHandler(handler)

    logger.info("本地方式均失敗，改用 Firecrawl：reader.example.com")

    # The message did NOT make it through — encode failed and was swallowed
    # by handleError. Buffer stays empty (cp1252 + Chinese = UnicodeEncodeError).
    assert backing_buf.getvalue() == b"", (
        "Bug regression: cp1252 stream should drop Chinese messages, "
        f"but buffer contains {backing_buf.getvalue()!r}"
    )
