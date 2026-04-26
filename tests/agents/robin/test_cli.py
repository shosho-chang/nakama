"""agents/robin/__main__.py — CLI heartbeat instrumentation 測試（Phase 5B-2）。

Robin 兩個 mode：
- --mode pubmed_digest（cron 05:30，instrumented）
- --mode ingest（manual file watcher，不 instrument）
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agents.robin.__main__ import _run_pubmed_digest


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
