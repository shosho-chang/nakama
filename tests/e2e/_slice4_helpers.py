"""Phase 6 Slice 4 E2E test helpers — agent happy-path mocks.

Imported explicitly by `test_agent_brook_e2e.py` / `test_agent_zoro_e2e.py`
(not autouse) so other E2E tests in this folder (`test_phase1_publish_flow.py`)
keep their existing live_wp / spec contracts untouched.

Mock fns use real signatures (per `feedback_test_realism.md`) — no
`lambda **kw` shortcut. SDK clients use `spec=Class` per
`feedback_mock_use_spec.md`.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Brook compose — fixed AST-shaped LLM payload
# ---------------------------------------------------------------------------


def brook_llm_response_json() -> str:
    """LLM-shaped JSON that ``compose_and_enqueue`` parses.

    Matches the shape produced by ``_parse_llm_output``: top-level metadata
    fields + ``blocks`` list of ``BlockNodeV1`` dicts. Lengths chosen to clear
    every ``constr`` constraint in DraftV1 (title 5-120, excerpt 20-300,
    meta_description 50-155, focus_keyword 2-60).
    """
    payload = {
        "title": "Brook E2E happy path test article",
        "slug_candidates": ["brook-e2e-happy-path"],
        "excerpt": "An excerpt of at least twenty characters present here for the test.",
        "secondary_categories": [],
        "tags": ["sleep"],
        "focus_keyword": "brook-e2e",
        "meta_description": (
            "A meta description that is at least fifty chars long to pass validator."
        ),
        "blocks": [
            {"block_type": "paragraph", "content": "First paragraph of the test article."},
            {"block_type": "paragraph", "content": "Second paragraph follows here."},
        ],
    }
    return json.dumps(payload, ensure_ascii=False)


def fake_ask_multi(
    messages: list[dict[str, Any]],
    *,
    system: str | None = None,
    model: str | None = None,
    max_tokens: int = 8192,
    temperature: float = 0.4,
    **_extra: Any,
) -> str:
    """Stand-in for ``shared.llm.ask_multi``.

    Mirrors the real signature so kwargs the caller passes are validated by
    Python at call time — typo defense.
    """
    return brook_llm_response_json()


# ---------------------------------------------------------------------------
# Zoro relevance — LLM judge + Slack publish + signal source
# ---------------------------------------------------------------------------


def fake_zoro_ask(
    prompt: str = "",
    *,
    system: str | None = None,
    max_tokens: int = 256,
    **_extra: Any,
) -> str:
    """Stand-in for ``shared.llm.ask`` covering Zoro's two call sites:

    1. Relevance judge (``prompts/zoro/scout.md``) — returns score+domain+reason JSON.
       System prompt opens with "你是 Zoro 的**相關性判準子系統**".
    2. compose_message (``prompts/zoro/compose_message.md``) — returns
       natural-language Slack text containing the topic title.
       System prompt opens with "你是 Zoro（索隆）".

    Dispatch on **stable system-prompt content** (the role-identity sentence is
    edit-rare). Falls back on max_tokens if both keywords are absent so a
    future caller without a known prompt still gets a safe default.
    """
    sys_text = system or ""
    if "判準" in sys_text:
        return json.dumps({"score": 0.85, "domain": "睡眠", "reason": "fake judge"})
    if "索隆" in sys_text or max_tokens >= 500:
        first_line = prompt.splitlines()[0] if prompt else ""
        return f"關注趨勢：{first_line}\n\n@Sanji @Robin 看怎麼想？"
    # Unknown caller — return judge JSON (cheap fail; relevance gate will skip
    # the topic if score doesn't parse). Loud-fail variant would assert here.
    return json.dumps({"score": 0.0, "domain": None, "reason": "unknown caller"})


def fake_trending_health() -> list[dict[str, Any]]:
    """Stand-in for ``agents.zoro.trends_api.discover_trending_health``.

    One signal that:
    - Beats the velocity threshold (DEFAULT_MIN_VELOCITY=30)
    - Hits the keyword pre-filter (`sleep` is in the 睡眠 seeds)
    - Has clean topic text the LLM judge will accept
    """
    return [
        {
            "title": "sleep cycle stages",
            "velocity_score": 80.0,
            "volume": 12000,
            "score": 250,  # growth_pct
            "related": ["REM cycle", "circadian"],
        },
    ]


def make_fake_slack_client():
    """Mock slack_sdk.WebClient with chat_postMessage returning a fake ts."""

    def fake_resp_get(key: str, default: Any = None) -> Any:
        return "1234567890.000001" if key == "ts" else default

    client = MagicMock()
    client.chat_postMessage = MagicMock(return_value=MagicMock(get=fake_resp_get))
    return client
