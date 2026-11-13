"""Tests for is_last_sunday_of_month helper (ADR-023 §7 S4).

Verified calendar facts used as fixtures:
  2026-05-31  = Sunday, last of May 2026       → True
  2026-05-24  = Sunday, second-to-last of May  → False
  2026-05-30  = Saturday                       → False
  2026-12-27  = Sunday, last of December 2026  → True  (cross-year edge case)
  2026-12-20  = Sunday, not last of December   → False
  2026-01-25  = Sunday, last of January 2026   → True
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from agents.franky.news_retrospective import is_last_sunday_of_month

_TAIPEI = ZoneInfo("Asia/Taipei")


def _dt(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, 22, 0, 0, tzinfo=_TAIPEI)


def test_last_sunday_of_may_2026():
    """May 31, 2026 is a Sunday and is the last Sunday of May."""
    assert is_last_sunday_of_month(_dt(2026, 5, 31)) is True


def test_second_to_last_sunday_of_may_2026():
    """May 24, 2026 is a Sunday but NOT the last Sunday of May."""
    assert is_last_sunday_of_month(_dt(2026, 5, 24)) is False


def test_saturday_is_not_sunday():
    """May 30, 2026 is a Saturday → always False."""
    assert is_last_sunday_of_month(_dt(2026, 5, 30)) is False


def test_last_sunday_of_december_2026_cross_year():
    """Dec 27, 2026 is the last Sunday of December — cross-year edge case.

    Adding 7 days gives Jan 3, 2027 (different month) → True.
    This confirms the cron correctly triggers December retrospective,
    while Jan Sundays run weekly synthesis.
    """
    assert is_last_sunday_of_month(_dt(2026, 12, 27)) is True


def test_not_last_sunday_of_december_2026():
    """Dec 20, 2026 is a Sunday but NOT the last Sunday of December."""
    assert is_last_sunday_of_month(_dt(2026, 12, 20)) is False


def test_last_sunday_of_january_2026():
    """Jan 25, 2026 is the last Sunday of January."""
    assert is_last_sunday_of_month(_dt(2026, 1, 25)) is True


def test_cron_dispatcher_delegates_correctly():
    """cron_dispatcher.should_run_retrospective delegates to is_last_sunday_of_month."""
    from agents.franky.cron_dispatcher import should_run_retrospective

    assert should_run_retrospective(_dt(2026, 5, 31)) is True
    assert should_run_retrospective(_dt(2026, 5, 24)) is False
    assert should_run_retrospective(_dt(2026, 12, 27)) is True
