"""agents/franky/__main__.py — CLI heartbeat instrumentation 測試（Phase 5B-2）。

驗證 backup-verify / digest / news 三個 cron entry 都會：
- 成功路徑 record_success（即使 backup-verify 報告 stale，script 自身仍是成功）
- 失敗路徑 record_failure 並 re-raise
- news --dry-run 跳過 heartbeat（避免污染 cron staleness 訊號）

不測 health（self-deadlock 不能監測自己）和 alert（manual self-test）。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agents.franky.__main__ import main

# ---------------------------------------------------------------------------
# franky digest（weekly report）
# ---------------------------------------------------------------------------


def test_digest_records_heartbeat_success():
    from shared import heartbeat

    with patch(
        "agents.franky.weekly_digest.send_digest",
        return_value={"operation_id": "op_x", "slack_ts": "1.2", "text_preview": "..."},
    ):
        rc = main(["digest"])

    assert rc == 0
    hb = heartbeat.get_heartbeat("franky-weekly-report")
    assert hb is not None
    assert hb.last_status == "success"
    assert hb.consecutive_failures == 0


def test_digest_records_heartbeat_failure_on_exception():
    from shared import heartbeat

    with patch("agents.franky.weekly_digest.send_digest", side_effect=RuntimeError("slack 5xx")):
        with pytest.raises(RuntimeError):
            main(["digest"])

    hb = heartbeat.get_heartbeat("franky-weekly-report")
    assert hb is not None
    assert hb.last_status == "fail"
    assert "slack 5xx" in (hb.last_error or "")


# ---------------------------------------------------------------------------
# franky backup-verify
# ---------------------------------------------------------------------------


def test_backup_verify_records_success_when_status_ok():
    from shared import heartbeat

    with patch(
        "agents.franky.r2_backup_verify.verify_once",
        return_value={
            "operation_id": "op_y",
            "status": "ok",
            "detail": "fresh",
            "alert": None,
        },
    ):
        rc = main(["backup-verify"])

    assert rc == 0
    hb = heartbeat.get_heartbeat("franky-r2-backup-verify")
    assert hb is not None
    assert hb.last_status == "success"


def test_backup_verify_records_success_even_when_alert_emitted():
    """Script *itself* succeeded — it correctly surfaced a stale-backup alert.

    The cron-staleness probe watches the SCRIPT's liveness. The backup-content
    health is a separate concern handled by probe_r2_backup_nakama. Treating
    alert-emission as cron-failure would make the operator chase the wrong
    issue (cron is fine; the BACKED-UP DATA is stale).
    """
    from shared import heartbeat
    from shared.schemas.franky import AlertV1

    fake_alert = AlertV1(
        rule_id="r2_backup_missing",
        severity="critical",
        title="t",
        message="m",
        fired_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        dedup_key="r2_backup_missing",
        operation_id="op_abcdef12",
    )
    with (
        patch(
            "agents.franky.r2_backup_verify.verify_once",
            return_value={
                "operation_id": "op_z",
                "status": "stale",
                "detail": "...",
                "alert": fake_alert,
            },
        ),
        patch("agents.franky.alert_router.make_default_sink", return_value=MagicMock()),
    ):
        rc = main(["backup-verify"])

    # Exit code 1 is the cron-noise signal (alert was emitted) — but heartbeat is success.
    assert rc == 1
    hb = heartbeat.get_heartbeat("franky-r2-backup-verify")
    assert hb is not None
    assert hb.last_status == "success"


def test_backup_verify_records_failure_on_uncaught_exception():
    from shared import heartbeat

    with patch(
        "agents.franky.r2_backup_verify.verify_once",
        side_effect=RuntimeError("boto crash"),
    ):
        with pytest.raises(RuntimeError):
            main(["backup-verify"])

    hb = heartbeat.get_heartbeat("franky-r2-backup-verify")
    assert hb is not None
    assert hb.last_status == "fail"
    assert "boto crash" in (hb.last_error or "")


# ---------------------------------------------------------------------------
# franky news
# ---------------------------------------------------------------------------


def test_news_records_heartbeat_success():
    from shared import heartbeat

    with patch("agents.franky.news_digest.run_news_digest", return_value="op=op_n"):
        rc = main(["news"])

    assert rc == 0
    hb = heartbeat.get_heartbeat("franky-news-digest")
    assert hb is not None
    assert hb.last_status == "success"


def test_news_records_heartbeat_failure_on_exception():
    from shared import heartbeat

    with patch(
        "agents.franky.news_digest.run_news_digest",
        side_effect=RuntimeError("rss feed timeout"),
    ):
        with pytest.raises(RuntimeError):
            main(["news"])

    hb = heartbeat.get_heartbeat("franky-news-digest")
    assert hb is not None
    assert hb.last_status == "fail"
    assert "rss feed timeout" in (hb.last_error or "")


def test_news_dry_run_does_not_record_heartbeat():
    from shared import heartbeat

    with patch("agents.franky.news_digest.run_news_digest", return_value="op=op_dry"):
        main(["news", "--dry-run"])

    assert heartbeat.get_heartbeat("franky-news-digest") is None


def test_news_dry_run_failure_does_not_record_heartbeat():
    from shared import heartbeat

    with patch(
        "agents.franky.news_digest.run_news_digest",
        side_effect=RuntimeError("boom"),
    ):
        with pytest.raises(RuntimeError):
            main(["news", "--dry-run"])

    assert heartbeat.get_heartbeat("franky-news-digest") is None


def test_news_no_publish_still_records_heartbeat():
    """--no-publish writes vault but skips Slack; that's still real work, so heartbeat fires."""
    from shared import heartbeat

    with patch("agents.franky.news_digest.run_news_digest", return_value="op=op_np"):
        main(["news", "--no-publish"])

    hb = heartbeat.get_heartbeat("franky-news-digest")
    assert hb is not None
    assert hb.last_status == "success"


# ---------------------------------------------------------------------------
# franky gsc-daily（ADR-008 Phase 2a-min）
# ---------------------------------------------------------------------------


def test_gsc_daily_records_heartbeat_success_on_ok():
    from agents.franky.jobs.gsc_daily import GscDailyResult
    from shared import heartbeat

    result = GscDailyResult(
        operation_id="op_gsc_ok",
        status="ok",
        detail="window=[2026-04-20,2026-04-26] processed=3 rows=18",
        keywords_total=3,
        keywords_processed=3,
        rows_written=18,
    )
    with patch("agents.franky.jobs.gsc_daily.run_once", return_value=result):
        rc = main(["gsc-daily"])

    assert rc == 0
    hb = heartbeat.get_heartbeat("franky-gsc-daily")
    assert hb is not None
    assert hb.last_status == "success"
    assert hb.consecutive_failures == 0


def test_gsc_daily_records_heartbeat_success_on_skipped():
    """Skipped (yaml empty / env missing) is a config gap, not a cron-stuck signal."""
    from agents.franky.jobs.gsc_daily import GscDailyResult
    from shared import heartbeat

    result = GscDailyResult(
        operation_id="op_gsc_skip",
        status="skipped",
        detail="keywords_yaml_empty",
    )
    with patch("agents.franky.jobs.gsc_daily.run_once", return_value=result):
        rc = main(["gsc-daily"])

    assert rc == 0
    hb = heartbeat.get_heartbeat("franky-gsc-daily")
    assert hb is not None
    assert hb.last_status == "success"


def test_gsc_daily_records_heartbeat_failure_on_status_fail():
    """status='fail' (all keywords failed) → heartbeat fail, exit 1."""
    from agents.franky.jobs.gsc_daily import GscDailyResult
    from shared import heartbeat

    result = GscDailyResult(
        operation_id="op_gsc_fail",
        status="fail",
        detail="all_keywords_failed count=5",
        keywords_total=5,
        keywords_failed=5,
    )
    with patch("agents.franky.jobs.gsc_daily.run_once", return_value=result):
        rc = main(["gsc-daily"])

    assert rc == 1
    hb = heartbeat.get_heartbeat("franky-gsc-daily")
    assert hb is not None
    assert hb.last_status == "fail"
    assert "all_keywords_failed" in (hb.last_error or "")


def test_gsc_daily_records_heartbeat_failure_on_uncaught_exception():
    from shared import heartbeat

    with patch("agents.franky.jobs.gsc_daily.run_once", side_effect=RuntimeError("api 5xx")):
        with pytest.raises(RuntimeError):
            main(["gsc-daily"])

    hb = heartbeat.get_heartbeat("franky-gsc-daily")
    assert hb is not None
    assert hb.last_status == "fail"
    assert "api 5xx" in (hb.last_error or "")


def test_gsc_daily_dry_run_does_not_record_heartbeat():
    """Dry-run is ad-hoc; heartbeat would corrupt cron-staleness signal."""
    from agents.franky.jobs.gsc_daily import GscDailyResult
    from shared import heartbeat

    result = GscDailyResult(
        operation_id="op_gsc_dry",
        status="ok",
        detail="dry_run window=[2026-04-20,2026-04-26]",
    )
    with patch("agents.franky.jobs.gsc_daily.run_once", return_value=result):
        main(["gsc-daily", "--dry-run"])

    assert heartbeat.get_heartbeat("franky-gsc-daily") is None
