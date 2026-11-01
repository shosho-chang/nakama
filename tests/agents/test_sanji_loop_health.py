"""Sanji 主迴圈的故障告警：連續失敗才 DM、一個故障一則、恢復再 DM 一次。

2026-09-06 起 Cloudflare 擋下每一輪請求整整三週，loop 只每分鐘記一行一模一樣的
ERROR、自己從不告警。本檔釘住補上的行為。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agents.franky.slack_bot import FrankySlackBot
from agents.sanji import loop as loop_mod
from agents.sanji.loop import SanjiLoop
from agents.sanji.settings import SanjiConfig
from agents.sanji.store import Store

T0 = datetime(2026, 9, 5, 16, 0, tzinfo=timezone.utc)  # 09-06 00:00 台北


@pytest.fixture()
def slack():
    bot = MagicMock(spec=FrankySlackBot)
    with patch("agents.franky.slack_bot.FrankySlackBot.from_env", return_value=bot):
        yield bot


@pytest.fixture()
def sanji(tmp_path: Path):
    cfg = SanjiConfig(
        wp_base_url="https://example.test", wp_user="sanji", wp_app_password="x", sanji_user_id=126
    )
    store = Store(db_path=tmp_path / "sanji.db")
    yield SanjiLoop(cfg, MagicMock(), store)
    store.close()


def _fail(sanji: SanjiLoop, n: int, *, start: datetime = T0, msg: str = "GET /events → 403"):
    for i in range(n):
        sanji.on_cycle_error(RuntimeError(msg), now=start + timedelta(minutes=i))


def _texts(slack) -> list[str]:
    return [c.args[0] for c in slack.post_plain.call_args_list]


def test_short_blip_does_not_alert(sanji, slack):
    _fail(sanji, loop_mod._ALERT_AFTER_FAILURES - 1)
    sanji.on_cycle_ok()

    slack.post_plain.assert_not_called()


def test_sustained_failure_alerts_once(sanji, slack):
    _fail(sanji, 120)  # 兩小時，每小時會重送一次——但 dedupe 一天只放一則

    texts = _texts(slack)
    assert len(texts) == 1
    assert "Sanji 主迴圈連續 5 輪失敗" in texts[0]
    assert "09-06 00:00" in texts[0]  # 台北時間
    assert "GET /events → 403" in texts[0]


def test_recovery_sends_one_resolution_and_rearms(sanji, slack):
    _fail(sanji, 10)
    sanji.on_cycle_ok()
    sanji.on_cycle_ok()  # 之後的成功不再 DM

    texts = _texts(slack)
    assert len(texts) == 2
    assert texts[1].startswith(":white_check_mark:")
    assert "已恢復" in texts[1]

    # 恢復後 dedupe 已解除：同一天再壞一次要立刻告警，不能被上一則的 24h 窗口吞掉
    _fail(sanji, 5, start=T0 + timedelta(hours=2))
    assert len(_texts(slack)) == 3


def test_restart_then_immediate_recovery_still_resolves(tmp_path, slack):
    """上一個 process 告警後被重啟、新 process 第一輪就成功——也要收尾，不能讓告警永遠 firing。"""
    cfg = SanjiConfig(
        wp_base_url="https://example.test", wp_user="sanji", wp_app_password="x", sanji_user_id=126
    )
    store = Store(db_path=tmp_path / "sanji.db")
    try:
        _fail(SanjiLoop(cfg, MagicMock(), store), 5)
        SanjiLoop(cfg, MagicMock(), store).on_cycle_ok()
    finally:
        store.close()

    texts = _texts(slack)
    assert len(texts) == 2
    assert "已恢復" in texts[1]


def test_first_success_without_prior_alert_is_silent(sanji, slack):
    sanji.on_cycle_ok()

    slack.post_plain.assert_not_called()


def test_repeated_identical_error_logged_once_per_hour(sanji, caplog):
    caplog.set_level("ERROR", logger="nakama.sanji.loop")
    with patch.object(loop_mod, "alert"):
        _fail(sanji, 59)
        sanji.on_cycle_error(RuntimeError("另一個錯誤"), now=T0 + timedelta(minutes=59))

    errors = [r.message for r in caplog.records if "cycle error" in r.message]
    assert len(errors) == 2  # 第一次 + 錯誤內容變了；中間 58 行重複全省略
    assert "連續第 1 輪" in errors[0]
    assert "另一個錯誤" in errors[1]


def test_alert_failure_does_not_break_loop(sanji):
    with patch.object(loop_mod, "alert", side_effect=RuntimeError("slack down")):
        _fail(sanji, 5)  # 不應拋出
