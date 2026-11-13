"""Franky cron dispatcher — weekly synthesis vs monthly retrospective (ADR-023 §1).

每週日 22:00 台北 cron 呼叫 dispatch()：
  最後一個週日 → 跑 retrospective（agents/franky/news_retrospective）
  其他週日     → 跑 synthesis（agents/franky/news_synthesis）

Cross-year edge case handled: December last Sunday correctly triggers
retrospective for December; the following January Sundays run synthesis.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from agents.franky.news_retrospective import is_last_sunday_of_month

_TAIPEI = ZoneInfo("Asia/Taipei")


def should_run_retrospective(dt: datetime) -> bool:
    """Return True if `dt` is the last Sunday of its month (run retrospective).

    Delegates to news_retrospective.is_last_sunday_of_month.
    """
    return is_last_sunday_of_month(dt)


def dispatch(
    now: datetime | None = None,
    *,
    dry_run: bool = False,
    no_publish: bool = False,
    slack_bot: Any | None = None,
) -> str:
    """Run retrospective or synthesis depending on the date.

    Returns the summary string from whichever pipeline runs.
    """
    if now is None:
        now = datetime.now(tz=_TAIPEI)

    if should_run_retrospective(now):
        from agents.franky.news_retrospective import NewsRetrospectivePipeline

        pipeline = NewsRetrospectivePipeline(
            dry_run=dry_run,
            no_publish=no_publish,
            slack_bot=slack_bot,
            now=now,
        )
        if dry_run:
            return pipeline.run()
        pipeline.execute()
        return f"retrospective op={pipeline.operation_id}"
    else:
        from agents.franky.news_synthesis import NewsSynthesisPipeline

        pipeline = NewsSynthesisPipeline(
            dry_run=dry_run,
            no_publish=no_publish,
            slack_bot=slack_bot,
        )
        if dry_run:
            return pipeline.run()
        pipeline.execute()
        return f"synthesis op={pipeline.operation_id}"
