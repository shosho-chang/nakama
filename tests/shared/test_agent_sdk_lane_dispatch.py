"""ADR-070 S2a（issue #1321）：``shared.agent_sdk.run_text`` 依 lane 狀態分派。

涵蓋：已經被擋的 family 直接 fail-fast / 改走 OpenRouter，不浪費一次注定失敗的
SDK 呼叫；剛好這次呼叫才踩到額度的，記一筆狀態轉換後 ``interactive`` 就地自動
改道重試；``batch`` 停在 exhausted 不自動改道；OpenRouter 路徑 fail closed；
桌機（runtime group）完全不參與這條機制。

**不打網路、不呼叫 LLM**：``claude_agent_sdk.query`` 換假 stream，OpenRouter
client 也整個 monkeypatch 掉。沿用 ``test_agent_sdk_l1`` 的 fixtures /
message-builder（同一套假 SDK message，重複維護兩份容易 drift）。
"""

from __future__ import annotations

import pytest

from shared import agent_sdk, llm_lane
from shared.agent_sdk import AgentSdkError, SubscriptionExhausted, run_text
from shared.llm_context import clear_current_agent
from tests.shared.test_agent_sdk_l1 import _assistant, _rate_limit, _result  # noqa: F401


@pytest.fixture(autouse=True)
def _l1_env(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-test-token")
    monkeypatch.delenv("NAMI_SDK_OAUTH_TOKEN", raising=False)
    monkeypatch.setattr(agent_sdk, "_legacy_token_logged", False)
    clear_current_agent()
    yield
    clear_current_agent()


@pytest.fixture(autouse=True)
def _vps_side(monkeypatch):
    """這個模組全部模擬 VPS-side（gateway/cron/bridge），才會走 lane 分派。"""
    from shared import llm_context

    monkeypatch.setattr(llm_context, "_runtime_group", "bridge")


@pytest.fixture(autouse=True)
def _no_slack(monkeypatch):
    monkeypatch.setattr(llm_lane, "_fire_alert", lambda *a, **k: None)


@pytest.fixture
def fake_sdk(monkeypatch):
    import claude_agent_sdk

    from tests.shared.test_agent_sdk_l1 import FakeSdk

    def _install(*messages, **kw):
        fake = FakeSdk(messages, **kw)
        monkeypatch.setattr(claude_agent_sdk, "query", fake.query)
        return fake

    return _install


def _call(**kw):
    kw.setdefault("system", "sys")
    kw.setdefault("model", "sonnet")
    kw.setdefault("timeout_s", 30)
    kw.setdefault("call_class", "batch")
    return run_text(kw.pop("prompt", "hi"), **kw)


def _fake_ask_openrouter(monkeypatch, *, text="openrouter reply", cost=0.01, error=None):
    calls = []

    def _fake(prompt, **kwargs):
        calls.append({"prompt": prompt, **kwargs})
        if error is not None:
            raise error
        on_cost = kwargs.get("on_cost")
        if on_cost is not None:
            on_cost(cost)
        return text

    monkeypatch.setattr("shared.openrouter_client.ask_openrouter", _fake)
    return calls


# ── 已經被擋：pre-check fail-fast / reroute，不打 SDK ──────────────────


def test_already_exhausted_batch_fails_fast_without_touching_sdk(fake_sdk):
    fake = fake_sdk()  # 沒有訊息也沒關係：根本不該被呼叫到
    llm_lane.record_exhausted("batch", model="opus", rate_limit_type="seven_day_opus", resets_at=1)

    with pytest.raises(SubscriptionExhausted):
        _call(model="opus", call_class="batch")
    assert fake.calls == []  # pre-check 擋下，完全沒打 SDK


def test_already_openrouter_auto_reroutes_without_touching_sdk(monkeypatch, fake_sdk):
    fake = fake_sdk()
    or_calls = _fake_ask_openrouter(monkeypatch, text="rerouted answer")
    llm_lane.record_exhausted(
        "interactive", model="sonnet", rate_limit_type="five_hour", resets_at=1
    )
    # 上面那次已經自動轉 openrouter_auto（interactive 當日上限還沒花）。

    out = _call(model="sonnet", call_class="interactive")
    assert out == "rerouted answer"
    assert fake.calls == []  # 沒打訂閱 SDK
    assert len(or_calls) == 1
    assert or_calls[0]["model"] == "sonnet"


def test_blocked_family_does_not_block_other_family(fake_sdk):
    """seven_day_opus 只擋 opus；sonnet 呼叫照常打 SDK。"""
    llm_lane.record_exhausted("batch", model="opus", rate_limit_type="seven_day_opus", resets_at=1)
    fake = fake_sdk(_assistant(model="claude-sonnet-5", text="ok"), _result("ok"))

    out = _call(model="sonnet", call_class="batch")
    assert out == "ok"
    assert len(fake.calls) == 1


# ── 剛好這次踩到：抓到 SubscriptionExhausted 記狀態，interactive 自動重試 ──


def test_interactive_auto_reroutes_on_first_exhaustion(monkeypatch, fake_sdk):
    fake_sdk(
        _rate_limit(status="rejected", rate_limit_type="five_hour", resets_at=1790001234),
        _assistant(model="<synthetic>", error="rate_limit", text="hit limit"),
        _result("hit limit", is_error=True),
        raise_after=Exception("Claude Code returned an error result: hit limit"),
    )
    or_calls = _fake_ask_openrouter(monkeypatch, text="fallback text", cost=1.23)

    out = _call(model="sonnet", call_class="interactive")

    assert out == "fallback text"
    assert len(or_calls) == 1
    state = llm_lane.get_state()
    assert state.interactive.status == "openrouter_auto"
    assert state.interactive.spend_usd == pytest.approx(1.23)


def test_batch_does_not_auto_reroute_on_first_exhaustion(fake_sdk):
    fake_sdk(
        _rate_limit(status="rejected", rate_limit_type="seven_day_opus", resets_at=1790001234),
        _assistant(model="<synthetic>", error="rate_limit", text="hit limit"),
        _result("hit limit", is_error=True),
        raise_after=Exception("Claude Code returned an error result: hit limit"),
    )
    with pytest.raises(SubscriptionExhausted):
        _call(model="opus", call_class="batch")
    state = llm_lane.get_state()
    assert state.batch.status == "exhausted"
    assert state.batch.blocked_family == "opus"


def test_interactive_raises_when_daily_cap_already_spent(monkeypatch, fake_sdk):
    """已經轉 openrouter_auto 但當日 $5 上限已經花完 → 直接 raise，不再打 OpenRouter。"""
    llm_lane.record_exhausted(
        "interactive", model="sonnet", rate_limit_type="five_hour", resets_at=1
    )
    llm_lane.record_openrouter_spend("interactive", cost_usd=10.0)  # 超過 $5，轉回 exhausted
    assert llm_lane.get_state().interactive.status == "exhausted"

    or_calls = _fake_ask_openrouter(monkeypatch)
    with pytest.raises(SubscriptionExhausted):
        _call(model="sonnet", call_class="interactive")
    assert or_calls == []


# ── OpenRouter 路徑：fail closed、structured output 不支援 ─────────────


def test_openrouter_reroute_fails_closed_without_api_key(monkeypatch, fake_sdk):
    fake_sdk(
        _rate_limit(status="rejected", rate_limit_type="five_hour", resets_at=1),
        _assistant(model="<synthetic>", error="rate_limit", text="hit limit"),
        _result("hit limit", is_error=True),
        raise_after=Exception("Claude Code returned an error result: hit limit"),
    )
    monkeypatch.setattr(
        "shared.openrouter_client.ask_openrouter",
        lambda *a, **k: (_ for _ in ()).throw(KeyError("OPENROUTER_API_KEY")),
    )
    with pytest.raises(AgentSdkError, match="OPENROUTER_API_KEY"):
        _call(model="sonnet", call_class="interactive")


def test_structured_output_over_openrouter_fails_loud(fake_sdk):
    schema = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "properties": {"x": {"type": "integer"}},
        "required": ["x"],
    }
    llm_lane.record_exhausted(
        "interactive", model="sonnet", rate_limit_type="five_hour", resets_at=1
    )
    with pytest.raises(AgentSdkError, match="structured output"):
        _call(model="sonnet", call_class="interactive", output_schema=schema)


# ── 桌機不參與 ──────────────────────────────────────────────────────────


def test_desktop_runtime_group_ignores_lane_state(monkeypatch, fake_sdk):
    """桌機：即使 llm_lane 顯示已經 exhausted，行為跟 S1 完全一樣（照打訂閱）。"""
    from shared import llm_context

    llm_lane.record_exhausted("batch", model="opus", rate_limit_type="seven_day_opus", resets_at=1)
    monkeypatch.setattr(llm_context, "_runtime_group", "desktop")
    fake = fake_sdk(_assistant(model="claude-opus-5", text="ok"), _result("ok"))

    out = _call(model="opus", call_class="batch")
    assert out == "ok"
    assert len(fake.calls) == 1
