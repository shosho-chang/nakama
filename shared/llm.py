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
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from shared.anthropic_client import ask_claude, ask_claude_multi, call_claude_with_tools
from shared.llm_context import _local
from shared.llm_router import get_model, get_provider

if TYPE_CHECKING:
    import anthropic
    from pydantic import BaseModel


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
    """
    if model is None:
        model = get_model(agent=getattr(_local, "agent", None), task=task)

    provider = get_provider(model)

    if provider == "anthropic":
        return ask_claude(
            prompt,
            system=system,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
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
    """
    if model is None:
        model = get_model(agent=getattr(_local, "agent", None), task=task)

    provider = get_provider(model)

    if provider == "anthropic":
        return ask_claude_multi(
            messages,
            system=system,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
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
    if model is None:
        model = get_model(agent=getattr(_local, "agent", None), task=task)

    provider = get_provider(model)

    if provider == "anthropic":
        return call_claude_with_tools(
            messages,
            tools,
            system=system,
            model=model,
            max_tokens=max_tokens,
            tool_choice=tool_choice,
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
