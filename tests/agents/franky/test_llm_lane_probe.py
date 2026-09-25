"""ADR-070 D5（S2a，issue #1321）：Franky 的 llm_lane 復原探針 + OpenRouter 每日 canary。

涵蓋：沒被擋時整個 tick 都是 no-op；到了探測時機才真的試訂閱，成功就切回訂閱
（全系統唯一 owner）、失敗維持原狀不重複發通知；沒有 ``resets_at`` 時退回 30
分鐘節流；切回訂閱後 60 分鐘觀察期內不再自動重探；OpenRouter canary 每天最多跑
一次。**不打網路、不呼叫 LLM**：``run_text_probe_subscription`` / ``ask_openrouter``
一律 monkeypatch。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agents.franky.health_check import (
    _probe_llm_lane_class,
    probe_llm_lane_openrouter_canary,
    probe_llm_lane_recovery,
)
from shared import llm_lane


@pytest.fixture(autouse=True)
def _no_slack(monkeypatch):
    monkeypatch.setattr(llm_lane, "_fire_alert", lambda *a, **k: None)


@pytest.fixture(autouse=True)
def _vps_side(monkeypatch):
    from shared import llm_context

    monkeypatch.setattr(llm_context, "_runtime_group", "bridge")


def _now():
    return datetime.now(tz=timezone.utc)


# ── _probe_llm_lane_class ────────────────────────────────────────────────


def test_returns_none_when_not_blocked():
    assert _probe_llm_lane_class("interactive", now=_now()) is None


def test_due_by_resets_at_recovers_on_success(monkeypatch):
    past = int((_now() - timedelta(minutes=1)).timestamp())
    llm_lane.record_exhausted(
        "interactive", model="sonnet", rate_limit_type="five_hour", resets_at=past
    )
    monkeypatch.setattr("shared.agent_sdk.run_text_probe_subscription", lambda *a, **k: "ok")

    probe = _probe_llm_lane_class("interactive", now=_now())
    assert probe is not None
    assert probe.status == "ok"
    assert probe.target == "llm_lane_interactive"
    assert llm_lane.get_state().interactive.status == "subscription"


def test_probe_failure_keeps_state_exhausted(monkeypatch):
    past = int((_now() - timedelta(minutes=1)).timestamp())
    llm_lane.record_exhausted(
        "batch", model="opus", rate_limit_type="seven_day_opus", resets_at=past
    )

    def _boom(*a, **k):
        raise RuntimeError("still rate limited")

    monkeypatch.setattr("shared.agent_sdk.run_text_probe_subscription", _boom)

    probe = _probe_llm_lane_class("batch", now=_now())
    assert probe is not None
    assert probe.status == "fail"
    assert "still rate limited" in probe.error
    assert llm_lane.get_state().batch.status == "exhausted"


def test_not_due_yet_without_resets_at_skips(monkeypatch):
    """沒有 resets_at、也還沒到 30 分鐘節流窗：這個 tick 直接 no-op。"""
    llm_lane.record_exhausted("interactive", model="sonnet", rate_limit_type=None, resets_at=None)
    called = {"n": 0}

    def _fail(*a, **k):
        called["n"] += 1
        raise RuntimeError("nope")

    monkeypatch.setattr("shared.agent_sdk.run_text_probe_subscription", _fail)
    # 第一次一定到期（沒有上次紀錄）。
    first = _probe_llm_lane_class("interactive", now=_now())
    assert first is not None
    assert called["n"] == 1

    second = _probe_llm_lane_class("interactive", now=_now())
    assert second is None  # 30 分鐘節流窗內，不重試
    assert called["n"] == 1


def test_due_again_after_30_minutes_without_resets_at(monkeypatch):
    llm_lane.record_exhausted("interactive", model="sonnet", rate_limit_type=None, resets_at=None)
    calls = {"n": 0}

    def _fail(*a, **k):
        calls["n"] += 1
        raise RuntimeError("nope")

    monkeypatch.setattr("shared.agent_sdk.run_text_probe_subscription", _fail)
    t0 = _now()
    _probe_llm_lane_class("interactive", now=t0)
    assert calls["n"] == 1

    later = t0 + timedelta(minutes=31)
    _probe_llm_lane_class("interactive", now=later)
    assert calls["n"] == 2


def test_observation_window_suppresses_reprobe_after_flap(monkeypatch):
    """切回訂閱後 60 分鐘內又失敗（exhausted）：觀察期內不再自動重探。"""
    past = int((_now() - timedelta(minutes=1)).timestamp())
    llm_lane.record_exhausted(
        "batch", model="opus", rate_limit_type="seven_day_opus", resets_at=past
    )
    monkeypatch.setattr("shared.agent_sdk.run_text_probe_subscription", lambda *a, **k: "ok")
    probe = _probe_llm_lane_class("batch", now=_now())
    assert probe is not None and probe.status == "ok"
    assert llm_lane.get_state().batch.status == "subscription"

    # 馬上又用完額度（模擬 flap）
    llm_lane.record_exhausted(
        "batch", model="opus", rate_limit_type="seven_day_opus", resets_at=past
    )

    def _boom(*a, **k):
        raise AssertionError("60 分鐘觀察期內不該再打探針")

    monkeypatch.setattr("shared.agent_sdk.run_text_probe_subscription", _boom)
    result = _probe_llm_lane_class("batch", now=_now())
    assert result is None


# ── OpenRouter canary ─────────────────────────────────────────────────


def test_canary_runs_on_first_tick_and_records_ok(monkeypatch):
    monkeypatch.setattr("shared.openrouter_client.ask_openrouter", lambda *a, **k: "ok")
    probe = probe_llm_lane_openrouter_canary(now=_now())
    assert probe is not None
    assert probe.status == "ok"
    assert probe.target == "llm_lane_openrouter_canary"


def test_canary_skips_within_24h(monkeypatch):
    calls = {"n": 0}

    def _fake(*a, **k):
        calls["n"] += 1
        return "ok"

    monkeypatch.setattr("shared.openrouter_client.ask_openrouter", _fake)
    t0 = _now()
    probe_llm_lane_openrouter_canary(now=t0)
    assert calls["n"] == 1

    again = probe_llm_lane_openrouter_canary(now=t0 + timedelta(hours=1))
    assert again is None
    assert calls["n"] == 1

    later = probe_llm_lane_openrouter_canary(now=t0 + timedelta(hours=25))
    assert later is not None
    assert calls["n"] == 2


def test_canary_reports_fail_on_exception(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("openrouter down")

    monkeypatch.setattr("shared.openrouter_client.ask_openrouter", _boom)
    probe = probe_llm_lane_openrouter_canary(now=_now())
    assert probe is not None
    assert probe.status == "fail"
    assert "openrouter down" in probe.error


# ── probe_llm_lane_recovery（組合入口）────────────────────────────────


def test_recovery_combines_both_classes_and_canary(monkeypatch):
    past = int((_now() - timedelta(minutes=1)).timestamp())
    llm_lane.record_exhausted(
        "interactive", model="sonnet", rate_limit_type="five_hour", resets_at=past
    )
    monkeypatch.setattr("shared.agent_sdk.run_text_probe_subscription", lambda *a, **k: "ok")
    monkeypatch.setattr("shared.openrouter_client.ask_openrouter", lambda *a, **k: "ok")

    probes = probe_llm_lane_recovery(now=_now())
    targets = {p.target for p in probes}
    assert targets == {"llm_lane_interactive", "llm_lane_openrouter_canary"}
