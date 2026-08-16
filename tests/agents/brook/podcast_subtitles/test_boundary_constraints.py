from __future__ import annotations

import pytest

from agents.brook.podcast_subtitles.boundary_constraints import (
    BoundaryConstraintError,
    ProtectedTermMetadata,
    assess_boundary_edges,
    derive_protected_token_ranges,
    validate_semantic_non_degeneracy,
)
from agents.brook.podcast_subtitles.profiles import HORIZONTAL_16X9
from shared.schemas.podcast_subtitles_v2 import CanonicalToken, SemanticUnit


def _tokens(texts: list[str], *, speakers: list[str] | None = None) -> tuple[CanonicalToken, ...]:
    labels = speakers or ["guest"] * len(texts)
    return tuple(
        CanonicalToken(
            id=f"t{index}",
            text=text,
            start_ms=index * 200,
            end_ms=(index + 1) * 200,
            speaker=labels[index],
        )
        for index, text in enumerate(texts)
    )


def _units(tokens: tuple[CanonicalToken, ...]) -> tuple[SemanticUnit, ...]:
    units: list[SemanticUnit] = []
    for start in range(0, len(tokens), 2):
        selected = tokens[start : start + 2]
        units.append(
            SemanticUnit(
                id=f"u{start}",
                token_ids=tuple(token.id for token in selected),
                kind="phrase" if len(selected) > 1 else "token",
                strength=0.6 if len(selected) > 1 else 0.2,
            )
        )
    return tuple(units)


def _relations(texts: list[str], *, metadata: tuple[ProtectedTermMetadata, ...] = ()):
    tokens = _tokens(texts)
    protected = derive_protected_token_ranges(tokens, metadata)
    return assess_boundary_edges(
        tokens,
        _units(tokens),
        HORIZONTAL_16X9,
        protected_ranges=protected,
    )


def test_generic_policy_term_metadata_protects_an_exact_spoken_range() -> None:
    edges = _relations(
        list("學業經歷"),
        metadata=(ProtectedTermMetadata("學業經歷", kind="term"),),
    )

    assert [edge.cue_relation for edge in edges] == ["forbidden"] * 3
    assert all("protected_term" in edge.reason_codes for edge in edges)


def test_anji_before_girls_high_school_is_derived_by_bounded_preposition_rule() -> None:
    edges = _relations(list("在女中之前"))

    assert [edge.cue_relation for edge in edges] == ["forbidden"] * 4
    assert all("bounded_preposition_structure" in edge.reason_codes for edge in edges)


def test_generic_term_plus_classifier_phrase_leaves_only_grammatical_edge_cuttable() -> None:
    edges = _relations(
        list("人生真實樣貌的一個展現"),
        metadata=(ProtectedTermMetadata("人生真實樣貌", kind="term"),),
    )

    forbidden = [edge.edge_index for edge in edges if edge.cue_relation == "forbidden"]
    assert forbidden == [1, 2, 3, 4, 5, 7, 8, 9, 10]
    assert edges[5].cue_relation in {"preferred", "discouraged", "neutral"}
    assert "classifier_phrase" in edges[7].reason_codes


def test_anji_terms_are_not_inferred_without_reference_or_semantic_evidence() -> None:
    academic = _relations(list("學業經歷"))
    real_life = _relations(list("人生真實樣貌的一個展現"))

    assert any(edge.cue_relation != "forbidden" for edge in academic)
    assert any(
        edge.edge_index <= 5 and edge.cue_relation != "forbidden" for edge in real_life
    )


@pytest.mark.parametrize(
    ("texts", "reason"),
    [
        (list("3.14mg"), "numeric_decimal"),
        (list("12.5%"), "numeric_percent"),
        (list("3-5公斤"), "numeric_range"),
        (list("2026年8月13日"), "numeric_date_time"),
        (list("v2.1.3"), "numeric_version"),
        (list("三個"), "classifier_phrase"),
    ],
)
def test_numeric_and_measure_expressions_are_atomic(texts: list[str], reason: str) -> None:
    edges = _relations(texts)

    assert all(edge.cue_relation == "forbidden" for edge in edges)
    assert any(reason in edge.reason_codes for edge in edges)


@pytest.mark.parametrize("closed", ["的", "了", "嗎", "把", "被", "在", "跟"])
def test_closed_class_word_cannot_become_an_independent_cue(closed: str) -> None:
    edges = _relations(["前", closed, "後"])

    assert edges[0].cue_relation == "forbidden"
    assert edges[1].cue_relation == "forbidden"
    assert all("closed_class_no_orphan" in edge.reason_codes for edge in edges)


def test_negation_connector_filler_and_self_repair_are_preserved_as_cohesion() -> None:
    negation = _relations(["我", "不", "同意"])
    connector = _relations(["前句", "但是", "後句"])
    filler = _relations(["我", "嗯", "覺得"])
    repair = _relations(["我", "我", "覺得"])

    assert "negation_no_orphan" in negation[1].reason_codes
    assert all("connector_no_orphan" in edge.reason_codes for edge in connector)
    assert all("filler_no_independent_cue" in edge.reason_codes for edge in filler)
    assert "asr_timestamp_gap_not_prosody" in filler[0].reason_codes
    assert "self_repair_cohesion" in repair[0].reason_codes


def test_code_switch_url_and_paired_punctuation_are_atomic() -> None:
    omega = _relations(list("Omega-3"))
    url = _relations(list("https://example.com"))
    quoted = _relations(list("《深度工作》"))

    assert all(edge.cue_relation == "forbidden" for edge in omega)
    assert any("protected_code_switch" in edge.reason_codes for edge in omega)
    assert all(edge.cue_relation == "forbidden" for edge in url)
    assert any("protected_url" in edge.reason_codes for edge in url)
    assert all(edge.cue_relation == "forbidden" for edge in quoted)
    assert any("paired_punctuation" in edge.reason_codes for edge in quoted)


def test_speaker_edge_is_mandatory_and_never_a_semantic_identity() -> None:
    tokens = _tokens(["主持", "來賓"], speakers=["host", "guest"])
    edges = assess_boundary_edges(tokens, _units(tokens), HORIZONTAL_16X9)

    assert edges[0].cue_relation == "mandatory"
    assert edges[0].line_relation == "mandatory"
    assert "speaker_change" in edges[0].reason_codes


def test_unmatched_punctuation_is_material_uncertainty() -> None:
    edges = _relations(["前", "《", "未完"])

    assert any(edge.material_uncertainty for edge in edges)
    assert any("unmatched_punctuation" in edge.reason_codes for edge in edges)


def test_reference_metadata_protects_only_exact_spoken_token_ranges() -> None:
    tokens = _tokens(["訪問", "王", "小", "明", "老師"])
    protected = derive_protected_token_ranges(
        tokens,
        (
            ProtectedTermMetadata(
                "王小明",
                kind="name",
                source="retrieved_reference_metadata",
                reference_evidence_ids=("ref-1",),
                scope_token_ids=("t1", "t2", "t3"),
            ),
            ProtectedTermMetadata(
                "不存在的名字",
                kind="name",
                source="retrieved_reference_metadata",
                reference_evidence_ids=("ref-2",),
                scope_token_ids=tuple(token.id for token in tokens),
            ),
        ),
    )

    assert len(protected) == 1
    assert protected[0].canonical_text == "王小明"
    assert protected[0].reference_evidence_ids == ("ref-1",)


def test_singleton_all_neutral_whole_run_missing_duplicate_and_conflict_fail_closed() -> None:
    tokens = _tokens(list("甲乙丙丁戊己"))
    singletons = tuple(
        SemanticUnit(id=f"s{i}", token_ids=(token.id,), kind="token", strength=1.0)
        for i, token in enumerate(tokens)
    )
    neutral = tuple(
        SemanticUnit(
            id=f"n{i}",
            token_ids=tuple(token.id for token in tokens[i : i + 2]),
            kind="phrase",
            strength=0.0,
        )
        for i in range(0, len(tokens), 2)
    )
    whole = (
        SemanticUnit(
            id="whole",
            token_ids=tuple(token.id for token in tokens),
            kind="sentence",
            strength=1.0,
        ),
    )
    missing = _units(tokens[:-1])
    duplicate = (*_units(tokens), _units(tokens)[0].model_copy(update={"id": "dup"}))
    conflict = (
        *_units(tokens),
        _units(tokens)[0].model_copy(
            update={"id": "conflict", "forbid_cue_breaks": True}
        ),
    )

    for invalid in (singletons, neutral, whole, missing, duplicate, conflict):
        with pytest.raises(BoundaryConstraintError):
            validate_semantic_non_degeneracy(tokens, invalid)
