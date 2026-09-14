"""Bare model ID → OpenRouter slug 對照（D2：registry / env / Bridge 面板全部
保留 bare ID，只在 OpenRouter transport 邊界翻成 ``provider/model`` slug）。

為什麼是顯式 map 而非演算法推導：OpenRouter 的 slug 命名不完全規則（dated pin
會收斂、版本號用點號 ``4.6`` 而非 ``4-6``），且某些 provider / tier 根本不在
OpenRouter 上（xAI 的 ``grok-4-fast`` 系列）。顯式 map 讓「查無即 fail-fast」
同時涵蓋這兩種情況，不會 silent 404，也不會 silent 換成別的模型。新增可選
model 時在這裡加一行（對齊 ``shared/llm_router.KNOWN_MODELS`` 的維護點）。

Slug 來源：2026-06-25 對 ``https://openrouter.ai/api/v1/models`` 的 preflight
逐一驗證（339 個 slug 全表比對）。
"""

from __future__ import annotations

# bare ID（Nakama 內部 registry / env 用）→ OpenRouter slug（provider/model）。
# 多對一是正常的：dated pin 與 canonical 收斂到同一個 OpenRouter slug
# （例：claude-haiku-4-5 與 claude-haiku-4-5-20251001 都是 anthropic/claude-haiku-4.5）。
_SLUG_MAP: dict[str, str] = {
    # ── Anthropic ──────────────────────────────────────────────────────────
    # Claude 5 家族（2026-08-19 對 OpenRouter /models API 逐一驗證過）
    "claude-opus-5": "anthropic/claude-opus-5",
    "claude-sonnet-5": "anthropic/claude-sonnet-5",
    "claude-fable-5": "anthropic/claude-fable-5",
    # 舊 ID 保留映射（KNOWN_MODELS 已下架，但既有 env/override 指著仍可解析）
    "claude-opus-4-8": "anthropic/claude-opus-4.8",
    "claude-opus-4-7": "anthropic/claude-opus-4.7",
    "claude-sonnet-4-6": "anthropic/claude-sonnet-4.6",
    "claude-sonnet-4-5-20250929": "anthropic/claude-sonnet-4.5",
    "claude-haiku-4-5": "anthropic/claude-haiku-4.5",
    "claude-haiku-4-5-20251001": "anthropic/claude-haiku-4.5",
    # ── Google ─────────────────────────────────────────────────────────────
    "gemini-2.5-pro": "google/gemini-2.5-pro",
    "gemini-2.5-flash": "google/gemini-2.5-flash",
    # ── OpenAI（Slice 4 會一併進 KNOWN_MODELS；transport map 先備好）─────────
    "gpt-5": "openai/gpt-5",
    "gpt-5-mini": "openai/gpt-5-mini",
    # GPT-5.6 Terra — balanced tier（2026-08-17 起可用），Franky / memory
    # reflection 換 OpenAI transport 用的預設選擇。
    "gpt-5.6-terra": "openai/gpt-5.6-terra",
}


class UnmappedModelError(ValueError):
    """Bare ID 無對應的 OpenRouter slug — fail fast，不讓 OpenRouter 回模糊 404。

    繼承 ``ValueError`` 讓既有 catch ``ValueError`` 的呼叫端語意不變。
    """


def to_openrouter_slug(bare_id: str) -> str:
    """把 Nakama 內部 bare model ID 轉成 OpenRouter slug。

    查無 → ``raise UnmappedModelError``（fail fast）。``grok-*`` 明確不支援：
    OpenRouter 不載 xAI 的 ``grok-4-fast`` tier（後台只有 ``grok-4.x``），故所有
    xAI 呼叫在 facade seam 會留在原生 ``shared.xai_client``，不進 OpenRouter
    （見 Slice 2 + ADR）。這支同時扮演 ``shared.xai_client._require_grok_model``
    那種 fail-fast guard 的角色（錯的 model 在送網路前就擋下）。
    """
    slug = _SLUG_MAP.get(bare_id)
    if slug is not None:
        return slug
    if bare_id.startswith("grok-"):
        raise UnmappedModelError(
            f"'{bare_id}' 不經 OpenRouter：xAI grok 系列在 OpenRouter 無對應 tier"
            f"（後台只有 grok-4.x），故 xAI 呼叫留原生 shared.xai_client。"
        )
    raise UnmappedModelError(
        f"No OpenRouter slug for bare model id '{bare_id}'. "
        f"Add it to shared/openrouter_models._SLUG_MAP (known: {sorted(_SLUG_MAP)})."
    )


__all__ = ["UnmappedModelError", "to_openrouter_slug"]
