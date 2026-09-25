"""Tests for agents/franky/sdk_freshness.py (ADR-070 D10 / S7b).

Coverage (task prompt §驗收):
- version parsing: the 4 documented examples + a date segment must not count
- weekly gate: not-due skip, due after Sunday 09:00 Taipei boundary, first-ever run
- check 1 (SDK deploy lag): triggers when main > installed, no alert when equal,
  git fetch failure -> no alert / no raise
- check 2 (model freshness): triggers per family when OpenRouter is ahead, skips a
  family with no 7-day data, OpenRouter fetch failure -> no alert / no raise
- dedupe_key content for both alerts

No real network / subprocess: httpx.get and subprocess.run are mocked.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import httpx
import pytest

from agents.franky import sdk_freshness
from agents.franky.sdk_freshness import (
    _LOCAL_MODEL_RE,
    _OPENROUTER_ID_RE,
    _actual_model_versions,
    _last_sunday_0900_boundary,
    _latest_per_family,
    _should_run_weekly,
    _version_tuple,
    check_model_freshness,
    check_sdk_deploy_lag,
)
from shared.state import _get_conn, record_api_call

_TAIPEI = ZoneInfo("Asia/Taipei")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _insert_api_call(
    model_actual: str, *, called_at: datetime, lane_actual: str = "subscription"
) -> None:
    record_api_call(
        "nami", model_actual, 100, 50, lane_actual=lane_actual, model_actual=model_actual
    )
    conn = _get_conn()
    conn.execute(
        "UPDATE api_calls SET called_at = ? WHERE model_actual = ? "
        "AND id = (SELECT MAX(id) FROM api_calls)",
        (called_at.isoformat(), model_actual),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Version parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "suffix,expected",
    [
        ("5.5", (5, 5)),
        ("5", (5, 0)),
        ("4.5", (4, 5)),
        ("4-5-20251001", (4, 5)),  # date segment must not count
    ],
)
def test_version_tuple_examples(suffix, expected):
    assert _version_tuple(suffix) == expected


def test_latest_per_family_picks_highest_version():
    ids = [
        "anthropic/claude-opus-5",
        "anthropic/claude-opus-5.5",
        "anthropic/claude-sonnet-5",
        "anthropic/claude-haiku-4.5",
        "openai/gpt-4o",  # doesn't match, ignored
    ]
    result = _latest_per_family(ids, _OPENROUTER_ID_RE)
    assert result["opus"] == ((5, 5), "anthropic/claude-opus-5.5")
    assert result["sonnet"] == ((5, 0), "anthropic/claude-sonnet-5")
    assert result["haiku"] == ((4, 5), "anthropic/claude-haiku-4.5")


def test_latest_per_family_local_model_ids():
    ids = ["claude-opus-5", "claude-haiku-4-5-20251001"]
    result = _latest_per_family(ids, _LOCAL_MODEL_RE)
    assert result["opus"] == ((5, 0), "claude-opus-5")
    assert result["haiku"] == ((4, 5), "claude-haiku-4-5-20251001")


# ---------------------------------------------------------------------------
# Weekly gate
# ---------------------------------------------------------------------------


def test_boundary_sunday_after_0900_is_same_day():
    now = datetime(2026, 9, 27, 10, 0, tzinfo=_TAIPEI)  # 2026-09-27 is a Sunday
    boundary = _last_sunday_0900_boundary(now)
    assert boundary == datetime(2026, 9, 27, 9, 0, tzinfo=_TAIPEI)


def test_boundary_sunday_before_0900_is_previous_week():
    now = datetime(2026, 9, 27, 3, 0, tzinfo=_TAIPEI)
    boundary = _last_sunday_0900_boundary(now)
    assert boundary == datetime(2026, 9, 20, 9, 0, tzinfo=_TAIPEI)


def test_boundary_midweek_is_last_sunday():
    now = datetime(2026, 9, 30, 12, 0, tzinfo=_TAIPEI)  # Wednesday
    boundary = _last_sunday_0900_boundary(now)
    assert boundary == datetime(2026, 9, 27, 9, 0, tzinfo=_TAIPEI)


def test_should_run_weekly_never_run_before():
    assert _should_run_weekly(None, _now()) is True


def test_should_run_weekly_already_ran_this_window():
    now = datetime(2026, 9, 30, 12, 0, tzinfo=_TAIPEI)  # Wednesday, window opened Sun 09:00
    last_check_at = datetime(2026, 9, 27, 9, 30, tzinfo=_TAIPEI)  # ran just after boundary
    assert _should_run_weekly(last_check_at, now.astimezone(timezone.utc)) is False


def test_should_run_weekly_due_new_window():
    now = datetime(2026, 9, 27, 10, 0, tzinfo=_TAIPEI)  # this Sunday, after 09:00
    last_check_at = datetime(2026, 9, 20, 9, 30, tzinfo=_TAIPEI)  # ran last week's window
    assert _should_run_weekly(last_check_at, now.astimezone(timezone.utc)) is True


# ---------------------------------------------------------------------------
# Check 1 — SDK deploy lag
# ---------------------------------------------------------------------------


def _fake_run(returncode: int, stdout: str = "", stderr: str = ""):
    return MagicMock(returncode=returncode, stdout=stdout, stderr=stderr)


def test_sdk_deploy_lag_alerts_when_main_ahead():
    with (
        patch.object(sdk_freshness, "_installed_sdk_version", return_value="0.2.128"),
        patch(
            "subprocess.run",
            side_effect=[
                _fake_run(0),
                _fake_run(0, stdout="claude-agent-sdk==0.2.140\n"),
            ],
        ),
        patch.object(sdk_freshness, "alert") as mock_alert,
    ):
        probe = check_sdk_deploy_lag(now=_now())

    assert probe.status == "ok"
    mock_alert.assert_called_once()
    args, kwargs = mock_alert.call_args
    assert args[0] == "error"
    assert args[1] == "franky"
    assert "0.2.140" in args[2]
    assert "0.2.128" in args[2]
    assert kwargs["dedupe_key"] == "sdk-deploy-lag-0.2.140"


def test_sdk_deploy_lag_no_alert_when_installed_matches_main():
    with (
        patch.object(sdk_freshness, "_installed_sdk_version", return_value="0.2.140"),
        patch(
            "subprocess.run",
            side_effect=[
                _fake_run(0),
                _fake_run(0, stdout="claude-agent-sdk==0.2.140\n"),
            ],
        ),
        patch.object(sdk_freshness, "alert") as mock_alert,
    ):
        probe = check_sdk_deploy_lag(now=_now())

    assert probe.status == "ok"
    mock_alert.assert_not_called()


def test_sdk_deploy_lag_fetch_failure_no_alert_no_raise():
    with (
        patch.object(sdk_freshness, "_installed_sdk_version", return_value="0.2.128"),
        patch("subprocess.run", side_effect=[_fake_run(1, stderr="network unreachable")]),
        patch.object(sdk_freshness, "alert") as mock_alert,
    ):
        probe = check_sdk_deploy_lag(now=_now())

    assert probe.status == "fail"
    mock_alert.assert_not_called()


def test_sdk_deploy_lag_skips_when_not_due():
    now = _now()
    with (
        patch.object(sdk_freshness, "_should_run_weekly", return_value=False),
        patch("subprocess.run") as mock_run,
        patch.object(sdk_freshness, "alert") as mock_alert,
    ):
        probe = check_sdk_deploy_lag(now=now)

    mock_run.assert_not_called()
    mock_alert.assert_not_called()
    assert probe.detail.get("skipped") is True


# ---------------------------------------------------------------------------
# Check 2 — model freshness
# ---------------------------------------------------------------------------


def _openrouter_response(ids: list[str]) -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"data": [{"id": i} for i in ids]}
    return resp


def test_model_freshness_alerts_when_openrouter_ahead():
    now = _now()
    _insert_api_call("claude-opus-5", called_at=now - timedelta(days=1))

    with (
        patch("httpx.get", return_value=_openrouter_response(["anthropic/claude-opus-5.5"])),
        patch.object(sdk_freshness, "alert") as mock_alert,
    ):
        probe = check_model_freshness(now=now)

    assert probe.status == "ok"
    mock_alert.assert_called_once()
    args, kwargs = mock_alert.call_args
    assert args[0] == "error"
    assert "anthropic/claude-opus-5.5" in args[2]
    assert "claude-opus-5" in args[2]
    assert kwargs["dedupe_key"] == "model-behind-opus-5.5"


def test_model_freshness_no_alert_when_up_to_date():
    now = _now()
    _insert_api_call("claude-opus-5.5", called_at=now - timedelta(days=1))

    with (
        patch("httpx.get", return_value=_openrouter_response(["anthropic/claude-opus-5.5"])),
        patch.object(sdk_freshness, "alert") as mock_alert,
    ):
        probe = check_model_freshness(now=now)

    assert probe.status == "ok"
    mock_alert.assert_not_called()


def test_model_freshness_skips_family_without_7day_data():
    now = _now()
    _insert_api_call("claude-opus-5", called_at=now - timedelta(days=10))  # too old

    with (
        patch("httpx.get", return_value=_openrouter_response(["anthropic/claude-opus-5.5"])),
        patch.object(sdk_freshness, "alert") as mock_alert,
    ):
        probe = check_model_freshness(now=now)

    assert probe.status == "ok"
    mock_alert.assert_not_called()


def test_model_freshness_fetch_failure_no_alert_no_raise():
    now = _now()
    _insert_api_call("claude-opus-5", called_at=now - timedelta(days=1))

    with (
        patch("httpx.get", side_effect=httpx.ConnectError("boom")),
        patch.object(sdk_freshness, "alert") as mock_alert,
    ):
        probe = check_model_freshness(now=now)

    assert probe.status == "fail"
    mock_alert.assert_not_called()


def test_model_freshness_skips_when_not_due():
    now = _now()
    with (
        patch.object(sdk_freshness, "_should_run_weekly", return_value=False),
        patch("httpx.get") as mock_get,
        patch.object(sdk_freshness, "alert") as mock_alert,
    ):
        probe = check_model_freshness(now=now)

    mock_get.assert_not_called()
    mock_alert.assert_not_called()
    assert probe.detail.get("skipped") is True


def test_actual_model_versions_excludes_openrouter_lane():
    now = _now()
    _insert_api_call("claude-opus-5", called_at=now - timedelta(days=1), lane_actual="openrouter")
    result = _actual_model_versions(now)
    assert "opus" not in result
