from __future__ import annotations

import json

import pytest

from agents.brook.script_video.transition_cold_read import (
    ColdReadSection,
    cold_read_sections,
    report_as_json,
)

_SECTIONS = (
    ColdReadSection(
        "section-02",
        "原生家庭不是萬用藉口，但它是第一個該查的線索",
        "原生家庭不是牽拖，是第一個線索",
    ),
    ColdReadSection(
        "section-03", "一次情緒底下連著一整串童年經驗，要一顆一顆鬆開", "情緒像粽子，要一顆一顆鬆開"
    ),
)


def _scripted(blind: list[dict], recovery: list[dict]):
    """兩次呼叫依序回傳盲讀與回收判定；同時記下每次收到的 prompt。"""
    seen: list[str] = []

    def ask(prompt: str) -> str:
        seen.append(prompt)
        payload = {"cards": blind} if len(seen) == 1 else {"pairs": recovery}
        return json.dumps(payload, ensure_ascii=False)

    return ask, seen


def test_cards_that_recover_the_section_summary_pass() -> None:
    ask, _ = _scripted(
        [
            {"index": 1, "guess": "猜一", "self_contained": True, "missing": "none"},
            {"index": 2, "guess": "猜二", "self_contained": True, "missing": "none"},
        ],
        [
            {"index": 1, "recovered": True, "why": "none"},
            {"index": 2, "recovered": True, "why": "none"},
        ],
    )

    report = cold_read_sections(_SECTIONS, ask, seed=0)

    assert report.passed
    assert report.failures == ()


def test_card_the_cold_reader_cannot_recover_fails() -> None:
    ask, _ = _scripted(
        [
            {"index": 1, "guess": "看不出來", "self_contained": True, "missing": "none"},
            {"index": 2, "guess": "猜二", "self_contained": True, "missing": "none"},
        ],
        [
            {"index": 1, "recovered": False, "why": "完全沒提到那一節的論點"},
            {"index": 2, "recovered": True, "why": "none"},
        ],
    )

    report = cold_read_sections(_SECTIONS, ask, seed=0)

    assert not report.passed
    assert len(report.failures) == 1
    assert report.failures[0].why == "完全沒提到那一節的論點"


def test_card_whose_referent_is_unfindable_fails_even_when_the_guess_lands() -> None:
    """「不是牽拖，是線索」這種——冷讀者猜對了方向，但那是猜的不是讀的。

    只用回收當判準會放行這張（實測 2026-09-08 蘇予昕舊版），所以 `self_contained`
    也是 gate 的一部分。
    """
    ask, _ = _scripted(
        [
            {"index": 1, "guess": "猜一", "self_contained": False, "missing": "主詞"},
            {"index": 2, "guess": "猜二", "self_contained": True, "missing": "none"},
        ],
        [
            {"index": 1, "recovered": True, "why": "none"},
            {"index": 2, "recovered": True, "why": "none"},
        ],
    )

    report = cold_read_sections(_SECTIONS, ask, seed=0)

    assert not report.passed
    assert report.failures[0].missing == "主詞"


def test_blind_pass_never_sees_the_section_summary() -> None:
    """盲讀看得到 summary 就不是冷讀了——它會照抄。"""
    ask, seen = _scripted(
        [
            {"index": 1, "guess": "猜一", "self_contained": True, "missing": "none"},
            {"index": 2, "guess": "猜二", "self_contained": True, "missing": "none"},
        ],
        [
            {"index": 1, "recovered": True, "why": "none"},
            {"index": 2, "recovered": True, "why": "none"},
        ],
    )

    cold_read_sections(_SECTIONS, ask, seed=0)

    blind_prompt = seen[0]
    for section in _SECTIONS:
        assert section.transition_title in blind_prompt
        assert section.summary not in blind_prompt


def test_recovery_pass_never_sees_the_card_text() -> None:
    """回收判定看得到卡片，就會回頭替盲讀答案合理化。"""
    ask, seen = _scripted(
        [
            {"index": 1, "guess": "猜一", "self_contained": True, "missing": "none"},
            {"index": 2, "guess": "猜二", "self_contained": True, "missing": "none"},
        ],
        [
            {"index": 1, "recovered": True, "why": "none"},
            {"index": 2, "recovered": True, "why": "none"},
        ],
    )

    cold_read_sections(_SECTIONS, ask, seed=0)

    recovery_prompt = seen[1]
    for section in _SECTIONS:
        assert section.summary in recovery_prompt
        assert section.transition_title not in recovery_prompt


def test_report_comes_back_in_section_order_not_shuffled_order() -> None:
    ask, _ = _scripted(
        [
            {"index": 1, "guess": "猜一", "self_contained": True, "missing": "none"},
            {"index": 2, "guess": "猜二", "self_contained": True, "missing": "none"},
        ],
        [
            {"index": 1, "recovered": True, "why": "none"},
            {"index": 2, "recovered": True, "why": "none"},
        ],
    )

    report = cold_read_sections(_SECTIONS, ask, seed=1)

    assert [card.section_id for card in report.cards] == ["section-02", "section-03"]


def test_worker_that_skips_a_card_is_an_error_not_a_silent_pass() -> None:
    ask, _ = _scripted(
        [{"index": 1, "guess": "猜一", "self_contained": True, "missing": "none"}],
        [{"index": 1, "recovered": True, "why": "none"}],
    )

    with pytest.raises(ValueError, match="漏答"):
        cold_read_sections(_SECTIONS, ask, seed=0)


def test_no_transition_cards_is_a_pass_without_calling_the_worker() -> None:
    def ask(prompt: str) -> str:  # pragma: no cover - 不該被呼叫
        raise AssertionError("沒有卡片就不該呼叫 worker")

    report = cold_read_sections((), ask)

    assert report.passed
    assert json.loads(report_as_json(report)) == {"passed": True, "cards": []}
