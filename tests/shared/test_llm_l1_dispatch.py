"""ADR-070 D1 / S1 / S1a：facade 依 model 字串 + runtime group 分派到 L1 的測試（mock，無網路）。

兩個分支都要鎖：

- 不在 ``L1_CUTOVER_GROUPS`` 裡的 runtime group（S1a 後只剩 ``cron`` / ``bridge`` /
  ``desktop``）→ 舊 ADR-026 路徑，``agent_sdk.run_text`` 完全不被碰（零行為改變）。
- ``gateway``（S1a 起的預設值）或用 ``cutover`` fixture 手動加進 ``L1_CUTOVER_GROUPS``
  → Claude 別名 / ``claude-*`` 走 ``run_text``，帶正確 ``call_class``；其他 model 仍走
  舊路徑。
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from shared import llm
from shared.llm_context import set_runtime_group
from shared.llm_router import is_claude_model, is_openrouter_slug, lane_for_model


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in list(os.environ):
        if key.startswith(("MODEL_", "AUTH_", "LLM_TRANSPORT")):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("NAKAMA_REQUIRE_MAX_PLAN", raising=False)


@pytest.fixture
def cutover(monkeypatch):
    """把某些 group 切到 L1，並把這個 process 設成指定 group。"""

    def _apply(*groups: str, current: str) -> None:
        monkeypatch.setattr(llm, "L1_CUTOVER_GROUPS", frozenset(groups))
        set_runtime_group(current)

    return _apply


def test_cutover_set_is_gateway_only_after_s1a():
    """S1a 完成：只有 ``gateway`` 切到 L1。S1b–d 才各自加 cron / bridge / desktop。"""
    assert llm.L1_CUTOVER_GROUPS == frozenset({"gateway"})


# ── D1 規則本身 ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("model", "lane"),
    [
        ("opus", "subscription"),
        ("sonnet", "subscription"),
        ("haiku", "subscription"),
        ("fable", "subscription"),
        ("claude-sonnet-4-6", "subscription"),
        ("claude-opus-5", "subscription"),
        ("openai/gpt-5.6-terra", "openrouter"),
        ("anthropic/claude-sonnet-4.5", "openrouter"),  # vendor/model 一律 L2
        ("gemini-2.5-pro", None),
        ("grok-4-fast", None),
        ("gpt-5", None),
        ("", None),
        (None, None),
    ],
)
def test_lane_for_model(model, lane):
    assert lane_for_model(model) == lane
    assert is_claude_model(model) is (lane == "subscription")
    assert is_openrouter_slug(model) is (lane == "openrouter")


# ── 非切換 group：零行為改變 ─────────────────────────────────────────────


@pytest.mark.parametrize("group", ["cron", "bridge", "desktop"])
def test_non_cutover_group_keeps_legacy_path(group):
    set_runtime_group(group)
    with (
        patch("shared.agent_sdk.run_text") as m_l1,
        patch("shared.llm.ask_claude", return_value="legacy") as m_claude,
        patch("shared.llm.ask_claude_multi", return_value="legacy-multi") as m_multi,
    ):
        assert llm.ask("hi", model="sonnet") == "legacy"
        multi = [{"role": "user", "content": "hi"}]
        assert llm.ask_multi(multi, model="claude-opus-4-8") == "legacy-multi"
    m_l1.assert_not_called()
    # 舊路徑的 model 轉換照舊（sonnet → API 替身 id）
    assert m_claude.call_args.kwargs["model"] == "claude-sonnet-4-6"
    assert m_multi.call_args.kwargs["model"] == "claude-opus-4-8"


def test_non_cutover_group_keeps_legacy_error_for_alias_old_path_does_not_know():
    """舊路徑本來就不認得 ``opus`` 別名（只有 sonnet 有 API 替身）—— S1a 不改這個行為。"""
    set_runtime_group("desktop")
    with patch("shared.agent_sdk.run_text") as m_l1, pytest.raises(ValueError, match="opus"):
        llm.ask("hi", model="opus")
    m_l1.assert_not_called()


def test_non_cutover_group_does_not_import_agent_sdk_path():
    """沒切換時 facade 不走 _ask_l1（lazy import 也不會發生）。"""
    set_runtime_group("desktop")
    with (
        patch("shared.llm._ask_l1") as m_l1,
        patch("shared.llm.ask_claude", return_value="legacy"),
    ):
        llm.ask("hi", model="claude-haiku-4-5")
    m_l1.assert_not_called()


# ── 切換後：Claude → L1，其餘照舊 ────────────────────────────────────────


def test_cutover_group_routes_claude_ask_to_run_text(cutover):
    cutover("gateway", current="gateway")
    with (
        patch("shared.agent_sdk.run_text", return_value="from-l1") as m_l1,
        patch("shared.llm.ask_claude") as m_claude,
    ):
        out = llm.ask("問題", system="系統", model="sonnet", max_tokens=321, temperature=0.3)
    assert out == "from-l1"
    m_claude.assert_not_called()
    m_l1.assert_called_once_with(
        "問題",
        system="系統",
        model="sonnet",  # 別名原樣交給 SDK，不經 api_model_id
        max_output_tokens=321,
        timeout_s=llm.L1_FACADE_TIMEOUT_S,
        call_class="batch",  # D5：沒宣告的一律當 batch
    )


def test_gateway_group_routes_to_l1_without_fixture_override():
    """S1a：``gateway`` 是 production 預設值，不需要 ``cutover`` fixture 才會切。"""
    set_runtime_group("gateway")
    with patch("shared.agent_sdk.run_text", return_value="from-l1") as m_l1:
        assert llm.ask("hi", model="sonnet") == "from-l1"
    m_l1.assert_called_once()


def test_ask_call_class_kwarg_propagates_to_run_text(cutover):
    """ADR-070 D5：呼叫點宣告的 ``call_class`` 要原樣傳到 ``run_text``。"""
    cutover("gateway", current="gateway")
    with patch("shared.agent_sdk.run_text", return_value="ok") as m_l1:
        llm.ask("hi", model="haiku", call_class="interactive")
    assert m_l1.call_args.kwargs["call_class"] == "interactive"


def test_ask_call_class_defaults_to_batch(cutover):
    """沒宣告 ``call_class`` 時走 D5「沒宣告的一律當 batch」。"""
    cutover("gateway", current="gateway")
    with patch("shared.agent_sdk.run_text", return_value="ok") as m_l1:
        llm.ask("hi", model="haiku")
    assert m_l1.call_args.kwargs["call_class"] == "batch"


def test_call_class_kwarg_ignored_on_legacy_path():
    """非 L1 路徑（未切換的 group）忽略 ``call_class``，不會因為多傳這個 kwarg 出錯。"""
    set_runtime_group("desktop")
    with patch("shared.llm.ask_claude", return_value="legacy") as m_claude:
        assert llm.ask("hi", model="sonnet", call_class="interactive") == "legacy"
    m_claude.assert_called_once()


def test_cutover_group_routes_claude_ask_multi_flattened(cutover):
    cutover("cron", current="cron")
    messages = [
        {"role": "user", "content": "第一句"},
        {"role": "assistant", "content": "回覆"},
        {"role": "user", "content": "請重寫"},
    ]
    with (
        patch("shared.agent_sdk.run_text", return_value="multi-l1") as m_l1,
        patch("shared.llm.ask_claude_multi") as m_multi,
    ):
        assert llm.ask_multi(messages, system="s", model="claude-opus-4-8") == "multi-l1"
    m_multi.assert_not_called()
    prompt = m_l1.call_args.args[0]
    assert prompt == "[USER]\n第一句\n\n[ASSISTANT]\n回覆\n\n[USER]\n請重寫"
    assert m_l1.call_args.kwargs["model"] == "claude-opus-4-8"
    assert m_l1.call_args.kwargs["max_output_tokens"] == 4096


def test_cutover_resolves_registry_model_then_routes(cutover):
    """model=None → 先照 registry 解析（Nami 預設是 sonnet 別名），再依 D1 分派。"""
    from shared.llm_context import clear_current_agent, set_current_agent

    cutover("gateway", current="gateway")
    set_current_agent("nami")
    try:
        with patch("shared.agent_sdk.run_text", return_value="ok") as m_l1:
            llm.ask("hi")
    finally:
        clear_current_agent()
    assert m_l1.call_args.kwargs["model"] == "sonnet"


def test_other_group_not_cut_over_keeps_legacy(cutover):
    cutover("gateway", current="bridge")
    with (
        patch("shared.agent_sdk.run_text") as m_l1,
        patch("shared.llm.ask_claude", return_value="legacy") as m_claude,
    ):
        assert llm.ask("hi", model="sonnet") == "legacy"
    m_l1.assert_not_called()
    m_claude.assert_called_once()


@pytest.mark.parametrize("model", ["gemini-2.5-pro", "grok-4-fast"])
def test_cutover_leaves_non_claude_models_on_legacy_path(cutover, model):
    cutover("gateway", current="gateway")
    with (
        patch("shared.agent_sdk.run_text") as m_l1,
        patch("shared.gemini_client.ask_gemini", return_value="gemini"),
        patch("shared.xai_client.ask_grok", return_value="grok"),
    ):
        assert llm.ask("hi", model=model) in {"gemini", "grok"}
    m_l1.assert_not_called()


def test_ask_with_tools_is_not_routed_in_s1(cutover):
    """tool-use 呼叫點是 S4 的範圍；S1 的 facade 只分派 ask / ask_multi。"""
    cutover("gateway", current="gateway")
    with (
        patch("shared.agent_sdk.run_text") as m_l1,
        patch("shared.llm.call_claude_with_tools", return_value="msg") as m_tools,
    ):
        llm.ask_with_tools([{"role": "user", "content": "x"}], [], model="claude-haiku-4-5")
    m_l1.assert_not_called()
    m_tools.assert_called_once()


def test_l1_errors_propagate_unchanged(cutover):
    from shared.agent_sdk import SubscriptionExhausted

    cutover("gateway", current="gateway")
    with patch("shared.agent_sdk.run_text", side_effect=SubscriptionExhausted("used up")):
        with pytest.raises(SubscriptionExhausted):
            llm.ask("hi", model="haiku")


# ── 驗收：沒有 production 呼叫路徑直接接到 run_text ─────────────────────


def test_no_production_code_calls_run_text_directly():
    """``L1_CUTOVER_GROUPS`` 為空時唯一的入口是 facade；不新增其他 production 呼叫點。

    只抓實際的呼叫（``run_text(``），docstring 提到名字不算。唯一的例外是 Franky 的
    llm_lane 復原探針：它必須繞過 lane 分派直接試訂閱，只准用專用的
    ``run_text_probe_subscription``（ADR-070 D5，S2a）。
    """
    import re

    repo = Path(__file__).resolve().parents[2]
    callers, probe_callers = [], []
    for top in ("agents", "gateway", "scripts", "shared", "thousand_sunny"):
        for path in (repo / top).rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="replace")
            rel = path.relative_to(repo).as_posix()
            if re.search(r"\brun_text\(", text):
                callers.append(rel)
            if re.search(r"\brun_text_probe_subscription\(", text):
                probe_callers.append(rel)
    assert sorted(callers) == ["shared/agent_sdk.py", "shared/llm.py"]
    assert sorted(probe_callers) == ["agents/franky/health_check.py", "shared/agent_sdk.py"]
