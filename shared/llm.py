"""Cross-provider LLM facade — 把 (agent, task) 路由到對的 provider wrapper。

呼叫端只需要 ``shared.llm.ask*(...)``，不用自己判斷 Claude / Grok / Gemini。
router 決定 model ID → provider，facade 再 dispatch 到對應的 ``shared/*_client.py``。

公開介面：

- :func:`ask` — 純文字 → 純文字（單回合）
- :func:`ask_multi` — messages → 純文字（多回合）
- :func:`ask_with_tools` — tool-use messages → 完整 Message（驅動 agent loop）
- :func:`ask_with_audio` — 音檔 + prompt → 純文字 / parsed BaseModel

目前 coverage：Anthropic（text + tools）+ xAI（text）+ Google（text + audio）。
其他 provider 與其他能力組合會 raise ``NotImplementedError``，讓 caller 明確
看到缺什麼（避免 silent fallback 那種不透明錯誤）。

ADR-070 D1（S1 起）：``ask`` / ``ask_multi`` 先看 **L1 切換開關**。這個 process 的
runtime group（``shared.llm_context.get_runtime_group``）在 :data:`L1_CUTOVER_GROUPS`
裡、而且 model 是 Claude 別名或 ``claude-*`` 時，改走
:func:`shared.agent_sdk.run_text`（Claude 訂閱）；否則照舊走 ADR-026 路徑，一個位元
都不變。S1 出貨時 :data:`L1_CUTOVER_GROUPS` 是空集合 → 零行為改變。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from shared.anthropic_client import ask_claude, ask_claude_multi, call_claude_with_tools
from shared.llm_context import get_current_agent, get_runtime_group
from shared.llm_router import (
    api_model_id,
    get_auth_policy,
    get_model,
    get_provider,
    is_claude_model,
)
from shared.llm_transport import openrouter_enabled

if TYPE_CHECKING:
    import anthropic
    from pydantic import BaseModel


# ── ADR-070 D1 / D4：L1 切換開關（寫在 code，不放 .env）──────────────────

L1_CUTOVER_GROUPS: frozenset[str] = frozenset()
"""哪些 runtime group 的 Claude 呼叫改走 L1（Claude 訂閱，Agent SDK）。

S1 出貨時是空集合：沒有任何 production 路徑改變。S1a–d 各自把自己的 group
（``gateway`` / ``cron`` / ``bridge`` / ``desktop``）加進來；回滾就是 revert 那個 PR
（ADR-070 §回滾）。group 在 process 入口由 ``shared.llm_context.set_runtime_group`` 設定。
"""

L1_FACADE_TIMEOUT_S = 600.0
"""facade 走 L1 時的 timeout（reliability §7 必填）。沿用 ``claude -p`` 路徑的預設 600s
（``shared/claude_cli_client.py``）；需要不同上限的呼叫點直接用 ``agent_sdk.run_text``。"""

L1_FACADE_CALL_CLASS = "batch"
"""facade 呼叫預設的 call class：ADR-070 D5「沒宣告的一律當作 batch」。"""


def _route_to_l1(model: str) -> bool:
    """這次呼叫要不要走 L1：runtime group 已切換，且 model 是 Claude 別名 / ``claude-*``。"""
    return get_runtime_group() in L1_CUTOVER_GROUPS and is_claude_model(model)


def _ask_l1(prompt: str, *, system: str, model: str, max_tokens: int) -> str:
    # lazy import：沒切換時 facade 不載入 Agent SDK
    from shared.agent_sdk import run_text  # noqa: PLC0415

    return run_text(
        prompt,
        system=system,
        model=model,
        max_output_tokens=max_tokens,
        timeout_s=L1_FACADE_TIMEOUT_S,
        call_class=L1_FACADE_CALL_CLASS,
    )


def ask(
    prompt: str,
    *,
    system: str = "",
    model: str | None = None,
    task: str = "default",
    max_tokens: int = 4096,
    temperature: float | None = None,
    thinking_budget: int | None = None,
) -> str:
    """送一次 LLM 請求，自動依 (agent, model) 路由到對的 provider。

    - ``model=None`` → 依 thread-local agent 從 router 解析
    - Claude ID → 走 :func:`shared.anthropic_client.ask_claude`
    - Grok ID → 走 :func:`shared.xai_client.ask_grok`
    - Gemini ID → 走 :func:`shared.gemini_client.ask_gemini`

    其他 provider 尚未實作，會拋 ``NotImplementedError``，讓 caller 明確看到
    缺什麼（避免 router silent 回預設那種不透明的 fallback）。

    ``thinking_budget`` 僅對 Gemini 生效（其他 provider 忽略）。傳 ``None`` 時讓
    Gemini wrapper 套自家預設 512；傳 ``0`` 明確關閉 thinking；正整數為上限。

    L1（ADR-070）：runtime group 在 :data:`L1_CUTOVER_GROUPS` 且 model 是 Claude 時走
    ``agent_sdk.run_text``。model 原樣（含別名）交給 SDK；``max_tokens`` 經
    ``CLAUDE_CODE_MAX_OUTPUT_TOKENS``；``temperature`` 丟掉（SDK 不支援，D2）。
    """
    agent = get_current_agent()
    if model is None:
        model = get_model(agent=agent, task=task)
    if _route_to_l1(model):
        return _ask_l1(prompt, system=system, model=model, max_tokens=max_tokens)
    model = api_model_id(model)

    provider = get_provider(model)

    if provider == "anthropic":
        return ask_claude(
            prompt,
            system=system,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            auth_policy=get_auth_policy(agent=agent, task=task),
        )
    if provider == "xai":
        from shared.xai_client import ask_grok

        return ask_grok(
            prompt,
            system=system,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
        )
    if provider == "google":
        from shared.gemini_client import ask_gemini

        extra: dict = {}
        if thinking_budget is not None:
            extra["thinking_budget"] = thinking_budget
        return ask_gemini(
            prompt,
            system=system,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            **extra,
        )
    if provider == "openai":
        # OpenAI 沒有原生 client（無 SDK 接線）；只在 OpenRouter transport 下可用
        # （BYOK 消化既有 OpenAI credit）。native 時 fail loud，不 silent。
        if openrouter_enabled():
            from shared.openrouter_client import ask_openrouter

            return ask_openrouter(
                prompt,
                system=system,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
            )
        raise NotImplementedError(
            f"OpenAI model '{model}' 只在 LLM_TRANSPORT=openrouter 時可用"
            f"（無原生 OpenAI SDK；BYOK 經 OpenRouter）。"
        )
    raise NotImplementedError(
        f"Provider '{provider}' (model={model}) not yet wired. "
        f"Add a wrapper to shared/ and dispatch here."
    )


def ask_multi(
    messages: list[dict],
    *,
    system: str = "",
    model: str | None = None,
    task: str = "default",
    max_tokens: int = 4096,
    temperature: float | None = None,
    thinking_budget: int | None = None,
) -> str:
    """多回合版本。messages 用兩家共通的 OpenAI/Anthropic 欄位（role/content）。

    Provider 差異都在 wrapper 層處理：Anthropic 不吃 role="system" in messages（走
    ``system`` 參數），xAI 兩種都吃，Gemini wrapper 也會把 role="system" 抽出來
    併進 system_instruction。caller 用共通格式即可，不需自己分支。

    ``thinking_budget`` 僅對 Gemini 生效（其他 provider 忽略）。

    L1（ADR-070）：同 :func:`ask` 的切換條件；``messages`` 用
    ``agent_sdk.flatten_messages`` 攤平成單一 prompt（沿用 ``claude -p`` 路徑的做法）。
    """
    agent = get_current_agent()
    if model is None:
        model = get_model(agent=agent, task=task)
    if _route_to_l1(model):
        from shared.agent_sdk import flatten_messages  # noqa: PLC0415

        return _ask_l1(
            flatten_messages(messages), system=system, model=model, max_tokens=max_tokens
        )
    model = api_model_id(model)

    provider = get_provider(model)

    if provider == "anthropic":
        return ask_claude_multi(
            messages,
            system=system,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            auth_policy=get_auth_policy(agent=agent, task=task),
        )
    if provider == "xai":
        from shared.xai_client import ask_grok_multi

        return ask_grok_multi(
            messages,
            system=system,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
        )
    if provider == "google":
        from shared.gemini_client import ask_gemini_multi

        extra: dict = {}
        if thinking_budget is not None:
            extra["thinking_budget"] = thinking_budget
        return ask_gemini_multi(
            messages,
            system=system,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            **extra,
        )
    if provider == "openai":
        if openrouter_enabled():
            from shared.openrouter_client import ask_openrouter_multi

            return ask_openrouter_multi(
                messages,
                system=system,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
            )
        raise NotImplementedError(
            f"OpenAI model '{model}' 只在 LLM_TRANSPORT=openrouter 時可用"
            f"（無原生 OpenAI SDK；BYOK 經 OpenRouter）。"
        )
    raise NotImplementedError(f"Provider '{provider}' (model={model}) not yet wired for ask_multi.")


def ask_with_tools(
    messages: list[dict],
    tools: list[dict],
    *,
    system: str = "",
    model: str | None = None,
    task: str = "tool_use",
    max_tokens: int = 2048,
    tool_choice: dict | None = None,
) -> "anthropic.types.Message":
    """tool-use API：回傳完整 Message（含 stop_reason、content blocks）以驅動 agent loop。

    ``model=None`` 時走 router ``task="tool_use"``（預設 Haiku 4.5）。

    ``tool_choice`` 用於強制 Claude 呼叫特定 tool（例如確保結構化 JSON 輸出）：
    ``{"type": "tool", "name": "my_tool"}`` 強制呼叫，``None`` 讓 Claude 自行決定。

    目前只 Anthropic 有 production-ready 的 tool-use 流程。Grok / Gemini
    各有 tool-use 但 schema / 回傳語意不同，這層 facade 暫不混淆 — 改
    其他 provider 時請補 dispatch 並對齊 stop_reason / content block 形狀。
    """
    agent = get_current_agent()
    if model is None:
        model = get_model(agent=agent, task=task)
    model = api_model_id(model)

    provider = get_provider(model)

    if provider == "anthropic":
        return call_claude_with_tools(
            messages,
            tools,
            system=system,
            model=model,
            max_tokens=max_tokens,
            tool_choice=tool_choice,
            auth_policy=get_auth_policy(agent=agent, task=task),
        )
    raise NotImplementedError(
        f"ask_with_tools 目前只支援 anthropic（收到 provider='{provider}', "
        f"model='{model}'）。其他 provider 的 tool-use schema 不同，請補 "
        f"dispatch 並對齊 Message 形狀。"
    )


def ask_with_audio(
    audio_path: str | Path,
    prompt: str,
    *,
    response_schema: type[BaseModel] | None = None,
    model: str = "gemini-2.5-pro",
    system: str = "",
    temperature: float = 0.2,
    max_output_tokens: int = 8192,
    thinking_budget: int | None = 512,
) -> Any:
    """音檔 + prompt → 純文字（或 parsed BaseModel）。

    目前只 Gemini 支援多模態音訊輸入。其他 provider 的 audio API 形狀不同
    （OpenAI Whisper 是純 transcription、Anthropic 沒有原生 audio），所以
    facade 拋 ``NotImplementedError`` 而非偽裝 unified — 真的要支援其他
    provider 時請補 dispatch 並對齊 response 形狀。

    Args:
        response_schema: 給則回傳 parsed BaseModel 實例，沒給則回傳純文字。
    """
    provider = get_provider(model)

    if provider == "google":
        from shared.gemini_client import ask_gemini_audio

        return ask_gemini_audio(
            audio_path,
            prompt,
            response_schema=response_schema,
            model=model,
            system=system,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            thinking_budget=thinking_budget,
        )
    raise NotImplementedError(
        f"ask_with_audio 目前只支援 google/Gemini（收到 provider='{provider}', model='{model}'）。"
    )
