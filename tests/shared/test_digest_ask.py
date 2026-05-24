"""Tests for shared.digest_ask — LLM-over-vault query."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from shared.digest_ask import (
    DEFAULT_DAYS,
    MAX_CONTEXT_CHARS,
    MAX_DAYS,
    MAX_QUESTION_CHARS,
    AskRequest,
    AskValidationError,
    ask,
    parse_request,
)
from shared.digest_indexer import DigestIndexer


def _today(offset: int = 0) -> str:
    return (datetime.now(ZoneInfo("Asia/Taipei")).date() - timedelta(days=offset)).isoformat()


def _seed(vault: Path, type_dir: str, date_: str, content: str) -> None:
    d = vault / type_dir
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{date_}.md").write_text(
        f"---\ndate: '{date_}'\ntype: digest\n---\n\n# {date_}\n\n{content}\n",
        encoding="utf-8",
    )


class TestParseRequest:
    def test_minimal(self):
        r = parse_request(question="X 是什麼？", days=None, types=None)
        assert r.question == "X 是什麼？"
        assert r.days == DEFAULT_DAYS
        assert r.types == ("pubmed", "ai")

    def test_strips_whitespace(self):
        r = parse_request(question="  hi  ", days=None, types=None)
        assert r.question == "hi"

    def test_empty_question_rejected(self):
        with pytest.raises(AskValidationError, match="輸入問題"):
            parse_request(question="", days=None, types=None)
        with pytest.raises(AskValidationError):
            parse_request(question="   ", days=None, types=None)

    def test_question_too_long(self):
        with pytest.raises(AskValidationError, match="太長"):
            parse_request(question="x" * (MAX_QUESTION_CHARS + 1), days=None, types=None)

    def test_days_parsed(self):
        r = parse_request(question="q", days="7", types=None)
        assert r.days == 7

    def test_days_invalid_int(self):
        with pytest.raises(AskValidationError, match="整數"):
            parse_request(question="q", days="abc", types=None)

    def test_days_out_of_range(self):
        with pytest.raises(AskValidationError):
            parse_request(question="q", days="0", types=None)
        with pytest.raises(AskValidationError):
            parse_request(question="q", days=str(MAX_DAYS + 1), types=None)

    def test_types_filter(self):
        r = parse_request(question="q", days=None, types=["pubmed"])
        assert r.types == ("pubmed",)

    def test_types_unknown_dropped(self):
        r = parse_request(question="q", days=None, types=["pubmed", "podcast"])
        assert r.types == ("pubmed",)

    def test_types_all_unknown_rejected(self):
        with pytest.raises(AskValidationError, match="至少選一種"):
            parse_request(question="q", days=None, types=["podcast"])


class TestAsk:
    def test_no_digests_returns_empty_answer_no_llm_call(self, tmp_path):
        idx = DigestIndexer(tmp_path)
        called = []

        def fake_llm(*a, **kw):
            called.append(1)
            return "should not run"

        req = AskRequest(question="X?", days=7, types=("pubmed",))
        result = ask(req, idx, llm=fake_llm)
        assert called == []
        assert result.answer.startswith("過去 7 天無 digest")
        assert result.sources == ()
        assert result.context_chars == 0
        assert result.truncated is False

    def test_concats_in_scope_digests(self, tmp_path):
        _seed(tmp_path, "KB/Wiki/Digests/PubMed", _today(0), "本日 PubMed")
        _seed(tmp_path, "KB/Wiki/Digests/AI", _today(1), "昨日 AI")
        idx = DigestIndexer(tmp_path)
        captured = {}

        def fake_llm(prompt, *, system, model, max_tokens):
            captured["prompt"] = prompt
            captured["system"] = system
            captured["model"] = model
            return "answer here"

        req = AskRequest(question="今天有什麼？", days=7, types=("pubmed", "ai"))
        result = ask(req, idx, llm=fake_llm)
        assert result.answer == "answer here"
        assert {(s.type, s.date) for s in result.sources} == {
            ("pubmed", _today(0)),
            ("ai", _today(1)),
        }
        assert "本日 PubMed" in captured["prompt"]
        assert "昨日 AI" in captured["prompt"]
        assert f"[pubmed/{_today(0)}]" in captured["prompt"]
        assert "今天有什麼？" in captured["prompt"]
        assert "繁體中文" in captured["system"]

    def test_type_filter_applied(self, tmp_path):
        _seed(tmp_path, "KB/Wiki/Digests/PubMed", _today(0), "P")
        _seed(tmp_path, "KB/Wiki/Digests/AI", _today(0), "A")
        idx = DigestIndexer(tmp_path)

        def fake_llm(prompt, **kw):
            assert "P" in prompt
            assert "A" not in prompt or "AI 每日" not in prompt
            return "ok"

        req = AskRequest(question="?", days=7, types=("pubmed",))
        result = ask(req, idx, llm=fake_llm)
        assert {s.type for s in result.sources} == {"pubmed"}

    def test_context_cap_truncates_oldest(self, tmp_path, monkeypatch):
        # Make each digest huge so two of them exceed the cap.
        big = "X" * 80_000
        for offset in range(3):
            _seed(tmp_path, "KB/Wiki/Digests/PubMed", _today(offset), big)
        idx = DigestIndexer(tmp_path)
        monkeypatch.setattr("shared.digest_ask.MAX_CONTEXT_CHARS", 150_000)

        def fake_llm(prompt, **kw):
            return "answer"

        req = AskRequest(question="q", days=7, types=("pubmed",))
        result = ask(req, idx, llm=fake_llm)
        # Newest first, so older entries get dropped.
        assert result.truncated is True
        assert len(result.sources) < 3
        assert result.context_chars <= 150_000

    def test_passes_default_model(self, tmp_path):
        _seed(tmp_path, "KB/Wiki/Digests/PubMed", _today(0), "hi")
        idx = DigestIndexer(tmp_path)
        captured = {}

        def fake_llm(prompt, **kw):
            captured.update(kw)
            return "ok"

        req = AskRequest(question="q", days=7, types=("pubmed",))
        ask(req, idx, llm=fake_llm)
        assert captured["model"] == "claude-sonnet-4-6"
        assert captured["max_tokens"] == 2048


class TestConstants:
    def test_context_cap_is_reasonable(self):
        # Guardrail: catch accidental cap bump that would explode cost.
        assert MAX_CONTEXT_CHARS <= 500_000
