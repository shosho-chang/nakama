"""Tests for shared.project_reviews — LLM dispatch is mocked."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from shared.project_reviews import (
    PERSONAS,
    PROMPT_VERSION,
    ProjectReviewError,
    ReviewResult,
    get_prompt_for_persona,
    review_hook,
    review_script,
)


class TestPrompts:
    def test_storyteller_prompt_has_rubric_and_lang_guard(self):
        prompt = get_prompt_for_persona("storyteller")
        assert "rubric" in prompt.lower() or "1 = " in prompt
        assert "繁體中文" in prompt
        assert "簡體字" in prompt
        # Few-shot present (per panel push)
        assert "範例 INPUT" in prompt or "範例 OUTPUT" in prompt
        # Placeholder for content substitution
        assert "{content}" in prompt

    def test_coach_prompt_has_rubric_and_lang_guard(self):
        prompt = get_prompt_for_persona("coach")
        assert "1 = " in prompt
        assert "繁體中文" in prompt
        assert "{content}" in prompt
        assert "範例" in prompt

    def test_personas_constant(self):
        assert PERSONAS == ("storyteller", "coach")

    def test_unknown_persona_raises(self):
        with pytest.raises(ValueError):
            get_prompt_for_persona("totally-bogus")


class TestReviewHook:
    def test_happy_path(self):
        canned = json.dumps(
            {
                "score": 4,
                "summary": "認知落差明顯，數字具體，結尾埋鉤子。",
                "suggestions": ["第一句再精簡", "數字後加情境對比"],
            },
            ensure_ascii=False,
        )
        with patch("shared.project_reviews.ask_claude", return_value=canned):
            result = review_hook("你以為咖啡只是醒腦？……")
        assert isinstance(result, ReviewResult)
        assert result.score == 4
        assert "認知落差" in result.summary
        assert result.suggestions == ["第一句再精簡", "數字後加情境對比"]
        assert result.prompt_version == PROMPT_VERSION
        # run_at is ISO 8601 with TZ
        assert "T" in result.run_at and ("+" in result.run_at or "Z" in result.run_at)

    def test_strips_code_fence(self):
        canned = (
            "```json\n"
            + json.dumps({"score": 3, "summary": "中等", "suggestions": []}, ensure_ascii=False)
            + "\n```"
        )
        with patch("shared.project_reviews.ask_claude", return_value=canned):
            result = review_hook("某 hook 內容")
        assert result.score == 3

    def test_empty_hook_rejected(self):
        with pytest.raises(ProjectReviewError, match="empty"):
            review_hook("   \n\n  ")

    def test_score_out_of_range_rejected(self):
        canned = json.dumps({"score": 7, "summary": "x", "suggestions": []})
        with patch("shared.project_reviews.ask_claude", return_value=canned):
            with pytest.raises(ProjectReviewError, match="out of range"):
                review_hook("某 hook")

    def test_missing_score_rejected(self):
        canned = json.dumps({"summary": "x", "suggestions": []})
        with patch("shared.project_reviews.ask_claude", return_value=canned):
            with pytest.raises(ProjectReviewError, match="score"):
                review_hook("某 hook")

    def test_empty_summary_rejected(self):
        canned = json.dumps({"score": 3, "summary": "", "suggestions": []})
        with patch("shared.project_reviews.ask_claude", return_value=canned):
            with pytest.raises(ProjectReviewError, match="summary"):
                review_hook("某 hook")

    def test_non_json_rejected(self):
        with patch("shared.project_reviews.ask_claude", return_value="just some prose"):
            with pytest.raises(ProjectReviewError, match="non-JSON"):
                review_hook("某 hook")

    def test_suggestions_capped_at_10(self):
        canned = json.dumps(
            {
                "score": 4,
                "summary": "ok",
                "suggestions": [f"建議 {i}" for i in range(20)],
            },
            ensure_ascii=False,
        )
        with patch("shared.project_reviews.ask_claude", return_value=canned):
            result = review_hook("某 hook")
        assert len(result.suggestions) == 10

    def test_simplified_chinese_leakage_rejected(self):
        # 3+ distinct simplified sentinels trigger guard
        canned = json.dumps(
            {
                "score": 4,
                "summary": "这个简体字汉字应该被实时拒绝。",
                "suggestions": [],
            },
            ensure_ascii=False,
        )
        with patch("shared.project_reviews.ask_claude", return_value=canned):
            with pytest.raises(ProjectReviewError, match="simplified"):
                review_hook("某 hook")

    def test_traditional_chinese_passes(self):
        canned = json.dumps(
            {
                "score": 4,
                "summary": "這是正體中文回應，沒有簡體字混入。",
                "suggestions": ["建議一", "建議二"],
            },
            ensure_ascii=False,
        )
        with patch("shared.project_reviews.ask_claude", return_value=canned):
            result = review_hook("某 hook")
        assert result.score == 4


class TestReviewScript:
    def test_happy_path(self):
        canned = json.dumps(
            {
                "score": 2,
                "summary": "句長單一，jargon 密度高。",
                "suggestions": ["插入短句呼吸", "白話 jargon"],
            },
            ensure_ascii=False,
        )
        with patch("shared.project_reviews.ask_claude", return_value=canned):
            result = review_script("肌酸是一種含氮有機酸……")
        assert result.score == 2
        assert "句長" in result.summary

    def test_empty_script_rejected(self):
        with pytest.raises(ProjectReviewError, match="empty"):
            review_script("")


class TestSerialization:
    def test_as_dict_round_trip(self):
        result = ReviewResult(
            run_at="2026-05-24T22:00:00+08:00",
            prompt_version=PROMPT_VERSION,
            score=4,
            summary="ok",
            suggestions=["a", "b"],
        )
        d = result.as_dict()
        assert d == {
            "run_at": "2026-05-24T22:00:00+08:00",
            "prompt_version": PROMPT_VERSION,
            "score": 4,
            "summary": "ok",
            "suggestions": ["a", "b"],
        }
