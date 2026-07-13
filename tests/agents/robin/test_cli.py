"""agents/robin/__main__.py — CLI heartbeat instrumentation 測試（Phase 5B-2）。

Robin 三個 mode：
- --mode pubmed_digest（cron 05:30，instrumented）
- --mode daily_review（cron 05:15，instrumented；週一自動帶每週清掃）
- --mode ingest（manual file watcher，不 instrument）
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agents.robin.__main__ import _run_daily_review, _run_pubmed_digest


def _fake_pipeline_class(*, raises: Exception | None = None) -> MagicMock:
    """Return a MagicMock that mimics PubMedDigestPipeline(...) constructor + .execute()."""
    instance = MagicMock()
    if raises is not None:
        instance.execute.side_effect = raises
    cls = MagicMock(return_value=instance)
    return cls


def test_pubmed_digest_records_heartbeat_success():
    from shared import heartbeat

    with patch("agents.robin.__main__.PubMedDigestPipeline", _fake_pipeline_class()):
        _run_pubmed_digest(dry_run=False)

    hb = heartbeat.get_heartbeat("robin-pubmed-digest")
    assert hb is not None
    assert hb.last_status == "success"
    assert hb.consecutive_failures == 0


def test_pubmed_digest_records_heartbeat_failure_on_exception():
    from shared import heartbeat

    cls = _fake_pipeline_class(raises=RuntimeError("eutils 503"))
    with patch("agents.robin.__main__.PubMedDigestPipeline", cls):
        with pytest.raises(RuntimeError):
            _run_pubmed_digest(dry_run=False)

    hb = heartbeat.get_heartbeat("robin-pubmed-digest")
    assert hb is not None
    assert hb.last_status == "fail"
    assert "eutils 503" in (hb.last_error or "")


def test_pubmed_digest_dry_run_does_not_record_heartbeat():
    from shared import heartbeat

    with patch("agents.robin.__main__.PubMedDigestPipeline", _fake_pipeline_class()):
        _run_pubmed_digest(dry_run=True)

    assert heartbeat.get_heartbeat("robin-pubmed-digest") is None


def test_pubmed_digest_dry_run_failure_does_not_record_heartbeat():
    from shared import heartbeat

    cls = _fake_pipeline_class(raises=RuntimeError("boom"))
    with patch("agents.robin.__main__.PubMedDigestPipeline", cls):
        with pytest.raises(RuntimeError):
            _run_pubmed_digest(dry_run=True)

    assert heartbeat.get_heartbeat("robin-pubmed-digest") is None


def test_ingest_mode_does_not_record_pubmed_heartbeat(monkeypatch):
    """--mode ingest is the manual file-watcher path — must not touch the pubmed-digest job."""
    from shared import heartbeat

    monkeypatch.setattr("sys.argv", ["agents.robin", "--mode", "ingest"])
    fake_agent = MagicMock()
    with patch("agents.robin.__main__.RobinAgent", return_value=fake_agent):
        from agents.robin.__main__ import main

        main()

    fake_agent.execute.assert_called_once()
    assert heartbeat.get_heartbeat("robin-pubmed-digest") is None


# ── daily_review mode（cron 05:15，instrumented；週一自動 weekly）──────────────


def test_daily_review_records_heartbeat_success(monkeypatch, tmp_path):
    from shared import heartbeat

    monkeypatch.setattr("agents.robin.daily_review.run_daily_review", lambda **kw: MagicMock())
    monkeypatch.setattr("agents.robin.daily_review.save_review_bundle", lambda *a, **k: None)
    monkeypatch.setattr("shared.config.get_vault_path", lambda: tmp_path)
    _run_daily_review(weekly=False)

    hb = heartbeat.get_heartbeat("robin-daily-review")
    assert hb is not None
    assert hb.last_status == "success"
    assert hb.consecutive_failures == 0


def test_daily_review_records_heartbeat_failure_on_exception(monkeypatch):
    from shared import heartbeat

    def _boom(**kw):
        raise RuntimeError("vault offline")

    monkeypatch.setattr("agents.robin.daily_review.run_daily_review", _boom)
    with pytest.raises(RuntimeError):
        _run_daily_review(weekly=False)

    hb = heartbeat.get_heartbeat("robin-daily-review")
    assert hb is not None
    assert hb.last_status == "fail"
    assert "vault offline" in (hb.last_error or "")


def test_daily_review_cli_auto_detects_weekly_on_monday(monkeypatch):
    """cron 單行每天跑；週一自動帶每週清掃（規則在 code，不靠 cron 排程記憶）。"""
    from datetime import date as _date

    captured: dict = {}
    monkeypatch.setattr(
        "agents.robin.__main__._run_daily_review",
        lambda *, weekly: captured.update(weekly=weekly),
    )
    monkeypatch.setattr("sys.argv", ["agents.robin", "--mode", "daily_review"])
    from agents.robin.__main__ import main

    def _fix_today(d):
        monkeypatch.setattr("agents.robin.daily_review._local_today", lambda *a, **k: d)

    _fix_today(_date(2026, 6, 22))
    main()  # Monday → weekly sweep
    assert captured["weekly"] is True

    captured.clear()
    _fix_today(_date(2026, 6, 23))
    main()  # Tuesday → plain daily
    assert captured["weekly"] is False
