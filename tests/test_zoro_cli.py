"""agents/zoro/__main__.py — CLI dispatch 測試。

重點是 N2 code review 的 footgun 修正：無 subcommand 不能自動跑 scout 真 publish。
"""

from __future__ import annotations

from unittest.mock import patch

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
