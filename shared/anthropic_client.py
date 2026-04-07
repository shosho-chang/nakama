"""Anthropic Claude API wrapper。"""

import os

import anthropic


_client: anthropic.Anthropic | None = None


def get_client() -> anthropic.Anthropic:
    """取得或建立 Anthropic client（singleton）。"""
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _client


def ask_claude(
    prompt: str,
    *,
    system: str = "",
    model: str = "claude-sonnet-4-20250514",
    max_tokens: int = 4096,
    temperature: float = 0.3,
) -> str:
    """送出一次 Claude API 請求，回傳純文字回應。"""
    client = get_client()

    messages = [{"role": "user", "content": prompt}]

    kwargs: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": messages,
    }
    if system:
        kwargs["system"] = system

    response = client.messages.create(**kwargs)
    return response.content[0].text
