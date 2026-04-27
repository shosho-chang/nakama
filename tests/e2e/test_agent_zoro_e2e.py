"""Zoro brainstorm-scout E2E happy path (Phase 6 Slice 4).

End-to-end: trends signal → velocity → relevance (LLM) → novelty → cooldown
→ pick best → Slack DM published → ``pushed_topics.record`` written.

What is mocked:
- ``agents.zoro.trends_api.discover_trending_health`` — returns one signal
  that beats velocity threshold and matches a 睡眠 keyword seed.
- ``shared.llm.ask`` (called inside ``_llm_judge_relevance``) — returns JSON
  with score=0.85 so the topic clears the relevance gate.
- ``slack_sdk.WebClient`` — chat_postMessage returns a fake ts; no Slack call.

What is NOT mocked:
- velocity_gate / signals_to_topics / keyword_prefilter logic
- novelty_gate / cooldown_gate (against ``isolated_db`` autouse tmp DB)
- pick_best_topic ranking
- ``pushed_topics.record`` write path

Marker: none. Runs on every CI invocation.
"""

from __future__ import annotations

import pytest

from shared import pushed_topics
from tests.e2e._slice4_helpers import (
    fake_trending_health,
    fake_zoro_ask,
    make_fake_slack_client,
)


@pytest.fixture
def zoro_e2e_mocks(monkeypatch):
    """Patch trends source + LLM (judge + compose) + WebClient at their use sites.

    WebClient is lazy-imported inside ``publish_to_slack`` (``from slack_sdk
    import WebClient``), so we patch ``slack_sdk.WebClient`` (the source) not
    a re-export attribute on brainstorm_scout — see
    ``feedback_pytest_monkeypatch_where_used.md``.
    """
    monkeypatch.setattr("agents.zoro.trends_api.discover_trending_health", fake_trending_health)
    monkeypatch.setattr("agents.zoro.brainstorm_scout.ask", fake_zoro_ask)

    fake_client = make_fake_slack_client()

    def fake_webclient_factory(token: str):  # match real WebClient(token=...) signature
        return fake_client

    monkeypatch.setattr("slack_sdk.WebClient", fake_webclient_factory)
    return fake_client


def test_zoro_scout_happy_path_publishes_and_records(zoro_e2e_mocks):
    """Full pipeline: trends → judge → publish → record."""
    from agents.zoro.brainstorm_scout import run

    best = run(
        llm_judge=True,
        publish=True,
        record=True,
        channel="C_TEST_BRAINSTORM",
        bot_token="xoxb-test-token",
    )

    # Pipeline returned a Topic — happy path
    assert best is not None
    assert best.title == "sleep cycle stages"
    assert best.velocity_score >= 30.0  # cleared velocity gate
    assert best.relevance_score >= 0.5  # cleared relevance gate (LLM judge gave 0.85)
    assert best.domain == "睡眠"

    # Slack DM was attempted exactly once
    zoro_e2e_mocks.chat_postMessage.assert_called_once()
    _, kw = zoro_e2e_mocks.chat_postMessage.call_args
    assert kw["channel"] == "C_TEST_BRAINSTORM"
    assert "sleep cycle stages" in kw["text"]

    # pushed_topics row landed in the isolated DB
    assert pushed_topics.is_on_cooldown("zoro", best.normalized_keywords)


def test_zoro_scout_dryrun_no_publish_no_record(monkeypatch):
    """publish=False + record=False — no Slack call, no DB write."""
    monkeypatch.setattr("agents.zoro.trends_api.discover_trending_health", fake_trending_health)
    monkeypatch.setattr("agents.zoro.brainstorm_scout.ask", fake_zoro_ask)

    from agents.zoro.brainstorm_scout import run

    best = run(llm_judge=True, publish=False, record=False)

    assert best is not None
    assert best.title == "sleep cycle stages"
    # No record → not on cooldown
    assert not pushed_topics.is_on_cooldown("zoro", best.normalized_keywords)
