"""agents/zoro/__main__.py — CLI dispatch 測試。

重點是 N2 code review 的 footgun 修正：無 subcommand 不能自動跑 scout 真 publish。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agents.zoro.__main__ import main


def test_main_no_args_prints_help_and_exits_2(capsys):
    """無 subcommand 要 print help + return 2，不能 dispatch 到 scout。

    保護 against `python -m agents.zoro` 誤打 → 真 publish 到 #brainstorm。
    """
    with patch("agents.zoro.__main__._cmd_scout") as m_scout:
        rc = main([])

    assert rc == 2
    m_scout.assert_not_called()
    out = capsys.readouterr().out
    assert "scout" in out  # help mentions the scout subcommand


def test_main_scout_subcommand_dispatches(capsys):
    with patch("agents.zoro.__main__._cmd_scout", return_value=0) as m_scout:
        rc = main(["scout"])

    assert rc == 0
    m_scout.assert_called_once()
    ns = m_scout.call_args.args[0]
    assert ns.dry_run is False


def test_main_scout_dry_run_flag_propagates():
    with patch("agents.zoro.__main__._cmd_scout", return_value=0) as m_scout:
        main(["scout", "--dry-run"])

    ns = m_scout.call_args.args[0]
    assert ns.dry_run is True


# ---------------------------------------------------------------------------
# Phase 5B-2 — heartbeat instrumentation
# ---------------------------------------------------------------------------


def _fake_topic() -> MagicMock:
    return MagicMock(title="zone 2 cardio", velocity_score=80.0, relevance_score=0.9, domain="運動")


def test_scout_records_heartbeat_success_on_happy_path():
    from shared import heartbeat

    with patch("agents.zoro.brainstorm_scout.run", return_value=_fake_topic()):
        rc = main(["scout"])

    assert rc == 0
    hb = heartbeat.get_heartbeat("zoro-brainstorm-scout")
    assert hb is not None
    assert hb.last_status == "success"
    assert hb.consecutive_failures == 0


def test_scout_records_heartbeat_success_when_no_topic_picked():
    """run() returning None still means the cron *ran* — operators should see fresh heartbeat."""
    from shared import heartbeat

    with patch("agents.zoro.brainstorm_scout.run", return_value=None):
        rc = main(["scout"])

    assert rc == 0
    hb = heartbeat.get_heartbeat("zoro-brainstorm-scout")
    assert hb is not None
    assert hb.last_status == "success"


def test_scout_records_heartbeat_failure_on_exception():
    from shared import heartbeat

    with patch("agents.zoro.brainstorm_scout.run", side_effect=RuntimeError("trends API down")):
        with pytest.raises(RuntimeError):
            main(["scout"])

    hb = heartbeat.get_heartbeat("zoro-brainstorm-scout")
    assert hb is not None
    assert hb.last_status == "fail"
    assert "trends API down" in (hb.last_error or "")
    assert hb.consecutive_failures == 1


def test_scout_dry_run_does_not_record_heartbeat():
    """Dry-run is manual / ad-hoc; recording it would mask real cron misses."""
    from shared import heartbeat

    with patch("agents.zoro.brainstorm_scout.run", return_value=_fake_topic()):
        main(["scout", "--dry-run"])

    assert heartbeat.get_heartbeat("zoro-brainstorm-scout") is None


def test_scout_dry_run_failure_does_not_record_heartbeat():
    from shared import heartbeat

    with patch("agents.zoro.brainstorm_scout.run", side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError):
            main(["scout", "--dry-run"])

    assert heartbeat.get_heartbeat("zoro-brainstorm-scout") is None
