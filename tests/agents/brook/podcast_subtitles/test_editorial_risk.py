from __future__ import annotations

from agents.brook.podcast_subtitles.risk import (
    ReconciliationCandidate,
    SpanObservation,
    discover_editorial_risks,
)


def _observation(index: int, text: str) -> SpanObservation:
    return SpanObservation(
        audio_span_ids=(f"span-{index}",),
        candidates=(
            ReconciliationCandidate(
                text=text,
                evidence_ids=(f"evidence-{index}",),
                confidence=0.95,
                speaker_labels=("guest",),
                adapter_id="fixture",
            ),
        ),
    )


def test_balanced_delimiters_across_recognition_tokens_are_not_false_positive() -> None:
    risks = discover_editorial_risks(
        (_observation(1, "請讀《大腦"), _observation(2, "簡史》這本書"))
    )

    assert risks == ()


def test_forbidden_punctuation_maps_back_to_exact_stable_span() -> None:
    risks = discover_editorial_risks((_observation(1, "第一段"), _observation(2, "第二段。")))

    assert len(risks) == 1
    assert risks[0].issue.code == "forbidden_punctuation"
    assert risks[0].audio_span_ids == ("span-2",)
    assert risks[0].evidence_ids == ("evidence-2",)
    assert risks[0].issue.candidates == ("第二段。",)


def test_repeated_forbidden_punctuation_in_one_span_collapses_to_one_issue() -> None:
    risks = discover_editorial_risks((_observation(1, "真的，錯了。"),))

    punctuation = [risk for risk in risks if risk.issue.code == "forbidden_punctuation"]
    assert len(punctuation) == 1
    assert punctuation[0].issue.span_ids == ("span-1",)


def test_simplified_phrase_spanning_tokens_is_review_only_and_fail_closed() -> None:
    observations = (_observation(1, "这"), _observation(2, "是错误"))
    risks = discover_editorial_risks(observations)

    assert {risk.issue.code for risk in risks} == {"simplified_chinese_suspected"}
    assert {span for risk in risks for span in risk.audio_span_ids} == {
        "span-1",
        "span-2",
    }
    assert [candidate.text for candidate in observations[0].candidates] == ["这"]
    assert [candidate.text for candidate in observations[1].candidates] == ["是错误"]


def test_unbalanced_delimiter_can_address_multiple_spans_without_text_mutation() -> None:
    risks = discover_editorial_risks((_observation(1, "《書名"), _observation(2, "」")))

    assert [(risk.issue.code, risk.issue.span_ids) for risk in risks] == [
        ("unbalanced_house_delimiter", ("span-1", "span-2"))
    ]
