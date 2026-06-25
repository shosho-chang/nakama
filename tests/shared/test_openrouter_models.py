"""shared.openrouter_models slug map 單元測試（純函數，不打網路）。"""

from __future__ import annotations

import pytest

from shared.openrouter_models import UnmappedModelError, to_openrouter_slug


@pytest.mark.parametrize(
    "bare,slug",
    [
        ("claude-opus-4-8", "anthropic/claude-opus-4.8"),
        ("claude-opus-4-7", "anthropic/claude-opus-4.7"),
        ("claude-sonnet-4-6", "anthropic/claude-sonnet-4.6"),
        ("claude-sonnet-4-5-20250929", "anthropic/claude-sonnet-4.5"),
        ("claude-haiku-4-5", "anthropic/claude-haiku-4.5"),
        # dated pin 收斂到同一個 canonical slug（多對一）
        ("claude-haiku-4-5-20251001", "anthropic/claude-haiku-4.5"),
        ("gemini-2.5-pro", "google/gemini-2.5-pro"),
        ("gpt-5", "openai/gpt-5"),
        ("gpt-5-mini", "openai/gpt-5-mini"),
    ],
)
def test_known_slugs_resolve(bare, slug):
    assert to_openrouter_slug(bare) == slug


def test_grok_raises_stays_native():
    """grok-* 明確 raise（carve-out：OpenRouter 不載此 tier，xAI 留原生）。"""
    with pytest.raises(UnmappedModelError, match="原生|grok"):
        to_openrouter_slug("grok-4-fast-non-reasoning")
    with pytest.raises(UnmappedModelError):
        to_openrouter_slug("grok-4-fast")


def test_unknown_model_raises():
    with pytest.raises(UnmappedModelError):
        to_openrouter_slug("totally-unknown-model-9000")


def test_unmapped_is_value_error():
    """UnmappedModelError 必須是 ValueError 子類（既有 catch ValueError 語意不變）。"""
    assert issubclass(UnmappedModelError, ValueError)


def test_every_known_model_resolves_except_grok():
    """KNOWN_MODELS 每個非 grok 的 bare ID 都要能翻 slug（避免上線才發現某 slug 漏建）；
    grok-* 確認 raise（明確 carve-out）。這是 plan Slice 4 驗收的前移保護。"""
    from shared.llm_router import KNOWN_MODELS

    for m in KNOWN_MODELS:
        if m.startswith("grok-"):
            with pytest.raises(UnmappedModelError):
                to_openrouter_slug(m)
        else:
            slug = to_openrouter_slug(m)
            assert "/" in slug, f"{m} → {slug} 不像 OpenRouter slug"
