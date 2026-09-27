"""Deterministic selective audio-audit planning and escalation.

Text audit remains universal.  This module only chooses the exact Canonical
spans whose AuditPlan cells require audio evidence in one immutable round.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Sequence

from shared.schemas.podcast_subtitles_v2 import AuditPlan, CanonicalTranscript
from shared.schemas.podcast_subtitles_v2_audio_selection import (
    AudioAuditRiskReasonV1,
    AudioAuditRiskTargetV1,
    AudioAuditSelectionPlanV1,
    AudioAuditSelectionPolicyV1,
    AudioAuditSelectionReceiptV1,
    AudioAuditSelectionTierV1,
    AudioAuditStratumAllocationV1,
)
from shared.schemas.podcast_subtitles_v2_selective_audio import (
    SelectiveAudioAuditExecutionRecordV3,
    SelectiveAudioAuditRoundReceiptV3,
)
from shared.schemas.podcast_subtitles_v2_text_audit import TextAuditExecutionRecordV2

from .hashing import canonical_json_bytes, hash_file, hash_object, sha256_bytes

AUDIO_AUDIT_SELECTION_BUILDER_ID = "nakama-selective-audio-audit-builder"
AUDIO_AUDIT_SELECTION_BUILDER_VERSION = 1

_SPAN_REASON = {
    "speaker_identity": "speaker_conflict",
}
_BOUNDARY_REASON = {
    "chunk_seam": "boundary_or_seam_anomaly",
    "speech_coverage": "speech_coverage_anomaly",
    "repetition_self_repair": "boundary_or_seam_anomaly",
}


class AudioAuditSelectionError(ValueError):
    """A selective audio-audit artifact cannot be reproduced exactly."""


def audio_audit_selection_builder_code_hash() -> str:
    return hash_file(Path(__file__))


def default_audio_audit_selection_policy(
    *, clock_strata: int = 10
) -> AudioAuditSelectionPolicyV1:
    payload = {
        "schema_version": 1,
        "policy_id": "nakama-selective-audio-audit",
        "policy_version": 1,
        "initial_sample_basis_points": 1000,
        "expanded_sample_basis_points": 3000,
        "material_error_threshold_basis_points": 200,
        "clock_strata": clock_strata,
        "speaker_stratification": True,
        "sample_algorithm": "sha256-ranked-clock-speaker-strata-v1",
        "risk_policy": "closed-risk-codes-plus-text-findings-v1",
        "catastrophic_escalation": "full_immediately",
        "threshold_comparison": "strictly_greater_than",
    }
    return AudioAuditSelectionPolicyV1(**payload, content_hash=hash_object(payload))


def _assert_parents(
    audit_plan: AuditPlan,
    transcript: CanonicalTranscript,
    text_record: TextAuditExecutionRecordV2 | None,
) -> None:
    transcript_hash = sha256_bytes(canonical_json_bytes(transcript))
    audit_hash = sha256_bytes(canonical_json_bytes(audit_plan))
    if (
        audit_plan.episode_id != transcript.episode_id
        or audit_plan.generation_id != transcript.generation_id
        or audit_plan.inputs.canonical_transcript_hash != transcript_hash
        or audit_plan.inputs.canonical_content_hash != transcript.content_hash
        or audit_plan.inputs.normalized_audio_hash != transcript.normalized_audio_hash
    ):
        raise AudioAuditSelectionError("audio selection parents cross Canonical lineage")
    if text_record is not None and (
        text_record.audit_plan_hash != audit_hash
        or text_record.audit_plan_content_hash != audit_plan.content_hash
        or text_record.execution_status != "complete_text_only"
    ):
        raise AudioAuditSelectionError("audio selection text record crosses AuditPlan lineage")


def _span_ownership(
    audit_plan: AuditPlan,
) -> tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...]:
    cells_by_target: dict[str, list[object]] = defaultdict(list)
    for cell in audit_plan.cells:
        cells_by_target[cell.target_id].append(cell)
    result: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = []
    for index, span in enumerate(audit_plan.span_targets):
        targets = [span.id, audit_plan.boundary_targets[index].id]
        if index == len(audit_plan.span_targets) - 1:
            targets.append(audit_plan.boundary_targets[-1].id)
        cells = tuple(cell for target in targets for cell in cells_by_target[target])
        result.append(
            (
                span.span_id,
                tuple(cell.id for cell in cells),  # type: ignore[attr-defined]
                tuple(
                    cell.id  # type: ignore[attr-defined]
                    for cell in cells
                    if cell.applicability == "required"  # type: ignore[attr-defined]
                ),
            )
        )
    return tuple(result)


def _risk_reasons(
    audit_plan: AuditPlan,
    transcript: CanonicalTranscript,
    text_record: TextAuditExecutionRecordV2 | None,
) -> tuple[
    dict[str, set[AudioAuditRiskReasonV1]],
    dict[str, set[str]],
]:
    span_by_target = {item.id: item.span_id for item in audit_plan.span_targets}
    boundary_by_target = {item.id: item for item in audit_plan.boundary_targets}
    reasons: dict[str, set[AudioAuditRiskReasonV1]] = defaultdict(set)
    source_ids: dict[str, set[str]] = defaultdict(set)
    for cell in audit_plan.cells:
        if cell.applicability != "required":
            continue
        if cell.target_kind == "span" and cell.category in _SPAN_REASON:
            span_id = span_by_target[cell.target_id]
            reasons[span_id].add(_SPAN_REASON[cell.category])  # type: ignore[arg-type]
            source_ids[span_id].add(cell.id)
        elif cell.target_kind == "boundary" and cell.category in _BOUNDARY_REASON:
            boundary = boundary_by_target[cell.target_id]
            reason = _BOUNDARY_REASON[cell.category]
            if boundary.left_span_id is not None:
                reasons[boundary.left_span_id].add(reason)  # type: ignore[arg-type]
                source_ids[boundary.left_span_id].add(cell.id)
            if boundary.right_span_id is not None:
                reasons[boundary.right_span_id].add(reason)  # type: ignore[arg-type]
                source_ids[boundary.right_span_id].add(cell.id)

    if text_record is not None:
        for assessment in text_record.assessments:
            if assessment.status == "verified_clean_within_text_evidence":
                continue
            reason: AudioAuditRiskReasonV1 = (
                "text_finding" if assessment.status == "finding" else "text_disagreement"
            )
            if assessment.target_id in span_by_target:
                span_id = span_by_target[assessment.target_id]
                reasons[span_id].add(reason)
                source_ids[span_id].add(assessment.id)
            elif assessment.target_id in boundary_by_target:
                boundary = boundary_by_target[assessment.target_id]
                if boundary.left_span_id is not None:
                    reasons[boundary.left_span_id].add(reason)
                    source_ids[boundary.left_span_id].add(assessment.id)
                if boundary.right_span_id is not None:
                    reasons[boundary.right_span_id].add(reason)
                    source_ids[boundary.right_span_id].add(assessment.id)
            else:
                raise AudioAuditSelectionError("text assessment targets an unknown AuditPlan cell")

    # Memo commonly provides no confidence at all.  Absence of that adapter
    # capability is not a per-span risk; universal text audit plus sampling
    # covers it.  Only an explicit stored low confidence targets audio.
    token_by_id = {item.id: item for item in transcript.tokens}
    for span in transcript.spans:
        explicit = tuple(
            token_by_id[token_id].confidence
            for token_id in span.token_ids
            if token_by_id[token_id].confidence is not None
        )
        if explicit and min(explicit) < audit_plan.policy.low_confidence_threshold:
            reasons[span.id].add("explicit_low_confidence")
            source_ids[span.id].update(
                token_id
                for token_id in span.token_ids
                if token_by_id[token_id].confidence is not None
                and token_by_id[token_id].confidence < audit_plan.policy.low_confidence_threshold
            )

    known_spans = {item.span_id for item in audit_plan.span_targets}
    for issue in transcript.review_issues:
        unknown = set(issue.span_ids) - known_spans
        if unknown:
            raise AudioAuditSelectionError("Canonical review issue targets an unknown span")
        if issue.code == "native_full_audit_requires_resolution":
            # This is a workflow authorization gate created before native audit,
            # not evidence that any particular span is acoustically risky.
            continue
        for span_id in issue.span_ids:
            reasons[span_id].add("canonical_review_issue")
            source_ids[span_id].add(issue.id)
    return reasons, source_ids


def _speaker_by_span(transcript: CanonicalTranscript) -> dict[str, str]:
    token_by_id = {item.id: item for item in transcript.tokens}
    result: dict[str, str] = {}
    for span in transcript.spans:
        speakers = {token_by_id[token_id].speaker for token_id in span.token_ids}
        result[span.id] = (
            next(iter(speakers))
            if len(speakers) == 1 and None not in speakers
            else "__unknown__"
        )
    return result


def _allocate_quotas(
    sizes: dict[tuple[int, str], int],
    quota: int,
) -> dict[tuple[int, str], int]:
    if quota < 0 or quota > sum(sizes.values()):
        raise AudioAuditSelectionError("audio sample quota exceeds ordinary population")
    if not sizes:
        return {}
    minimum = {key: 1 for key in sizes} if quota >= len(sizes) else {key: 0 for key in sizes}
    remaining = quota - sum(minimum.values())
    capacities = {key: sizes[key] - minimum[key] for key in sizes}
    capacity_total = sum(capacities.values())
    if remaining == 0 or capacity_total == 0:
        return minimum
    floors = {
        key: remaining * capacities[key] // capacity_total
        for key in sizes
    }
    allocations = {key: minimum[key] + floors[key] for key in sizes}
    leftover = quota - sum(allocations.values())
    remainder_order = sorted(
        sizes,
        key=lambda key: (
            -(remaining * capacities[key] % capacity_total),
            key,
        ),
    )
    for key in remainder_order:
        if leftover == 0:
            break
        if allocations[key] < sizes[key]:
            allocations[key] += 1
            leftover -= 1
    if leftover:
        raise AudioAuditSelectionError("audio sample largest-remainder allocation failed")
    return allocations


def _sample_strata(
    audit_plan: AuditPlan,
    transcript: CanonicalTranscript,
    policy: AudioAuditSelectionPolicyV1,
    *,
    seed: str,
    risk_span_ids: set[str],
    sample_basis_points: int,
    tier: AudioAuditSelectionTierV1,
    prior_sample_span_ids: Sequence[str] = (),
) -> tuple[tuple[str, ...], tuple[AudioAuditStratumAllocationV1, ...]]:
    speakers = _speaker_by_span(transcript)
    duration_ms = max(item.end_ms for item in audit_plan.span_targets)
    strata: dict[tuple[int, str], list[str]] = defaultdict(list)
    for target in audit_plan.span_targets:
        if target.span_id in risk_span_ids:
            continue
        clock = min(
            policy.clock_strata - 1,
            target.start_ms * policy.clock_strata // max(duration_ms, 1),
        )
        speaker = speakers[target.span_id] if policy.speaker_stratification else "__all__"
        strata[(clock, speaker)].append(target.span_id)
    ordinary_count = sum(len(items) for items in strata.values())
    quota = (ordinary_count * sample_basis_points + 9999) // 10000
    allocations = _allocate_quotas(
        {key: len(value) for key, value in strata.items()},
        quota,
    )
    prior = set(prior_sample_span_ids)
    ordinary = {span_id for values in strata.values() for span_id in values}
    if not prior <= ordinary:
        raise AudioAuditSelectionError("prior audio sample crosses frozen ordinary population")
    prior_counts = {
        key: len(prior & set(candidates)) for key, candidates in strata.items()
    }
    for key in allocations:
        allocations[key] = max(allocations[key], prior_counts[key])
    excess = sum(allocations.values()) - quota
    if excess:
        reducible = sorted(
            allocations,
            key=lambda key: (allocations[key] - prior_counts[key], key),
            reverse=True,
        )
        for key in reducible:
            take = min(excess, allocations[key] - prior_counts[key])
            allocations[key] -= take
            excess -= take
            if excess == 0:
                break
    if excess:
        raise AudioAuditSelectionError("prior audio sample cannot fit cumulative tier quota")

    selected: set[str] = set(prior)
    allocation_models: list[AudioAuditStratumAllocationV1] = []
    for stratum, candidates in sorted(strata.items()):
        ranked = sorted(
            candidates,
            key=lambda span_id: hash_object(
                {
                    "seed": seed,
                    "tier": tier,
                    "stratum": stratum,
                    "span_id": span_id,
                }
            ),
        )
        already = prior & set(candidates)
        needed = allocations[stratum] - len(already)
        selected.update(
            span_id for span_id in ranked if span_id not in already
        )
        chosen = already | set(
            span_id for span_id in ranked if span_id not in already
        )
        # Trim only the newly ranked suffix; prior selections are immutable.
        new_ranked = [span_id for span_id in ranked if span_id not in already][:needed]
        chosen = already | set(new_ranked)
        selected.difference_update(set(candidates) - chosen)
        population_order = tuple(
            item.span_id
            for item in audit_plan.span_targets
            if item.span_id in set(candidates)
        )
        selected_order = tuple(span_id for span_id in population_order if span_id in chosen)
        stratum_id = hash_object(
            {
                "clock_bucket": stratum[0],
                "speaker_label": stratum[1],
                "population_span_ids": population_order,
            }
        )
        allocation_models.append(
            AudioAuditStratumAllocationV1(
                stratum_id=stratum_id,
                clock_bucket=stratum[0],
                speaker_label=stratum[1],
                population_span_ids=population_order,
                quota=len(selected_order),
                selected_span_ids=selected_order,
            )
        )
    sample = tuple(
        item.span_id for item in audit_plan.span_targets if item.span_id in selected
    )
    if len(sample) != quota:
        raise AudioAuditSelectionError("audio sample union differs from exact tier quota")
    return sample, tuple(allocation_models)


def _assert_prior_round(
    *,
    tier: AudioAuditSelectionTierV1,
    prior_plan: AudioAuditSelectionPlanV1 | None,
    prior_receipt: AudioAuditSelectionReceiptV1 | None,
    audit_plan: AuditPlan,
    transcript: CanonicalTranscript,
    policy: AudioAuditSelectionPolicyV1,
    text_record: TextAuditExecutionRecordV2 | None,
) -> None:
    if tier == "sample_10":
        if prior_plan is not None or prior_receipt is not None:
            raise AudioAuditSelectionError("initial selection cannot claim a prior round")
        return
    if prior_plan is None or prior_receipt is None:
        raise AudioAuditSelectionError("escalated selection requires prior plan and receipt")
    try:
        parsed_prior = AudioAuditSelectionPlanV1.model_validate_json(
            canonical_json_bytes(prior_plan), strict=True
        )
    except ValueError as exc:
        raise AudioAuditSelectionError("prior audio selection plan is invalid") from exc
    text_hash = (
        sha256_bytes(canonical_json_bytes(text_record)) if text_record is not None else None
    )
    if parsed_prior != prior_plan or (
        prior_plan.episode_id != transcript.episode_id
        or prior_plan.generation_id != transcript.generation_id
        or prior_plan.normalized_audio_hash != transcript.normalized_audio_hash
        or prior_plan.audit_plan_hash != sha256_bytes(canonical_json_bytes(audit_plan))
        or prior_plan.audit_plan_content_hash != audit_plan.content_hash
        or prior_plan.canonical_transcript_hash
        != sha256_bytes(canonical_json_bytes(transcript))
        or prior_plan.canonical_content_hash != transcript.content_hash
        or prior_plan.policy != policy
        or prior_plan.text_audit_record_hash != text_hash
        or prior_plan.text_audit_record_content_hash
        != (text_record.content_hash if text_record is not None else None)
    ):
        raise AudioAuditSelectionError("prior audio selection plan crosses current lineage")
    verify_audio_audit_selection_receipt(prior_plan, prior_receipt)
    expected = "escalate_30" if tier == "sample_30" else "escalate_full"
    if prior_receipt.decision != expected:
        raise AudioAuditSelectionError("prior selection receipt does not authorize this tier")
    if tier == "sample_30" and prior_plan.tier != "sample_10":
        raise AudioAuditSelectionError("30 percent selection requires the initial round")
    if tier == "full" and prior_plan.tier not in ("sample_10", "sample_30"):
        raise AudioAuditSelectionError("full selection requires a sampled prior round")


def build_audio_audit_selection_plan(
    audit_plan: AuditPlan,
    transcript: CanonicalTranscript,
    policy: AudioAuditSelectionPolicyV1,
    *,
    tier: AudioAuditSelectionTierV1,
    text_record: TextAuditExecutionRecordV2 | None = None,
    prior_plan: AudioAuditSelectionPlanV1 | None = None,
    prior_receipt: AudioAuditSelectionReceiptV1 | None = None,
) -> AudioAuditSelectionPlanV1:
    """Build one exact deterministic audio selection round."""

    _assert_parents(audit_plan, transcript, text_record)
    _assert_prior_round(
        tier=tier,
        prior_plan=prior_plan,
        prior_receipt=prior_receipt,
        audit_plan=audit_plan,
        transcript=transcript,
        policy=policy,
        text_record=text_record,
    )
    seed = hash_object(
        {
            "episode_id": transcript.episode_id,
            "normalized_audio_hash": transcript.normalized_audio_hash,
            "policy_id": policy.policy_id,
            "policy_version": policy.policy_version,
            "policy_content_hash": policy.content_hash,
        }
    )
    reasons, risk_source_ids = _risk_reasons(audit_plan, transcript, text_record)
    risk_span_ids = tuple(
        item.span_id for item in audit_plan.span_targets if reasons.get(item.span_id)
    )
    basis_points = (
        10000
        if tier == "full"
        else (
            policy.initial_sample_basis_points
            if tier == "sample_10"
            else policy.expanded_sample_basis_points
        )
    )
    sample_span_ids, strata = _sample_strata(
        audit_plan,
        transcript,
        policy,
        seed=seed,
        risk_span_ids=set(risk_span_ids),
        sample_basis_points=basis_points,
        tier=tier,
        prior_sample_span_ids=(
            prior_plan.sample_span_ids if prior_plan is not None else ()
        ),
    )
    selected_set = set(risk_span_ids) | set(sample_span_ids)
    selected_span_ids = tuple(
        item.span_id for item in audit_plan.span_targets if item.span_id in selected_set
    )
    ownership = _span_ownership(audit_plan)
    selected_owned = tuple(
        cell_id
        for span_id, owned, _ in ownership
        if span_id in selected_set
        for cell_id in owned
    )
    selected_required = tuple(
        cell_id
        for span_id, _, required in ownership
        if span_id in selected_set
        for cell_id in required
    )
    transcript_hash = sha256_bytes(canonical_json_bytes(transcript))
    audit_hash = sha256_bytes(canonical_json_bytes(audit_plan))
    text_hash = (
        sha256_bytes(canonical_json_bytes(text_record)) if text_record is not None else None
    )
    payload = {
        "schema_version": 1,
        "episode_id": transcript.episode_id,
        "generation_id": transcript.generation_id,
        "normalized_audio_hash": transcript.normalized_audio_hash,
        "audit_plan_hash": audit_hash,
        "audit_plan_content_hash": audit_plan.content_hash,
        "canonical_transcript_hash": transcript_hash,
        "canonical_content_hash": transcript.content_hash,
        "text_audit_record_hash": text_hash,
        "text_audit_record_content_hash": (
            text_record.content_hash if text_record is not None else None
        ),
        "policy": policy,
        "policy_hash": sha256_bytes(canonical_json_bytes(policy)),
        "tier": tier,
        "prior_plan_id": prior_plan.id if prior_plan is not None else None,
        "prior_receipt_id": prior_receipt.id if prior_receipt is not None else None,
        "sample_seed": seed,
        "all_span_ids": tuple(item.span_id for item in audit_plan.span_targets),
        "risk_targets": tuple(
            AudioAuditRiskTargetV1(
                span_id=item.span_id,
                span_target_id=item.id,
                reasons=tuple(sorted(reasons[item.span_id])),
                source_finding_ids=tuple(sorted(risk_source_ids[item.span_id])),
            )
            for item in audit_plan.span_targets
            if item.span_id in reasons
        ),
        "risk_span_ids": risk_span_ids,
        "strata": strata,
        "sample_span_ids": sample_span_ids,
        "selected_span_ids": selected_span_ids,
        "selected_owned_cell_ids": selected_owned,
        "selected_required_cell_ids": selected_required,
        "selection_scope": "exact_selected_audio_targets_only",
        "authority": "scope_only_not_audio_evidence_or_release_approval",
    }
    digest = hash_object(payload)
    return AudioAuditSelectionPlanV1(**payload, id=digest, content_hash=digest)


def verify_audio_audit_selection_plan(
    stored: AudioAuditSelectionPlanV1,
    audit_plan: AuditPlan,
    transcript: CanonicalTranscript,
    policy: AudioAuditSelectionPolicyV1,
    *,
    text_record: TextAuditExecutionRecordV2 | None = None,
    prior_plan: AudioAuditSelectionPlanV1 | None = None,
    prior_receipt: AudioAuditSelectionReceiptV1 | None = None,
) -> AudioAuditSelectionPlanV1:
    try:
        parsed = AudioAuditSelectionPlanV1.model_validate_json(
            canonical_json_bytes(stored), strict=True
        )
    except ValueError as exc:
        raise AudioAuditSelectionError("stored audio selection plan is invalid") from exc
    rebuilt = build_audio_audit_selection_plan(
        audit_plan,
        transcript,
        policy,
        tier=stored.tier,
        text_record=text_record,
        prior_plan=prior_plan,
        prior_receipt=prior_receipt,
    )
    if parsed != stored or rebuilt != stored:
        raise AudioAuditSelectionError("stored audio selection plan does not replay")
    return stored


def build_audio_audit_selection_receipt(
    plan: AudioAuditSelectionPlanV1,
    *,
    audited_span_ids: Sequence[str] | None = None,
    material_error_span_ids: Sequence[str] = (),
    major_error_span_ids: Sequence[str] = (),
    unresolved_span_ids: Sequence[str] = (),
    risk_finding_span_ids: Sequence[str] = (),
    catastrophic_span_ids: Sequence[str] = (),
) -> AudioAuditSelectionReceiptV1:
    """Close one round only when every exact selected target has an audio receipt."""

    audited = tuple(audited_span_ids) if audited_span_ids is not None else plan.selected_span_ids
    if audited != plan.selected_span_ids:
        raise AudioAuditSelectionError("audio receipt lacks exact selected target coverage")
    order = {span_id: index for index, span_id in enumerate(plan.selected_span_ids)}
    catastrophic = tuple(dict.fromkeys(catastrophic_span_ids))
    material_set = set(material_error_span_ids)
    major_set = set(major_error_span_ids)
    unresolved_set = set(unresolved_span_ids)
    risk_finding_set = set(risk_finding_span_ids)
    if not material_set <= set(plan.sample_span_ids):
        raise AudioAuditSelectionError("sample material finding targets a non-sample span")
    if not major_set <= material_set:
        raise AudioAuditSelectionError("major audio errors must be sampled material errors")
    if not risk_finding_set <= set(plan.risk_span_ids):
        raise AudioAuditSelectionError("risk audio finding targets a non-risk span")
    if not unresolved_set <= set(audited):
        raise AudioAuditSelectionError("unresolved audio finding targets an unselected span")
    if not set(catastrophic) <= set(audited):
        raise AudioAuditSelectionError("audio findings target an unselected span")
    material = tuple(sorted(material_set, key=order.__getitem__))
    major = tuple(sorted(major_set, key=order.__getitem__))
    unresolved = tuple(sorted(unresolved_set, key=order.__getitem__))
    risk_findings = tuple(sorted(risk_finding_set, key=order.__getitem__))
    catastrophic = tuple(sorted(catastrophic, key=order.__getitem__))
    denominator = len(plan.sample_span_ids)
    above_threshold = denominator > 0 and (
        len(material) * 10000
        > plan.policy.material_error_threshold_basis_points * denominator
    )
    if catastrophic:
        decision = "escalate_full"
    elif plan.tier == "sample_10" and (major or unresolved or above_threshold):
        decision = "escalate_30"
    elif plan.tier == "sample_30" and (unresolved or above_threshold):
        decision = "escalate_full"
    else:
        decision = "complete"
    payload = {
        "schema_version": 1,
        "plan_id": plan.id,
        "plan_content_hash": plan.content_hash,
        "plan_artifact_hash": sha256_bytes(canonical_json_bytes(plan)),
        "episode_id": plan.episode_id,
        "generation_id": plan.generation_id,
        "normalized_audio_hash": plan.normalized_audio_hash,
        "policy_hash": plan.policy_hash,
        "tier": plan.tier,
        "audited_span_ids": audited,
        "sampled_audited_span_ids": plan.sample_span_ids,
        "risk_audited_span_ids": plan.risk_span_ids,
        "material_error_span_ids": material,
        "major_error_span_ids": major,
        "unresolved_span_ids": unresolved,
        "risk_finding_span_ids": risk_findings,
        "catastrophic_span_ids": catastrophic,
        "material_error_numerator": len(material),
        "material_error_denominator": denominator,
        "decision": decision,
        "coverage_statement": "exact_selected_targets_have_audio_receipts",
        "authority": "selection_escalation_only_not_correction_or_release",
    }
    digest = hash_object(payload)
    return AudioAuditSelectionReceiptV1(**payload, id=digest, content_hash=digest)


def verify_audio_audit_selection_receipt(
    plan: AudioAuditSelectionPlanV1,
    stored: AudioAuditSelectionReceiptV1,
) -> AudioAuditSelectionReceiptV1:
    try:
        parsed = AudioAuditSelectionReceiptV1.model_validate_json(
            canonical_json_bytes(stored), strict=True
        )
    except ValueError as exc:
        raise AudioAuditSelectionError("stored audio selection receipt is invalid") from exc
    rebuilt = build_audio_audit_selection_receipt(
        plan,
        audited_span_ids=stored.audited_span_ids,
        material_error_span_ids=stored.material_error_span_ids,
        major_error_span_ids=stored.major_error_span_ids,
        unresolved_span_ids=stored.unresolved_span_ids,
        risk_finding_span_ids=stored.risk_finding_span_ids,
        catastrophic_span_ids=stored.catastrophic_span_ids,
    )
    if parsed != stored or rebuilt != stored:
        raise AudioAuditSelectionError("stored audio selection receipt does not replay")
    return stored


def build_selective_audio_round_receipt_v3(
    plan: AudioAuditSelectionPlanV1,
    records: Sequence[SelectiveAudioAuditExecutionRecordV3],
    *,
    previous_round: SelectiveAudioAuditRoundReceiptV3 | None = None,
) -> SelectiveAudioAuditRoundReceiptV3:
    """Derive one cumulative decision from authenticated selected-audio records."""

    record_tuple = tuple(records)
    if not record_tuple:
        raise AudioAuditSelectionError("selective audio round requires execution records")
    if plan.tier == "sample_10":
        if previous_round is not None:
            raise AudioAuditSelectionError("initial selective audio round cannot have a parent")
    else:
        if (
            previous_round is None
            or plan.prior_receipt_id != previous_round.decision_receipt.id
            or tuple(item.id for item in record_tuple[:-1])
            != previous_round.execution_record_ids
        ):
            raise AudioAuditSelectionError(
                "selective audio round does not extend exact prior ledger"
            )
    seen_spans: set[str] = set()
    for record in record_tuple:
        try:
            validated = SelectiveAudioAuditExecutionRecordV3.model_validate_json(
                canonical_json_bytes(record), strict=True
            )
        except ValueError as exc:
            raise AudioAuditSelectionError("selective audio record is invalid") from exc
        if validated != record or (
            record.audit_plan_hash != plan.audit_plan_hash
            or record.audit_plan_content_hash != plan.audit_plan_content_hash
            or seen_spans & set(record.selected_owned_span_ids)
        ):
            raise AudioAuditSelectionError("selective audio record lineage or delta overlaps")
        seen_spans.update(record.selected_owned_span_ids)
    if seen_spans != set(plan.selected_span_ids):
        raise AudioAuditSelectionError("selective audio records lack cumulative selected coverage")
    latest = record_tuple[-1]
    if (
        latest.selection_plan_id != plan.id
        or latest.selection_plan_content_hash != plan.content_hash
        or latest.tier != plan.tier
    ):
        raise AudioAuditSelectionError("latest audio delta differs from current selection")
    material = set().union(*(set(item.material_span_ids) for item in record_tuple))
    major = set().union(*(set(item.major_span_ids) for item in record_tuple))
    unresolved = set().union(*(set(item.unresolved_span_ids) for item in record_tuple))
    catastrophic = set().union(*(set(item.catastrophic_span_ids) for item in record_tuple))
    decision = build_audio_audit_selection_receipt(
        plan,
        material_error_span_ids=material & set(plan.sample_span_ids),
        major_error_span_ids=major & set(plan.sample_span_ids),
        unresolved_span_ids=unresolved,
        risk_finding_span_ids=material & set(plan.risk_span_ids),
        catastrophic_span_ids=catastrophic,
    )
    payload = {
        "schema_version": 3,
        "episode_id": plan.episode_id,
        "generation_id": plan.generation_id,
        "normalized_audio_hash": plan.normalized_audio_hash,
        "selection_plan_id": plan.id,
        "selection_plan_content_hash": plan.content_hash,
        "selection_plan_artifact_hash": sha256_bytes(canonical_json_bytes(plan)),
        "tier": plan.tier,
        "previous_round_receipt_id": previous_round.id if previous_round else None,
        "execution_record_ids": tuple(item.id for item in record_tuple),
        "execution_record_content_hashes": tuple(item.content_hash for item in record_tuple),
        "execution_record_artifact_hashes": tuple(
            sha256_bytes(canonical_json_bytes(item)) for item in record_tuple
        ),
        "decision_receipt": decision,
        "decision_receipt_artifact_hash": sha256_bytes(canonical_json_bytes(decision)),
        "execution_scope": (
            "cumulative_exact_selected_spans_from_nonoverlapping_delta_records"
        ),
        "authority": "escalation_only_not_correction_or_release",
    }
    digest = hash_object(payload)
    return SelectiveAudioAuditRoundReceiptV3(**payload, id=digest, content_hash=digest)


def verify_selective_audio_round_receipt_v3(
    stored: SelectiveAudioAuditRoundReceiptV3,
    plan: AudioAuditSelectionPlanV1,
    records: Sequence[SelectiveAudioAuditExecutionRecordV3],
    *,
    previous_round: SelectiveAudioAuditRoundReceiptV3 | None = None,
) -> SelectiveAudioAuditRoundReceiptV3:
    rebuilt = build_selective_audio_round_receipt_v3(
        plan,
        records,
        previous_round=previous_round,
    )
    if rebuilt != stored:
        raise AudioAuditSelectionError("selective audio round receipt does not replay")
    return stored


__all__ = [
    "AUDIO_AUDIT_SELECTION_BUILDER_ID",
    "AUDIO_AUDIT_SELECTION_BUILDER_VERSION",
    "AudioAuditSelectionError",
    "audio_audit_selection_builder_code_hash",
    "build_audio_audit_selection_plan",
    "build_audio_audit_selection_receipt",
    "build_selective_audio_round_receipt_v3",
    "default_audio_audit_selection_policy",
    "verify_audio_audit_selection_plan",
    "verify_audio_audit_selection_receipt",
    "verify_selective_audio_round_receipt_v3",
]
