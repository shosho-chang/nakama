"""Shared test fixtures."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Module-level: disable the SQLite log sink BEFORE any test module imports
# `shared.log` and triggers handler setup. Tests that exercise the handler
# itself (`test_log_handler.py`) explicitly opt out via monkeypatch.
os.environ.setdefault("NAKAMA_LOG_DB_DISABLE", "1")


@pytest.fixture(autouse=True)
def _prevent_real_memory_extraction(request, monkeypatch):
    """Stop background Haiku calls during tests.

    The gateway handler triggers ``extract_in_background`` on ``end_turn``.
    Without this fixture, any test that reaches end_turn would spawn a daemon
    thread making real API calls (costing money + flaky).

    Tests that specifically exercise the real extractor should mark themselves
    with ``@pytest.mark.real_extractor``.
    """
    if request.node.get_closest_marker("real_extractor"):
        return
    monkeypatch.setattr(
        "shared.memory_extractor.extract_in_background",
        MagicMock(return_value=MagicMock(is_alive=lambda: False)),
    )
    try:
        import gateway.handlers.nami as nami

        monkeypatch.setattr(nami, "extract_in_background", MagicMock())
    except ImportError:
        pass


@pytest.fixture(autouse=True)
def _prevent_real_slack_alerts(request, monkeypatch):
    """Stop ``shared.alerts.alert("error", ...)`` from DMing real Slack.

    ``_send_slack`` lazy-imports ``FrankySlackBot.from_env``. On dev machines
    where ``SLACK_FRANKY_BOT_TOKEN`` + ``SLACK_USER_ID_SHOSHO`` are set, any
    failure-path test that exercises a backup/cron script silently posts to
    the user's production DM (e.g. pytest tmp_path leaking via "data dir
    missing: ...pytest-of-Shosho/...").

    Default: redirect ``from_env`` to the no-op stub. Tests that want to
    assert post arguments stack their own patch (see
    ``tests/shared/test_alerts.py::fake_slack``).

    Tests that need real Slack delivery should mark themselves with
    ``@pytest.mark.real_slack``.
    """
    if request.node.get_closest_marker("real_slack"):
        return
    from agents.franky.slack_bot import _NoopSlackStub

    monkeypatch.setattr(
        "agents.franky.slack_bot.FrankySlackBot.from_env",
        MagicMock(return_value=_NoopSlackStub()),
    )


@pytest.fixture(autouse=True)
def _prevent_real_google_calendar(request, monkeypatch):
    """Stop tests from touching the REAL Google Calendar.

    All calendar CRUD (create/update/delete/list_events) goes through the one
    chokepoint ``shared.google_calendar._get_service``. On a box WITHOUT a token
    that raised ``GoogleCalendarAuthError`` → ``schedule_entry`` returned
    UNAVAILABLE, so unmocked tests were side-effect-free *by accident*. The
    moment a real token lands in ``data/`` (e.g. copied in to run the dev
    server) those same unmocked tests start writing real events — observed:
    all-day "測試任務" events appearing on the user's calendar on the test
    dates. Patch the service chokepoint to reproduce the safe no-token
    behaviour regardless of whether a token file exists.

    Tests that mock ``create_event``/``list_events`` directly are unaffected
    (their patch is applied after this and wins). Tests that genuinely need the
    live API mark themselves ``@pytest.mark.real_calendar``.
    """
    if request.node.get_closest_marker("real_calendar"):
        return
    from shared.google_calendar import GoogleCalendarAuthError

    def _no_service(*_a, **_k):
        raise GoogleCalendarAuthError("calendar disabled in tests (no real_calendar marker)")

    monkeypatch.setattr("shared.google_calendar._get_service", _no_service)


@pytest.fixture(autouse=True)
def _isolated_incidents_pending(tmp_path: Path, monkeypatch):
    """Route shared.incident_archive default dir to tmp.

    Without this, any test that fires `shared.alerts.alert("error", ...)` or
    `agents.franky.alert_router.dispatch(critical_alert)` writes a real .md
    into the repo's `data/incidents-pending/` and pollutes git status.
    """
    monkeypatch.setenv("NAKAMA_INCIDENTS_PENDING_DIR", str(tmp_path / "_incidents-pending"))


@pytest.fixture(autouse=True)
def isolated_db(tmp_path: Path, monkeypatch):
    """Route shared.state to a temporary SQLite DB per test.

    Autouse so every test hits a tmp DB — real config points to
    /home/nakama/data/state.db which doesn't exist on CI runners.
    """
    db_path = tmp_path / "test.db"
    import shared.state as state

    monkeypatch.setattr(state, "get_db_path", lambda: db_path)

    if state._conn is not None:
        try:
            state._conn.close()
        except sqlite3.Error:
            pass
    state._conn = None

    from shared import agent_memory, episodic_memory, pushed_topics

    agent_memory._SCHEMA_INITIALIZED = False
    episodic_memory._SCHEMA_INITIALIZED = False
    pushed_topics._SCHEMA_INITIALIZED = False

    yield db_path

    if state._conn is not None:
        try:
            state._conn.close()
        except sqlite3.Error:
            pass
        state._conn = None
