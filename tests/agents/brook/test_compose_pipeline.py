"""Tests for agents/brook/compose.py production pipeline (compose_and_enqueue).

LLM 全程 mock；不打真 Anthropic API（feedback_test_api_isolation.md）。
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import pytest

from agents.brook.compose import (
    ComposeOutputParseError,
    _extract_json_object,
    _new_draft_id,
    compose_and_enqueue,
)
from shared import approval_queue
from shared.schemas.approval import ApprovalPayloadV1Adapter

# ---------------------------------------------------------------------------
# LLM response fixtures
# ---------------------------------------------------------------------------


def _valid_llm_response(**overrides: Any) -> str:
    body: dict[str, Any] = {
        "title": "閱讀筆記：《原子習慣》如何把持續 2% 的小事變成長期槓桿",
        "slug_candidates": ["atomic-habits-review", "atomic-habits-takeaway"],
        "excerpt": "這本書教我把每天 1% 的改進累積成長期結果，分享三個實踐筆記。",
        "focus_keyword": "atomic habits",
        "meta_description": (
            "讀完《原子習慣》之後，我整理了三個最打中我的概念，再配合自己 30 天實踐 "
            "結果，給想改變生活節奏的你一個立刻可以動手的起手式。"
        ),
        "secondary_categories": ["personal-development"],
        "tags": ["book-review", "habits"],
        "blocks": [
            {
                "block_type": "heading",
                "attrs": {"level": 2},
                "content": "為什麼我又翻了一次這本書",
                "children": [],
            },
            {
                "block_type": "paragraph",
                "attrs": {},
                "content": ("最近又把《原子習慣》拿出來翻，內容僅供參考，不構成醫療建議。"),
                "children": [],
            },
        ],
    }
    body.update(overrides)
    return json.dumps(body, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _queue_row(queue_row_id: int) -> dict[str, Any]:
    row = approval_queue.get_by_id(queue_row_id)
    assert row is not None
    return row


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_extract_json_strips_markdown_fence():
    raw = '```json\n{"a": 1}\n```'
    assert _extract_json_object(raw) == {"a": 1}


def test_extract_json_finds_embedded_object():
    raw = '這是前言\n\n{"title": "X"}\n後話'
    assert _extract_json_object(raw) == {"title": "X"}


def test_extract_json_raises_when_no_object():
    with pytest.raises(ComposeOutputParseError):
        _extract_json_object("純文字沒有 JSON")


def test_new_draft_id_pattern():
    import re
    from datetime import datetime, timezone

    from shared.schemas.publishing import DraftV1

    did = _new_draft_id(datetime(2026, 4, 23, 12, 34, 56, tzinfo=timezone.utc))
    # DraftV1.draft_id regex
    pattern = DraftV1.model_fields["draft_id"].metadata[0].pattern
    assert re.match(pattern, did), f"draft_id {did!r} 不符 pattern"


def test_compose_and_enqueue_happy_path():
    fake_response = _valid_llm_response()
    with patch("agents.brook.compose.ask_claude_multi", return_value=fake_response) as mock_llm:
        result = compose_and_enqueue(topic="讀書心得：原子習慣", category="book-review")

    assert mock_llm.called
    assert result["category"] == "book-review"
    assert result["draft_id"].startswith("draft_")
    assert result["operation_id"].startswith("op_")
    assert result["compliance_flags"].medical_claim is False
    assert result["compliance_flags"].absolute_assertion is False

    row = _queue_row(result["queue_row_id"])
    assert row["source_agent"] == "brook"
    assert row["status"] == "pending"
    assert row["target_platform"] == "wordpress"
    assert row["target_site"] == "wp_shosho"
    assert row["action_type"] == "publish_post"
    assert row["operation_id"] == result["operation_id"]

    payload = ApprovalPayloadV1Adapter.validate_python(json.loads(row["payload"]))
    assert payload.action_type == "publish_post"
    assert payload.draft.draft_id == result["draft_id"]
    assert payload.draft.primary_category == "book-review"
    assert payload.draft.tags == ["book-review", "habits"]
    assert payload.draft.style_profile_id == "book-review@0.1.0"


def test_auto_category_detection():
    """category=None 時靠 detect_category 依 topic 關鍵字判斷。"""
    fake = _valid_llm_response()
    with patch("agents.brook.compose.ask_claude_multi", return_value=fake):
        result = compose_and_enqueue(topic="這本書帶我看見的三件事")
    assert result["category"] == "book-review"


def test_no_category_and_no_keywords_raises():
    """topic 無任何類別關鍵字 → ValueError（由 caller 決定手動指定）。"""
    with pytest.raises(ValueError, match="無法自動判斷"):
        compose_and_enqueue(topic="一段雜訊文字 quack zoot blob")


def test_medical_claim_flags_propagate_to_payload():
    """LLM 寫進療效詞 → gate flags + DB column + payload 全部 True。"""
    fake = _valid_llm_response(
        blocks=[
            {
                "block_type": "paragraph",
                "attrs": {},
                "content": "實證：這個方法 100% 有效，可以治癒糖尿病。",
                "children": [],
            }
        ]
    )
    with patch("agents.brook.compose.ask_claude_multi", return_value=fake):
        result = compose_and_enqueue(topic="讀書心得：實證科學", category="book-review")

    assert result["compliance_flags"].medical_claim is True
    assert result["compliance_flags"].absolute_assertion is True

    row = _queue_row(result["queue_row_id"])
    payload = ApprovalPayloadV1Adapter.validate_python(json.loads(row["payload"]))
    assert payload.compliance_flags.medical_claim is True
    # reviewer_compliance_ack 起始為 False（等 Bridge HITL 加強審核）
    assert payload.reviewer_compliance_ack is False
    assert row["reviewer_compliance_ack"] == 0


def test_blacklisted_tags_rejected():
    fake = _valid_llm_response(
        tags=["book-review", "cancer-cure", "habits"],
    )
    with patch("agents.brook.compose.ask_claude_multi", return_value=fake):
        result = compose_and_enqueue(topic="讀書心得", category="book-review")

    row = _queue_row(result["queue_row_id"])
    payload = ApprovalPayloadV1Adapter.validate_python(json.loads(row["payload"]))
    assert "cancer-cure" not in payload.draft.tags
    assert ("cancer-cure", "blacklisted") in result["tag_filter_rejected"]


def test_llm_returns_non_json_raises_parse_error():
    with patch(
        "agents.brook.compose.ask_claude_multi",
        return_value="I'm sorry, I cannot comply.",
    ):
        with pytest.raises(ComposeOutputParseError):
            compose_and_enqueue(topic="讀書心得", category="book-review")


def test_empty_blocks_raises_parse_error():
    fake = _valid_llm_response(blocks=[])
    with patch("agents.brook.compose.ask_claude_multi", return_value=fake):
        with pytest.raises(ComposeOutputParseError):
            compose_and_enqueue(topic="讀書心得", category="book-review")


def test_primary_category_override_wins_over_profile():
    fake = _valid_llm_response()
    with patch("agents.brook.compose.ask_claude_multi", return_value=fake):
        result = compose_and_enqueue(
            topic="科普文章",
            category="science",
            primary_category_override="nutrition-science",
        )
    row = _queue_row(result["queue_row_id"])
    payload = ApprovalPayloadV1Adapter.validate_python(json.loads(row["payload"]))
    assert payload.draft.primary_category == "nutrition-science"


def test_scheduled_at_propagated_to_payload():
    from datetime import datetime, timezone

    fake = _valid_llm_response()
    scheduled = datetime(2026, 5, 1, 9, 0, tzinfo=timezone.utc)
    with patch("agents.brook.compose.ask_claude_multi", return_value=fake):
        result = compose_and_enqueue(
            topic="讀書心得",
            category="book-review",
            scheduled_at=scheduled,
        )

    row = _queue_row(result["queue_row_id"])
    payload = ApprovalPayloadV1Adapter.validate_python(json.loads(row["payload"]))
    assert payload.scheduled_at == scheduled


def test_default_tag_hints_used_when_llm_returns_empty_tags():
    fake = _valid_llm_response(tags=[])
    with patch("agents.brook.compose.ask_claude_multi", return_value=fake):
        result = compose_and_enqueue(topic="讀書心得", category="book-review")
    row = _queue_row(result["queue_row_id"])
    payload = ApprovalPayloadV1Adapter.validate_python(json.loads(row["payload"]))
    # book-review.yaml default_tag_hints 包含 "book-review"
    assert "book-review" in payload.draft.tags


def test_llm_title_too_short_wraps_as_parse_error():
    """title < 5 字（DraftV1 下限）ValidationError 必須轉 ComposeOutputParseError。"""
    fake = _valid_llm_response(title="太短")
    with patch("agents.brook.compose.ask_claude_multi", return_value=fake):
        with pytest.raises(ComposeOutputParseError):
            compose_and_enqueue(topic="讀書心得", category="book-review")


def test_llm_missing_required_key_wraps_as_parse_error():
    """LLM 漏 focus_keyword → KeyError 必須轉 ComposeOutputParseError。"""
    body: dict[str, Any] = {
        "title": "閱讀筆記：這本書改變了我的作息節奏與日常",
        "slug_candidates": ["my-book"],
        "excerpt": "短短 30 字的 excerpt，講一下這本書帶來的改變，請嚴格遵守。",
        # focus_keyword intentionally missing
        "meta_description": (
            "讀完之後我整理了三個最打中的觀點，搭配 30 天實踐，"
            "給你一個可以立刻動手的起手式，想看完整版 blog 歡迎訂閱。"
        ),
        "secondary_categories": [],
        "tags": [],
        "blocks": [
            {
                "block_type": "paragraph",
                "attrs": {},
                "content": "正文。",
                "children": [],
            }
        ],
    }
    fake = json.dumps(body, ensure_ascii=False)
    with patch("agents.brook.compose.ask_claude_multi", return_value=fake):
        with pytest.raises(ComposeOutputParseError):
            compose_and_enqueue(topic="讀書心得", category="book-review")


def test_llm_bad_slug_pattern_wraps_as_parse_error():
    """LLM 送 CJK slug → DraftV1.slug_candidates pattern fail → ComposeOutputParseError。"""
    fake = _valid_llm_response(slug_candidates=["這是中文slug"])
    with patch("agents.brook.compose.ask_claude_multi", return_value=fake):
        with pytest.raises(ComposeOutputParseError):
            compose_and_enqueue(topic="讀書心得", category="book-review")
