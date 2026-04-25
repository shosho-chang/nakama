"""Tests for shared/heartbeat.py."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from shared import heartbeat


def test_record_success_creates_row():
    heartbeat.record_success("test-job")

    hb = heartbeat.get_heartbeat("test-job")
    assert hb is not None
    assert hb.job_name == "test-job"
    assert hb.last_status == "success"
    assert hb.last_error is None
    assert hb.consecutive_failures == 0
    assert hb.last_success_at is not None
    assert hb.last_run_at is not None


def test_record_success_overwrites_prior():
    heartbeat.record_success("job-x")
    first = heartbeat.get_heartbeat("job-x")

    heartbeat.record_success("job-x")
    second = heartbeat.get_heartbeat("job-x")

    # Same row (PK), updated timestamps
    assert second.last_run_at >= first.last_run_at
    assert second.consecutive_failures == 0


def test_record_failure_preserves_last_success_at():
    heartbeat.record_success("job-y")
    success_hb = heartbeat.get_heartbeat("job-y")
    success_at = success_hb.last_success_at

    heartbeat.record_failure("job-y", "boom")
    fail_hb = heartbeat.get_heartbeat("job-y")

    assert fail_hb.last_status == "fail"
    assert fail_hb.last_error == "boom"
    assert fail_hb.last_success_at == success_at  # preserved across failure
    assert fail_hb.consecutive_failures == 1


def test_record_failure_increments_consecutive_counter():
    heartbeat.record_failure("job-z", "err1")
    heartbeat.record_failure("job-z", "err2")
    heartbeat.record_failure("job-z", "err3")

    hb = heartbeat.get_heartbeat("job-z")
    assert hb.consecutive_failures == 3
    assert hb.last_error == "err3"


def test_record_success_after_failures_resets_counter():
    heartbeat.record_failure("job-w", "fail1")
    heartbeat.record_failure("job-w", "fail2")

    heartbeat.record_success("job-w")
    hb = heartbeat.get_heartbeat("job-w")

    assert hb.last_status == "success"
    assert hb.consecutive_failures == 0
    assert hb.last_error is None


def test_record_failure_truncates_long_error():
    long_error = "x" * 5000
    heartbeat.record_failure("job-trunc", long_error)

    hb = heartbeat.get_heartbeat("job-trunc")
    assert len(hb.last_error) == 2000  # truncated to 2000 chars per implementation


def test_get_heartbeat_returns_none_for_unknown():
    assert heartbeat.get_heartbeat("never-recorded") is None


def test_list_all_returns_alphabetical():
    heartbeat.record_success("zoro-job")
    heartbeat.record_success("alpha-job")
    heartbeat.record_success("nami-job")

    rows = heartbeat.list_all()
    names = [r.job_name for r in rows]
    assert names == sorted(names)
    assert "zoro-job" in names
    assert "alpha-job" in names
    assert "nami-job" in names


def test_list_stale_finds_old_runs(monkeypatch):
    # Record a heartbeat that's 2h old, by directly inserting via _get_conn.
    from shared.state import _get_conn

    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    fresh = datetime.now(timezone.utc).isoformat()
    _get_conn().execute(
        """
        INSERT INTO heartbeats (
            job_name, last_success_at, last_run_at, last_status,
            last_error, consecutive_failures, updated_at
        ) VALUES (?, ?, ?, 'success', NULL, 0, ?)
        """,
        ("old-job", old, old, old),
    )
    _get_conn().execute(
        """
        INSERT INTO heartbeats (
            job_name, last_success_at, last_run_at, last_status,
            last_error, consecutive_failures, updated_at
        ) VALUES (?, ?, ?, 'success', NULL, 0, ?)
        """,
        ("fresh-job", fresh, fresh, fresh),
    )
    _get_conn().commit()

    stale = heartbeat.list_stale(threshold_minutes=60)
    stale_names = [r.job_name for r in stale]
    assert "old-job" in stale_names
    assert "fresh-job" not in stale_names


def test_heartbeat_stale_minutes_property():
    heartbeat.record_success("hb-prop")
    hb = heartbeat.get_heartbeat("hb-prop")
    # Just-recorded → stale_minutes ≈ 0
    assert hb.stale_minutes is not None
    assert hb.stale_minutes <= 1


def test_heartbeat_success_age_minutes_after_failure():
    heartbeat.record_success("age-test")
    success_hb = heartbeat.get_heartbeat("age-test")
    assert success_hb.success_age_minutes is not None

    heartbeat.record_failure("age-test", "post-success-fail")
    fail_hb = heartbeat.get_heartbeat("age-test")
    # success_age_minutes still computable from preserved last_success_at
    assert fail_hb.success_age_minutes is not None


def test_heartbeat_success_age_is_none_when_never_succeeded():
    heartbeat.record_failure("never-success", "first run was a fail")
    hb = heartbeat.get_heartbeat("never-success")
    assert hb.success_age_minutes is None
