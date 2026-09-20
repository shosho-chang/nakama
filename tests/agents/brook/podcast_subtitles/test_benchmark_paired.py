from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agents.brook.podcast_subtitles.benchmark import (
    BenchmarkCandidate,
    GateStatus,
    benchmark_suite_hash,
    evaluate_benchmark,
    evaluate_paired_boundary_study,
    load_json_fixture,
    load_paired_boundary_study,
    paired_boundary_candidate_record_hash,
    paired_boundary_mapping_commitment_hash,
    paired_boundary_predeclaration_hash,
    paired_boundary_study_hash,
)
from agents.brook.podcast_subtitles.hashing import hash_object

ANJI_AUDIO_SHA256 = "bc652157b0551769a2e79d565ee10338626db89fc7accd82ec23e2873022ef8a"
FIXTURES = Path(__file__).parents[3] / "fixtures" / "podcast_subtitles_v2"


def _utc(offset_minutes: int) -> str:
    value = datetime(2026, 8, 13, tzinfo=timezone.utc) + timedelta(minutes=offset_minutes)
    return value.isoformat().replace("+00:00", "Z")


def _study_payload(
    *,
    outcomes: tuple[str, ...] = ("b_better",) * 20,
    v2_unacceptable: frozenset[int] = frozenset(),
) -> dict:
    clips = [
        {
            "clip_id": f"clip-{index:02d}",
            "start_ms": index * 10_000,
            "end_ms": index * 10_000 + 8_000,
            "normalized_audio_hash": ANJI_AUDIO_SHA256,
            "audio_clip_hash": hash_object({"clip": index, "audio": ANJI_AUDIO_SHA256}),
            "selection_stratum": f"episode-window-{index:02d}",
        }
        for index in range(len(outcomes))
    ]
    predeclaration = {
        "schema_version": 1,
        "study_id": "anji-paired-boundary-v1",
        "frozen_at_utc": _utc(0),
        "selection_method": "predeclared_nonoverlapping_episode_windows_v1",
        "selection_independent_of_candidates": True,
        "sampling_frame_hash": hash_object({"episode": "anji", "windows": len(clips)}),
        "selection_seed_commitment": hash_object({"seed": "held-by-human-operator"}),
        "minimum_clip_count": 20,
        "minimum_decisive_count": 12,
        "one_sided_alpha": 0.05,
        "minimum_v2_decisive_win_rate": 0.65,
        "maximum_v2_unacceptable_rate": 0.05,
        "clips": clips,
    }
    predeclaration["predeclaration_hash"] = paired_boundary_predeclaration_hash(predeclaration)

    candidates = []
    canonical_content_hash = hash_object({"canonical": "exact-shared-content"})
    token_sequence_hash = hash_object({"tokens": ["exact", "shared", "sequence"]})
    renderer_identity_hash = hash_object({"renderer": "exact-shared-identity"})
    for system, generated_minute in (("v1", 10), ("v2", 11)):
        clip_presentations = [
            {
                "clip_id": clip["clip_id"],
                "presentation_artifact_hash": hash_object(
                    {"system": system, "clip": clip["clip_id"], "view": "all-cues"}
                ),
                "cue_set_hash": hash_object(
                    {"system": system, "clip": clip["clip_id"], "cues": "exact"}
                ),
                "cue_count": 4,
            }
            for clip in clips
        ]
        candidate = {
            "system": system,
            "candidate_id": f"{system}-candidate",
            "generation_id": f"{system}-generation",
            "canonical_content_hash": canonical_content_hash,
            "token_sequence_hash": token_sequence_hash,
            "candidate_artifact_hash": hash_object({"system": system, "artifact": "bytes"}),
            "subtitle_bytes_hash": hash_object({"system": system, "subtitle": "bytes"}),
            "renderer_identity_hash": renderer_identity_hash,
            "normalized_audio_hash": ANJI_AUDIO_SHA256,
            "predeclaration_hash": predeclaration["predeclaration_hash"],
            "generated_at_utc": _utc(generated_minute),
            "clip_presentations": clip_presentations,
        }
        candidate["candidate_record_hash"] = paired_boundary_candidate_record_hash(candidate)
        candidates.append(candidate)

    mapping_entries = []
    for index, clip in enumerate(clips):
        # Alternate the hidden order so presentation position cannot reveal the system.
        a_system, b_system = ("v1", "v2") if index % 2 == 0 else ("v2", "v1")
        by_system = {candidate["system"]: candidate for candidate in candidates}
        mapping_entries.append(
            {
                "clip_id": clip["clip_id"],
                "a_candidate_record_hash": by_system[a_system]["candidate_record_hash"],
                "b_candidate_record_hash": by_system[b_system]["candidate_record_hash"],
                "nonce": f"human-secret-{index:02d}-not-shown-before-reveal",
            }
        )
    commitment_hash = paired_boundary_mapping_commitment_hash(
        study_id=predeclaration["study_id"],
        predeclaration_hash=predeclaration["predeclaration_hash"],
        entries=mapping_entries,
    )
    mapping = {
        "commitment_hash": commitment_hash,
        "committed_at_utc": _utc(20),
        "randomization_method": "opaque_balanced_per_clip_v1",
        "reveal": {
            "revealed_at_utc": _utc(50 + len(outcomes)),
            "labels_completed_at_utc": _utc(40 + len(outcomes)),
            "entries": mapping_entries,
        },
    }
    candidate_by_hash = {candidate["candidate_record_hash"]: candidate for candidate in candidates}
    judgements = []
    for index, (clip, requested_outcome, entry) in enumerate(
        zip(clips, outcomes, mapping_entries, strict=True)
    ):
        a_system = candidate_by_hash[entry["a_candidate_record_hash"]]["system"]
        b_system = candidate_by_hash[entry["b_candidate_record_hash"]]["system"]
        if requested_outcome == "v2_better":
            outcome = "a_better" if a_system == "v2" else "b_better"
        elif requested_outcome == "v1_better":
            outcome = "a_better" if a_system == "v1" else "b_better"
        else:
            outcome = requested_outcome
        by_system = {candidate["system"]: candidate for candidate in candidates}
        presentations = {
            system: next(
                item
                for item in by_system[system]["clip_presentations"]
                if item["clip_id"] == clip["clip_id"]
            )
            for system in ("v1", "v2")
        }
        a_unacceptable = index in v2_unacceptable and a_system == "v2"
        b_unacceptable = index in v2_unacceptable and b_system == "v2"
        judgements.append(
            {
                "clip_id": clip["clip_id"],
                "evaluator_id": "human-evaluator-01",
                "outcome": outcome,
                "a_unacceptable": a_unacceptable,
                "b_unacceptable": b_unacceptable,
                "a_presentation_artifact_hash": presentations[a_system][
                    "presentation_artifact_hash"
                ],
                "b_presentation_artifact_hash": presentations[b_system][
                    "presentation_artifact_hash"
                ],
                "mapping_commitment_hash": commitment_hash,
                "submitted_at_utc": _utc(30 + index // 2),
            }
        )
    payload = {
        "schema_version": 1,
        "evaluation_kind": "paired_boundary_superiority",
        "study_id": predeclaration["study_id"],
        "protocol_id": "podcast-subtitle-v2-paired-blind-v1",
        "episode_id": "anji-20260415",
        "lineage_id": "anji-program-v2-normalized",
        "normalized_audio_hash": ANJI_AUDIO_SHA256,
        "benchmark_suite_hash": "f" * 64,
        "complete": True,
        "candidate_identity_hidden_during_labelling": True,
        "labels_created_by_humans": True,
        "predeclaration": predeclaration,
        "candidates": candidates,
        "mapping": mapping,
        "judgements": judgements,
    }
    payload["study_hash"] = paired_boundary_study_hash(payload)
    return payload


def _rebind_predeclaration(payload: dict) -> None:
    payload["predeclaration"]["predeclaration_hash"] = paired_boundary_predeclaration_hash(
        payload["predeclaration"]
    )
    for candidate in payload["candidates"]:
        candidate["predeclaration_hash"] = payload["predeclaration"]["predeclaration_hash"]
        candidate["candidate_record_hash"] = paired_boundary_candidate_record_hash(candidate)
    by_system = {candidate["system"]: candidate for candidate in payload["candidates"]}
    for index, entry in enumerate(payload["mapping"]["reveal"]["entries"]):
        a_system, b_system = ("v1", "v2") if index % 2 == 0 else ("v2", "v1")
        entry["a_candidate_record_hash"] = by_system[a_system]["candidate_record_hash"]
        entry["b_candidate_record_hash"] = by_system[b_system]["candidate_record_hash"]
    commitment = paired_boundary_mapping_commitment_hash(
        study_id=payload["study_id"],
        predeclaration_hash=payload["predeclaration"]["predeclaration_hash"],
        entries=payload["mapping"]["reveal"]["entries"],
    )
    payload["mapping"]["commitment_hash"] = commitment
    for judgement in payload["judgements"]:
        judgement["mapping_commitment_hash"] = commitment
    payload["study_hash"] = paired_boundary_study_hash(payload)


def test_same_lineage_complete_blind_pairs_can_prove_v2_superiority() -> None:
    payload = _study_payload(outcomes=("v2_better",) * 16 + ("tie_both_good",) * 4)

    report = evaluate_paired_boundary_study(load_paired_boundary_study(payload))

    assert report.status is GateStatus.PASSED
    assert report.v2_wins == 16
    assert report.v1_wins == 0
    assert report.tie_both_good == 4
    assert report.tie_both_bad == 0
    assert report.decisive_pair_count == 16
    assert report.v2_decisive_win_rate == 1.0
    assert report.v2_unacceptable_rate == 0.0
    assert report.one_sided_p_value == pytest.approx(1 / 65_536)
    assert report.statistical_method == (
        "exact_one_sided_paired_sign_test_binomial_p0_0.5_ties_excluded"
    )
    assert dict(report.predeclared_thresholds)["minimum_v2_decisive_win_rate"] == 0.65
    assert report.evidence_scope == "one_episode_predeclared_nonoverlapping_clips"
    assert report.to_gate_result().status is GateStatus.PASSED


def test_v1_decisive_win_is_a_failed_superiority_gate() -> None:
    payload = _study_payload(outcomes=("v1_better",) * 16 + ("tie_both_good",) * 4)

    report = evaluate_paired_boundary_study(payload)

    assert report.status is GateStatus.FAILED
    assert report.v2_wins == 0
    assert report.v1_wins == 16
    assert report.one_sided_p_value == 1.0
    assert "superiority" in (report.reason or "")


def test_tie_heavy_or_too_small_studies_remain_not_evaluated() -> None:
    tie_heavy = evaluate_paired_boundary_study(
        _study_payload(outcomes=("v2_better",) * 11 + ("tie_both_good",) * 9)
    )
    too_small = evaluate_paired_boundary_study(_study_payload(outcomes=("v2_better",) * 10))

    assert tie_heavy.status is GateStatus.NOT_EVALUATED
    assert tie_heavy.decisive_pair_count == 11
    assert "decisive" in (tie_heavy.reason or "")
    assert too_small.status is GateStatus.NOT_EVALUATED
    assert too_small.labelled_pair_count == 10
    assert "clip sample" in (too_small.reason or "")


def test_frozen_effect_and_guardrail_thresholds_are_inclusive_at_exact_value() -> None:
    exact_effect = evaluate_paired_boundary_study(
        _study_payload(outcomes=("v2_better",) * 26 + ("v1_better",) * 14)
    )
    exact_guardrail = evaluate_paired_boundary_study(
        _study_payload(outcomes=("v2_better",) * 20, v2_unacceptable=frozenset({0}))
    )

    assert exact_effect.v2_decisive_win_rate == 0.65
    assert exact_effect.one_sided_p_value is not None
    assert exact_effect.one_sided_p_value <= 0.05
    assert exact_effect.status is GateStatus.PASSED
    assert exact_guardrail.v2_unacceptable_rate == 0.05
    assert exact_guardrail.status is GateStatus.PASSED

    exact_p = _study_payload(outcomes=("v2_better",) * 12 + ("tie_both_good",) * 8)
    exact_p["predeclaration"]["one_sided_alpha"] = 1 / 4096
    _rebind_predeclaration(exact_p)
    exact_p_report = evaluate_paired_boundary_study(exact_p)
    assert exact_p_report.one_sided_p_value == 1 / 4096
    assert exact_p_report.status is GateStatus.PASSED


def test_unrevealed_early_reveal_or_broken_blindness_cannot_pass() -> None:
    unrevealed = _study_payload(outcomes=("v2_better",) * 20)
    unrevealed["complete"] = False
    unrevealed["mapping"]["reveal"] = None
    unrevealed["study_hash"] = paired_boundary_study_hash(unrevealed)
    unrevealed_report = evaluate_paired_boundary_study(unrevealed)

    visible_identity = _study_payload(outcomes=("v2_better",) * 20)
    visible_identity["candidate_identity_hidden_during_labelling"] = False
    visible_identity["study_hash"] = paired_boundary_study_hash(visible_identity)
    visible_report = evaluate_paired_boundary_study(visible_identity)

    non_human = _study_payload(outcomes=("v2_better",) * 20)
    non_human["labels_created_by_humans"] = False
    non_human["study_hash"] = paired_boundary_study_hash(non_human)
    non_human_report = evaluate_paired_boundary_study(non_human)

    early_reveal = _study_payload(outcomes=("v2_better",) * 20)
    early_reveal["mapping"]["reveal"]["revealed_at_utc"] = early_reveal["mapping"]["reveal"][
        "labels_completed_at_utc"
    ]
    early_reveal["study_hash"] = paired_boundary_study_hash(early_reveal)

    assert unrevealed_report.status is GateStatus.NOT_EVALUATED
    assert visible_report.status is GateStatus.NOT_EVALUATED
    assert "identity" in (visible_report.reason or "")
    assert non_human_report.status is GateStatus.NOT_EVALUATED
    assert "human" in (non_human_report.reason or "")
    with pytest.raises(ValueError, match="revealed only after labels"):
        load_paired_boundary_study(early_reveal)


def test_candidate_byte_drift_or_candidate_swap_breaks_content_addressed_mapping() -> None:
    byte_drift = _study_payload(outcomes=("v2_better",) * 20)
    byte_drift["candidates"][1]["candidate_artifact_hash"] = "0" * 64
    byte_drift["study_hash"] = paired_boundary_study_hash(byte_drift)

    swapped = _study_payload(outcomes=("v2_better",) * 20)
    swapped["candidates"][1]["clip_presentations"][0]["presentation_artifact_hash"] = "1" * 64
    swapped["candidates"][1]["candidate_record_hash"] = paired_boundary_candidate_record_hash(
        swapped["candidates"][1]
    )
    swapped["study_hash"] = paired_boundary_study_hash(swapped)

    with pytest.raises(ValueError, match="candidate_record_hash mismatch"):
        load_paired_boundary_study(byte_drift)
    with pytest.raises(ValueError, match="map exact V1 and V2 candidate records"):
        load_paired_boundary_study(swapped)

    mapping_swap = _study_payload(outcomes=("v2_better",) * 20)
    entries = mapping_swap["mapping"]["reveal"]["entries"]
    for entry in entries[:2]:
        entry["a_candidate_record_hash"], entry["b_candidate_record_hash"] = (
            entry["b_candidate_record_hash"],
            entry["a_candidate_record_hash"],
        )
    mapping_swap["mapping"]["commitment_hash"] = paired_boundary_mapping_commitment_hash(
        study_id=mapping_swap["study_id"],
        predeclaration_hash=mapping_swap["predeclaration"]["predeclaration_hash"],
        entries=entries,
    )
    for judgement in mapping_swap["judgements"]:
        judgement["mapping_commitment_hash"] = mapping_swap["mapping"]["commitment_hash"]
    mapping_swap["study_hash"] = paired_boundary_study_hash(mapping_swap)
    with pytest.raises(ValueError, match="presentation hash/mapping mismatch"):
        load_paired_boundary_study(mapping_swap)


def test_audio_lineage_drift_is_rejected_even_when_outer_study_hash_is_recomputed() -> None:
    payload = _study_payload(outcomes=("v2_better",) * 20)
    payload["normalized_audio_hash"] = "e" * 64
    payload["study_hash"] = paired_boundary_study_hash(payload)

    with pytest.raises(ValueError, match="normalized-audio lineage"):
        load_paired_boundary_study(payload)


@pytest.mark.parametrize(
    "field", ["canonical_content_hash", "token_sequence_hash", "renderer_identity_hash"]
)
def test_paired_boundary_study_isolates_segmentation_from_text_and_rendering(field: str) -> None:
    payload = _study_payload(outcomes=("v2_better",) * 20)
    payload["candidates"][1][field] = "d" * 64
    payload["candidates"][1]["candidate_record_hash"] = paired_boundary_candidate_record_hash(
        payload["candidates"][1]
    )
    for entry in payload["mapping"]["reveal"]["entries"]:
        if entry["a_candidate_record_hash"] != payload["candidates"][0]["candidate_record_hash"]:
            entry["a_candidate_record_hash"] = payload["candidates"][1]["candidate_record_hash"]
        if entry["b_candidate_record_hash"] != payload["candidates"][0]["candidate_record_hash"]:
            entry["b_candidate_record_hash"] = payload["candidates"][1]["candidate_record_hash"]
    payload["mapping"]["commitment_hash"] = paired_boundary_mapping_commitment_hash(
        study_id=payload["study_id"],
        predeclaration_hash=payload["predeclaration"]["predeclaration_hash"],
        entries=payload["mapping"]["reveal"]["entries"],
    )
    for judgement in payload["judgements"]:
        judgement["mapping_commitment_hash"] = payload["mapping"]["commitment_hash"]
    payload["study_hash"] = paired_boundary_study_hash(payload)

    with pytest.raises(ValueError, match=f"share exact {field}"):
        load_paired_boundary_study(payload)


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("protocol_id",), "custom-weaker-protocol", "protocol_id"),
        (
            ("predeclaration", "selection_method"),
            "candidate-picked-clips",
            "selection_method",
        ),
        (("mapping", "randomization_method"), "always-put-v2-in-b", "randomization_method"),
    ],
)
def test_only_canonical_protocol_versions_can_reach_release_gate(
    path: tuple[str, ...], value: str, message: str
) -> None:
    payload = _study_payload(outcomes=("v2_better",) * 20)
    target = payload
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    if path[0] == "predeclaration":
        payload["predeclaration"]["predeclaration_hash"] = paired_boundary_predeclaration_hash(
            payload["predeclaration"]
        )
    payload["study_hash"] = paired_boundary_study_hash(payload)

    with pytest.raises(ValueError, match=message):
        load_paired_boundary_study(payload)


def test_predeclared_thresholds_may_be_stricter_but_not_weaker_than_protocol_floor() -> None:
    payload = _study_payload(outcomes=("v2_better",) * 20)
    payload["predeclaration"]["minimum_v2_decisive_win_rate"] = 0.5001
    _rebind_predeclaration(payload)

    with pytest.raises(ValueError, match=r"\[0\.65, 1\]"):
        load_paired_boundary_study(payload)


def test_v2_unacceptable_guardrail_fails_even_if_ties_make_decisive_sample_insufficient() -> None:
    payload = _study_payload(
        outcomes=("v2_better",) * 2 + ("tie_both_good",) * 18,
        v2_unacceptable=frozenset({0, 1}),
    )

    report = evaluate_paired_boundary_study(payload)

    assert report.decisive_pair_count == 2
    assert report.v2_unacceptable_rate == 0.1
    assert report.status is GateStatus.FAILED
    assert "guardrail" in (report.reason or "")


def test_selection_must_be_independent_and_frozen_before_candidate_generation() -> None:
    non_independent = _study_payload(outcomes=("v2_better",) * 20)
    non_independent["predeclaration"]["selection_independent_of_candidates"] = False
    _rebind_predeclaration(non_independent)
    non_independent_report = evaluate_paired_boundary_study(non_independent)

    candidate_before_freeze = _study_payload(outcomes=("v2_better",) * 20)
    candidate_before_freeze["candidates"][1]["generated_at_utc"] = _utc(-1)
    candidate_before_freeze["candidates"][1]["candidate_record_hash"] = (
        paired_boundary_candidate_record_hash(candidate_before_freeze["candidates"][1])
    )
    candidate_before_freeze["study_hash"] = paired_boundary_study_hash(candidate_before_freeze)

    assert non_independent_report.status is GateStatus.NOT_EVALUATED
    assert "independent" in (non_independent_report.reason or "")
    with pytest.raises(ValueError, match="generated after selection freeze"):
        load_paired_boundary_study(candidate_before_freeze)


def test_missing_labels_are_not_evaluated_while_duplicates_and_aggregate_only_are_rejected() -> (
    None
):
    missing = _study_payload(outcomes=("v2_better",) * 20)
    missing["complete"] = False
    missing["judgements"].pop()
    missing["study_hash"] = paired_boundary_study_hash(missing)
    missing_report = evaluate_paired_boundary_study(missing)

    duplicate = _study_payload(outcomes=("v2_better",) * 20)
    duplicate["judgements"].append(copy.deepcopy(duplicate["judgements"][0]))
    duplicate["study_hash"] = paired_boundary_study_hash(duplicate)

    aggregate_only = {
        "schema_version": 1,
        "evaluation_kind": "paired_boundary_superiority",
        "study_id": "aggregate-is-not-evidence",
        "v2_wins": 20,
        "v1_wins": 0,
        "ties": 0,
        "one_sided_p_value": 0.000001,
    }

    assert missing_report.status is GateStatus.NOT_EVALUATED
    assert missing_report.labelled_pair_count == 19
    with pytest.raises(ValueError, match="exactly one judgement"):
        load_paired_boundary_study(duplicate)
    with pytest.raises(ValueError, match="fields mismatch"):
        load_paired_boundary_study(aggregate_only)

    missing_complete_view = _study_payload(outcomes=("v2_better",) * 20)
    missing_complete_view["candidates"][1]["clip_presentations"].pop()
    missing_complete_view["candidates"][1]["candidate_record_hash"] = (
        paired_boundary_candidate_record_hash(missing_complete_view["candidates"][1])
    )
    missing_complete_view["study_hash"] = paired_boundary_study_hash(missing_complete_view)
    with pytest.raises(ValueError, match="present every predeclared clip"):
        load_paired_boundary_study(missing_complete_view)


def test_main_benchmark_consumes_paired_gate_without_treating_legacy_baseline_as_superiority() -> (
    None
):
    correction = load_json_fixture(FIXTURES / "anji_correction_gold.v1.json")
    boundary = load_json_fixture(FIXTURES / "anji_boundary_gold.v1.json")
    review = load_json_fixture(FIXTURES / "anji_review_gold.v1.json")
    payload = _study_payload(outcomes=("v2_better",) * 20)
    payload["episode_id"] = correction["episode_id"]
    payload["lineage_id"] = correction["lineage_id"]
    payload["benchmark_suite_hash"] = benchmark_suite_hash(correction, boundary, review)
    v2 = next(item for item in payload["candidates"] if item["system"] == "v2")
    candidate = BenchmarkCandidate(
        candidate_id=v2["candidate_id"],
        generation_id=v2["generation_id"],
        canonical_content_hash=v2["canonical_content_hash"],
        normalized_audio_hash=v2["normalized_audio_hash"],
        artifact_hash=v2["candidate_artifact_hash"],
    )
    payload["study_hash"] = paired_boundary_study_hash(payload)

    report = evaluate_benchmark(
        correction_gold=correction,
        boundary_gold=boundary,
        review_gold=review,
        candidate=candidate,
        paired_boundary=payload,
    )

    assert report.gate("paired_boundary_v2_superiority").status is GateStatus.PASSED
    assert report.gate("paired_boundary_v2_superiority").baseline_value is None
    assert not report.release_ready  # Other independent release gold is intentionally absent.
