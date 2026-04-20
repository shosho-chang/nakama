"""Cross-provider LLM facade — 把 (agent, task) 路由到對的 provider wrapper。

呼叫端只需要 `shared.llm.ask(prompt, ...)`，不用自己判斷 Claude / Grok / Gemini。
router 決定 model ID → provider，facade 再 dispatch 到對應的 `shared/*_client.py`。

目前 coverage：Anthropic + xAI + Google。OpenAI 等下個步驟加。
"""

from __future__ import annotations

from shared.anthropic_client import _local, ask_claude, ask_claude_multi
from shared.llm_router import get_model, get_provider


def ask(
    prompt: str,
    *,
    system: str = "",
    model: str | None = None,
    max_tokens: int = 4096,
    temperature: float | None = None,
    thinking_budget: int | None = None,
) -> str:
    """送一次 LLM 請求，自動依 (agent, model) 路由到對的 provider。

    - `model=None` → 依 thread-local agent 從 router 解析
    - Claude ID → 走 `shared.anthropic_client.ask_claude`
    - Grok ID → 走 `shared.xai_client.ask_grok`
    - Gemini ID → 走 `shared.gemini_client.ask_gemini`

    其他 provider 尚未實作，會拋 `NotImplementedError`，讓 caller 明確看到
    缺什麼（避免 router silent 回預設那種不透明的 fallback）。

    `thinking_budget` 僅對 Gemini 生效（其他 provider 忽略）。傳 `None` 時讓
    Gemini wrapper 套自家預設 512；傳 `0` 明確關閉 thinking；正整數為上限。
    """
    if model is None:
        model = get_model(agent=getattr(_local, "agent", None), task="default")

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
    max_tokens: int = 4096,
    temperature: float | None = None,
    thinking_budget: int | None = None,
) -> str:
    """多回合版本。messages 用兩家共通的 OpenAI/Anthropic 欄位（role/content）。

    Provider 差異都在 wrapper 層處理：Anthropic 不吃 role="system" in messages（走
    `system` 參數），xAI 兩種都吃，Gemini wrapper 也會把 role="system" 抽出來
    併進 system_instruction。caller 用共通格式即可，不需自己分支。

    `thinking_budget` 僅對 Gemini 生效（其他 provider 忽略）。
    """
    if model is None:
        model = get_model(agent=getattr(_local, "agent", None), task="default")

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
