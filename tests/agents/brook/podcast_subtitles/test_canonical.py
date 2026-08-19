from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agents.brook.podcast_subtitles.canonical import (
    add_review_risks,
    attest_speech_coverage,
    resolve_canonical,
    review_target_fingerprint,
)
from agents.brook.podcast_subtitles.canonical import (
    attest_full_audit as _attest_full_audit,
)
from agents.brook.podcast_subtitles.canonical import (
    reconcile_canonical as _reconcile_canonical,
)
from agents.brook.podcast_subtitles.errors import StaleFingerprintError
from agents.brook.podcast_subtitles.hashing import hash_object
from agents.brook.podcast_subtitles.ledger import CorrectionLedger
from agents.brook.podcast_subtitles.risk import RiskRecord, TermHint
from shared.schemas.podcast_subtitles_v2 import (
    ArtifactDigest,
    AudioAuditReceipt,
    CorrectionDecision,
    EvidenceToken,
    RecognitionEvidence,
    ReferenceArtifact,
    ReferenceEvidence,
    ReferenceLocator,
    ReferenceLocatorPart,
    ReplacementLexemeAlignment,
    ReviewIssue,
    SpeechActivityInterval,
    SpeechCoverageReceipt,
)
from tests.agents.brook.podcast_subtitles.reference_authority_fixtures import (
    reference_authority_fixture,
)

H_AUDIO = "a" * 64
H_SOURCE = "b" * 64
H_CONFIG = "c" * 64
H_RAW = "d" * 64
H_POLICY = "e" * 64
H_RECEIPT = "0" * 64


def reconcile_canonical(**kwargs):
    kwargs.setdefault("normalization_receipt_hash", H_RECEIPT)
    return _reconcile_canonical(**kwargs)


def attest_full_audit(result, receipts):
    original_generation_id = result.transcript.generation_id
    primary_refs = {
        evidence_id for token in result.transcript.tokens for evidence_id in token.evidence_ids
    }
    evidence_hash = result.transcript.evidence_hash
    intervals = sorted((span.start_ms, span.end_ms) for span in result.transcript.spans)
    coverage_payload = {
        "episode_id": result.transcript.episode_id,
        "normalized_audio_hash": result.transcript.normalized_audio_hash,
        "recognition_evidence_hash": evidence_hash,
        "intervals": intervals,
        "evidence_refs": sorted(primary_refs),
    }
    raw_hash = hash_object(coverage_payload)
    coverage = SpeechCoverageReceipt(
        status="completed",
        episode_id=result.transcript.episode_id,
        invocation_id="fixture-independent-coverage",
        analyzer="fixture-independent-coverage",
        analyzer_version="1",
        analyzer_config_hash=H_CONFIG,
        analyzer_code_hash=H_RAW,
        analyzer_runtime_hash=H_POLICY,
        method="fixture_intervals_v1",
        normalized_audio_hash=result.transcript.normalized_audio_hash,
        normalized_audio_size_bytes=1,
        normalized_audio_duration_ms=max(end for _, end in intervals),
        recognition_evidence_hash=evidence_hash,
        recognition_interval_hash=hash_object(intervals),
        coverage_tolerance_ms=0,
        minimum_uncovered_ms=1,
        raw_output=ArtifactDigest(
            uri=f"fixture://speech-coverage/{raw_hash}",
            sha256=raw_hash,
            size_bytes=1,
        ),
        raw_output_hash=raw_hash,
        activity_intervals=tuple(
            SpeechActivityInterval(
                id=f"activity-{index}",
                start_ms=start_ms,
                end_ms=end_ms,
            )
            for index, (start_ms, end_ms) in enumerate(intervals, start=1)
        ),
        passed=True,
    )
    covered = attest_speech_coverage(result, coverage)
    rebound = tuple(
        receipt.model_copy(update={"preaudit_generation_id": covered.transcript.generation_id})
        if receipt.preaudit_generation_id == original_generation_id
        else receipt
        for receipt in receipts
    )
    return _attest_full_audit(covered, rebound)


def _reference(
    *,
    reference_id: str,
    kind: str,
    digest: str,
    locator_kind: str,
    locator_value: str,
    title: str,
    trust_tier: str,
) -> ReferenceEvidence:
    excerpt = f"{title}：數位遊牧"
    excerpt_hash = __import__("hashlib").sha256(excerpt.encode("utf-8")).hexdigest()
    return ReferenceEvidence(
        id=reference_id,
        artifact=ReferenceArtifact(
            source_id=f"source-{reference_id}",
            kind=kind,
            source_format="text",
            digest=ArtifactDigest(uri=f"kb://{reference_id}", sha256=digest, size_bytes=100),
            extracted_text=ArtifactDigest(
                uri=f"reference-extraction://{reference_id}",
                sha256=excerpt_hash,
                size_bytes=len(excerpt.encode("utf-8")),
            ),
            extractor_name="fixture",
            extractor_version="1",
            extractor_config_hash=H_CONFIG,
            extractor_code_hash=H_RAW,
            extractor_runtime_hash=H_POLICY,
            offset_unit="unicode_scalar_v1",
            extraction_block_count=1,
            title=title,
            author="Fixture Author" if kind == "book" else None,
            publisher="Fixture Publisher" if kind == "book" else None,
            version="snapshot:1",
            trust_tier=trust_tier,
            authority=reference_authority_fixture(
                source_id=f"source-{reference_id}",
                kind=kind,
                title=title,
                author="Fixture Author" if kind == "book" else None,
                publisher="Fixture Publisher" if kind == "book" else None,
                version="snapshot:1",
                trust_tier=trust_tier,
            ),
        ),
        locator=ReferenceLocator(
            parts=(ReferenceLocatorPart(kind=locator_kind, value=locator_value),)
        ),
        extraction_block_index=0,
        extraction_block_hash=excerpt_hash,
        excerpt_start=0,
        excerpt_end=len(excerpt),
        excerpt=excerpt,
        excerpt_hash=excerpt_hash,
    )


def _evidence(
    *,
    adapter: str = "primary",
    raw_hash: str = H_RAW,
    text: str = "心理健康",
    confidence: float | None = 0.97,
    speaker: str | None = "speaker-1",
    invocation_id: str = "recognition-input",
    raw_uri: str | None = None,
    normalized_audio_hash: str = H_AUDIO,
) -> RecognitionEvidence:
    return RecognitionEvidence(
        episode_id="anji",
        invocation_id=invocation_id,
        adapter=adapter,
        model="fixture-v1",
        language="zh-Hant-TW",
        config_hash=H_CONFIG,
        raw_output=ArtifactDigest(
            uri=raw_uri or f"fixture://{adapter}/{raw_hash}",
            sha256=raw_hash,
            size_bytes=100,
        ),
        raw_output_hash=raw_hash,
        normalized_audio_hash=normalized_audio_hash,
        tokens=(
            EvidenceToken(
                id=f"{adapter}-token-1",
                text=text,
                start_ms=1000,
                end_ms=1800,
                confidence=confidence,
                speaker=speaker,
            ),
        ),
    )


def _multi_token_evidence(
    *,
    adapter: str,
    raw_hash: str,
    tokens: tuple[tuple[str, int, int], ...],
) -> RecognitionEvidence:
    return RecognitionEvidence(
        episode_id="anji",
        invocation_id="recognition-input",
        adapter=adapter,
        model="fixture-v1",
        language="zh-Hant-TW",
        config_hash=H_CONFIG,
        raw_output=ArtifactDigest(
            uri=f"fixture://{adapter}/{raw_hash}",
            sha256=raw_hash,
            size_bytes=100,
        ),
        raw_output_hash=raw_hash,
        normalized_audio_hash=H_AUDIO,
        tokens=tuple(
            EvidenceToken(
                id=f"{adapter}-token-{index}",
                text=text,
                start_ms=start_ms,
                end_ms=end_ms,
                confidence=0.97,
                speaker="speaker-1",
            )
            for index, (text, start_ms, end_ms) in enumerate(tokens, start=1)
        ),
    )


def test_same_evidence_produces_stable_ids_and_hashes() -> None:
    request = {
        "primary": _evidence(),
        "source_audio_hash": H_SOURCE,
        "normalization_receipt_hash": H_RECEIPT,
        "policy_hash": H_POLICY,
    }

    first = reconcile_canonical(**request)
    second = reconcile_canonical(**request)

    assert first.outcome == "needs_review"
    assert first.transcript.full_audit_receipt_set_hash is None
    assert first.transcript == second.transcript
    assert first.transcript.evidence_hash == second.transcript.evidence_hash
    assert first.transcript.content_hash == second.transcript.content_hash
    assert first.transcript.generation_id == second.transcript.generation_id
    assert first.transcript.spans[0].id == second.transcript.spans[0].id
    assert first.transcript.tokens[0].id == second.transcript.tokens[0].id
    assert first.transcript.tokens[0].evidence_ids


def test_evidence_identity_excludes_invocation_and_artifact_uri() -> None:
    first = reconcile_canonical(
        primary=_evidence(invocation_id="run-a", raw_uri="file:///workspace-a/raw.json"),
        source_audio_hash=H_SOURCE,
        policy_hash=H_POLICY,
    )
    relocated = reconcile_canonical(
        primary=_evidence(invocation_id="run-b", raw_uri="file:///workspace-b/raw.json"),
        source_audio_hash=H_SOURCE,
        policy_hash=H_POLICY,
    )

    assert first.transcript.evidence_hash == relocated.transcript.evidence_hash
    assert first.transcript.generation_id == relocated.transcript.generation_id


def test_missing_confidence_or_speaker_evidence_fails_closed() -> None:
    missing_confidence = reconcile_canonical(
        primary=_evidence(confidence=None),
        source_audio_hash=H_SOURCE,
        policy_hash=H_POLICY,
    )
    missing_speaker = reconcile_canonical(
        primary=_evidence(speaker=None),
        source_audio_hash=H_SOURCE,
        policy_hash=H_POLICY,
    )

    assert missing_confidence.outcome == "needs_review"
    assert missing_confidence.risks == ()
    assert len(missing_confidence.transcript.generation_warnings) == 1
    assert (
        missing_confidence.transcript.generation_warnings[0].code
        == "recognizer_confidence_unavailable"
    )
    assert missing_speaker.outcome == "needs_review"
    assert "speaker_unresolved" in {risk.issue.code for risk in missing_speaker.risks}


def _audit_receipt(result, span_ids: tuple[str, ...]) -> AudioAuditReceipt:
    spans = {span.id: span for span in result.transcript.spans}
    start_ms = spans[span_ids[0]].start_ms
    end_ms = spans[span_ids[-1]].end_ms
    return AudioAuditReceipt(
        id=f"audio-audit-{len(span_ids)}",
        episode_id=result.transcript.episode_id,
        preaudit_generation_id=result.transcript.generation_id,
        preaudit_content_hash=result.transcript.content_hash,
        evidence_hash=result.transcript.evidence_hash,
        reference_evidence_hash=result.transcript.reference_evidence_hash,
        policy_hash=result.transcript.policy_hash,
        normalized_audio_hash=result.transcript.normalized_audio_hash,
        target_span_ids=span_ids,
        window_start_ms=start_ms,
        window_end_ms=end_ms,
        clip_start_ms=start_ms,
        clip_end_ms=end_ms,
        work_packet_id="audio-work-1",
        request=ArtifactDigest(
            uri="generation-artifact://audio_audit/requests/request",
            sha256="1" * 64,
            size_bytes=1,
        ),
        response=ArtifactDigest(
            uri="generation-artifact://audio_audit/responses/response",
            sha256="2" * 64,
            size_bytes=1,
        ),
        clip=ArtifactDigest(
            uri="generation-artifact://audio_audit/clips/clip",
            sha256="3" * 64,
            size_bytes=1,
        ),
        adapter_name="fixture-audio-auditor",
        adapter_version="1",
        model="gold-listening",
        model_version="1",
        runtime_hash="3" * 64,
        adapter_code_hash="4" * 64,
        config_hash="5" * 64,
        adapter_identity_hash="3" * 64,
        status="confirmed",
        confidence=0.99,
        rationale="gold audio review",
        proposal_set_hash="4" * 64,
    )


def test_missing_confidence_is_one_warning_mitigated_only_by_exact_full_audit() -> None:
    evidence = RecognitionEvidence(
        **{
            **_multi_token_evidence(
                adapter="program",
                raw_hash=H_RAW,
                tokens=(("心理", 0, 400), ("健康", 400, 800), ("開始", 800, 1200)),
            ).model_dump(),
            "tokens": tuple(
                token.model_copy(update={"confidence": None})
                for token in _multi_token_evidence(
                    adapter="program",
                    raw_hash=H_RAW,
                    tokens=(("心理", 0, 400), ("健康", 400, 800), ("開始", 800, 1200)),
                ).tokens
            ),
        }
    )
    draft = reconcile_canonical(
        primary=evidence,
        source_audio_hash=H_SOURCE,
        policy_hash=H_POLICY,
    )
    span_ids = tuple(span.id for span in draft.transcript.spans)

    assert draft.outcome == "needs_review"
    assert draft.risks == ()
    assert draft.transcript.generation_warnings[0].affected_token_count == 3
    with pytest.raises(ValueError, match="exactly partition"):
        attest_full_audit(draft, (_audit_receipt(draft, span_ids[:-1]),))

    audited = attest_full_audit(draft, (_audit_receipt(draft, span_ids),))

    assert audited.outcome == "accepted"
    assert audited.transcript.generation_warnings[0].status == "mitigated"
    assert audited.transcript.full_audit_receipt_set_hash is not None
    assert audited.transcript.generation_id != draft.transcript.generation_id


def test_anji_sized_missing_confidence_does_not_expand_to_per_token_issues() -> None:
    token_count = 24_769
    raw = ArtifactDigest(uri="fixture://program/anji", sha256=H_RAW, size_bytes=100)
    evidence = RecognitionEvidence(
        episode_id="anji",
        invocation_id="recognition-anji",
        adapter="program-words",
        model="program-export-v1",
        language="zh-Hant-TW",
        config_hash=H_CONFIG,
        raw_output=raw,
        raw_output_hash=raw.sha256,
        normalized_audio_hash=H_AUDIO,
        tokens=tuple(
            EvidenceToken(
                id=f"program-{index}",
                text="字",
                start_ms=index,
                end_ms=index + 1,
                confidence=None,
                speaker="speaker-1",
            )
            for index in range(token_count)
        ),
    )

    result = reconcile_canonical(
        primary=evidence,
        source_audio_hash=H_SOURCE,
        policy_hash=H_POLICY,
    )

    assert result.risks == ()
    assert len(result.transcript.review_issues) == 0
    assert len(result.transcript.generation_warnings) == 1
    assert result.transcript.generation_warnings[0].affected_token_count == token_count
    assert result.outcome == "needs_review"


def test_primary_evidence_identity_is_not_rehashed_per_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agents.brook.podcast_subtitles import canonical as canonical_module

    identity_calls = 0
    original_identity = canonical_module._evidence_identity

    def counted_identity(evidence: RecognitionEvidence) -> str:
        nonlocal identity_calls
        identity_calls += 1
        return original_identity(evidence)

    monkeypatch.setattr(canonical_module, "_evidence_identity", counted_identity)
    raw = ArtifactDigest(uri="fixture://program/identity-cache", sha256=H_RAW, size_bytes=100)
    evidence = RecognitionEvidence(
        episode_id="identity-cache",
        invocation_id="recognition-identity-cache",
        adapter="program-words",
        model="program-export-v1",
        language="zh-Hant-TW",
        config_hash=H_CONFIG,
        raw_output=raw,
        raw_output_hash=raw.sha256,
        normalized_audio_hash=H_AUDIO,
        tokens=tuple(
            EvidenceToken(
                id=f"program-{index}",
                text="字",
                start_ms=index,
                end_ms=index + 1,
                confidence=None,
                speaker="speaker-1",
            )
            for index in range(64)
        ),
    )

    result = reconcile_canonical(
        primary=evidence,
        source_audio_hash=H_SOURCE,
        policy_hash=H_POLICY,
    )

    assert result.risks == ()
    assert identity_calls <= 3


def test_full_audit_does_not_clear_disagreement_or_coverage_gap() -> None:
    draft = reconcile_canonical(
        primary=_evidence(confidence=None, text="BA典禮"),
        corroborating=(_evidence(adapter="secondary", raw_hash="7" * 64, text="畢業典禮"),),
        source_audio_hash=H_SOURCE,
        policy_hash=H_POLICY,
    )
    audited = attest_full_audit(
        draft,
        (_audit_receipt(draft, tuple(span.id for span in draft.transcript.spans)),),
    )

    assert audited.outcome == "needs_review"
    assert "recognition_disagreement" in {risk.issue.code for risk in audited.risks}
    assert all(risk.issue.status == "unresolved" for risk in audited.risks)


def test_add_review_risk_reseals_then_decision_resolves(
    tmp_path: Path,
) -> None:
    preaudit = reconcile_canonical(
        primary=_evidence(),
        source_audio_hash=H_SOURCE,
        policy_hash=H_POLICY,
    )
    accepted = attest_full_audit(
        preaudit,
        (_audit_receipt(preaudit, tuple(span.id for span in preaudit.transcript.spans)),),
    )
    span_id = accepted.transcript.spans[0].id
    evidence_ids = accepted.transcript.tokens[0].evidence_ids
    risk = RiskRecord(
        issue=ReviewIssue(
            id="issue-corrector-found",
            risk="text",
            severity="medium",
            code="correction_proposal",
            span_ids=(span_id,),
            audio_evidence_ids=evidence_ids,
            candidates=("心理健康", "心理健康問題"),
        ),
        audio_span_ids=(span_id,),
        evidence_ids=evidence_ids,
    )

    reviewed = add_review_risks(accepted, (risk,))

    assert accepted.outcome == "accepted"
    assert reviewed.outcome == "needs_review"
    assert reviewed.transcript.status == "draft"
    assert reviewed.transcript.generation_id != accepted.transcript.generation_id

    ledger = CorrectionLedger(tmp_path / "added-risk.ndjson")
    resolved = resolve_canonical(
        reviewed,
        _decision(
            reviewed,
            event_id="confirm-corrector-risk",
            span_ids=(span_id,),
            action="confirm_original",
        ),
        ledger=ledger,
    )
    # Even confirm_original creates a new child Generation.  The parent's
    # full-audit receipt cannot attest that child; Module must audit the child
    # before it can become accepted.
    assert resolved.outcome == "needs_review"
    assert resolved.transcript.status == "draft"
    assert resolved.transcript.full_audit_receipt_set_hash is None
    assert resolved.transcript.review_issues == ()


def test_decision_resolves_only_explicit_issue_ids(tmp_path: Path) -> None:
    accepted = reconcile_canonical(
        primary=_evidence(),
        source_audio_hash=H_SOURCE,
        policy_hash=H_POLICY,
    )
    span_id = accepted.transcript.spans[0].id
    evidence_ids = accepted.transcript.tokens[0].evidence_ids

    def risk(issue_id: str, code: str) -> RiskRecord:
        return RiskRecord(
            issue=ReviewIssue(
                id=issue_id,
                risk="text",
                severity="medium",
                code=code,
                span_ids=(span_id,),
                audio_evidence_ids=evidence_ids,
                candidates=("心理健康",),
            ),
            audio_span_ids=(span_id,),
            evidence_ids=evidence_ids,
        )

    reviewed = add_review_risks(
        accepted,
        (risk("issue-one", "first"), risk("issue-two", "second")),
    )
    resolved_one = resolve_canonical(
        reviewed,
        _decision(
            reviewed,
            event_id="resolve-one-only",
            span_ids=(span_id,),
            action="confirm_original",
            issue_ids=("issue-one",),
        ),
        ledger=CorrectionLedger(tmp_path / "one-only.ndjson"),
    )

    statuses = {issue.id: issue.status for issue in resolved_one.transcript.review_issues}
    assert statuses == {"issue-one": "resolved", "issue-two": "unresolved"}
    assert resolved_one.outcome == "needs_review"


def test_recognition_disagreement_is_fail_closed_and_keeps_both_evidence_refs() -> None:
    secondary = _evidence(
        adapter="secondary",
        raw_hash="f" * 64,
        text="心理開始健康",
        confidence=0.96,
    )

    result = reconcile_canonical(
        primary=_evidence(text="心理健康開始"),
        corroborating=(secondary,),
        source_audio_hash=H_SOURCE,
        policy_hash=H_POLICY,
    )

    assert result.outcome == "needs_review"
    assert result.transcript.status == "draft"
    assert len(result.risks) == 1
    risk = result.risks[0]
    assert risk.issue.code == "recognition_disagreement"
    assert risk.issue.severity == "high"
    assert set(risk.issue.candidates) == {"心理健康開始", "心理開始健康"}
    assert len(risk.evidence_ids) == 2
    assert set(result.transcript.tokens[0].evidence_ids) == set(risk.evidence_ids)


def test_low_confidence_audio_evidence_requires_review() -> None:
    result = reconcile_canonical(
        primary=_evidence(text="數位遊牧", confidence=0.42),
        source_audio_hash=H_SOURCE,
        policy_hash=H_POLICY,
    )

    assert result.outcome == "needs_review"
    assert result.transcript.status == "draft"
    assert [risk.issue.code for risk in result.risks] == ["low_confidence"]
    assert result.risks[0].issue.severity == "high"


def test_code_switch_is_traced_without_blocking_high_confidence_audio() -> None:
    preaudit = reconcile_canonical(
        primary=_evidence(text="Traveling Village", confidence=0.97),
        source_audio_hash=H_SOURCE,
        policy_hash=H_POLICY,
    )
    result = attest_full_audit(
        preaudit,
        (_audit_receipt(preaudit, tuple(span.id for span in preaudit.transcript.spans)),),
    )

    assert result.outcome == "accepted"
    assert [(risk.issue.code, risk.issue.severity) for risk in result.risks] == [
        ("code_switch", "low")
    ]


def test_repetition_and_suspicious_tokens_are_material_risks() -> None:
    repeated = reconcile_canonical(
        primary=_evidence(text="有一個有一個", confidence=0.95),
        source_audio_hash=H_SOURCE,
        policy_hash=H_POLICY,
    )
    suspicious = reconcile_canonical(
        primary=_evidence(text="[UNK]數位遊牧", confidence=0.95),
        source_audio_hash=H_SOURCE,
        policy_hash=H_POLICY,
    )

    assert repeated.outcome == "needs_review"
    assert "adjacent_repetition" in {risk.issue.code for risk in repeated.risks}
    assert suspicious.outcome == "needs_review"
    assert "suspicious_token" in {risk.issue.code for risk in suspicious.risks}


def test_continuous_editorial_detector_is_wired_without_mutating_taiwan_wording() -> None:
    simplified = reconcile_canonical(
        primary=_evidence(text="这是错误。", confidence=0.95),
        source_audio_hash=H_SOURCE,
        policy_hash=H_POLICY,
    )
    taiwan_wording = reconcile_canonical(
        primary=_evidence(text="類型與對象", confidence=0.95),
        source_audio_hash=H_SOURCE,
        policy_hash=H_POLICY,
    )

    assert simplified.outcome == "needs_review"
    assert {risk.issue.code for risk in simplified.risks}.issuperset(
        {"simplified_chinese_suspected", "forbidden_punctuation"}
    )
    assert simplified.transcript.tokens[0].text == "这是错误。"
    assert taiwan_wording.transcript.tokens[0].text == "類型與對象"
    assert not {
        "simplified_chinese_suspected",
        "forbidden_punctuation",
    }.intersection(risk.issue.code for risk in taiwan_wording.risks)


def test_located_book_term_can_propose_but_not_auto_apply_a_correction() -> None:
    reference = _reference(
        reference_id="ref-book-1",
        kind="book",
        digest="1" * 64,
        locator_kind="page",
        locator_value="42",
        title="數位遊牧指南",
        trust_tier="authoritative",
    )
    hint = TermHint(
        id="term-digital-nomad",
        canonical_text="數位遊牧",
        aliases=("蘇味遊牧",),
        reference_ids=(reference.id,),
    )

    result = reconcile_canonical(
        primary=_evidence(text="蘇味遊牧", confidence=0.98),
        source_audio_hash=H_SOURCE,
        policy_hash=H_POLICY,
        references=(reference,),
        term_hints=(hint,),
    )

    assert result.outcome == "needs_review"
    assert result.transcript.tokens[0].text == "蘇味遊牧"
    reference_risk = next(
        risk for risk in result.risks if risk.issue.code == "reference_term_candidate"
    )
    assert set(reference_risk.issue.candidates) == {"蘇味遊牧", "數位遊牧"}
    assert reference_risk.supporting_reference_ids == (reference.id,)
    assert result.candidates[0].candidates[-1].reference_ids == (reference.id,)


def test_reference_audio_conflict_stays_unresolved_and_fail_closed() -> None:
    reference = _reference(
        reference_id="ref-report-1",
        kind="research_report",
        digest="2" * 64,
        locator_kind="other",
        locator_value="table 3, row 8",
        title="訪談名詞核對表",
        trust_tier="curated",
    )
    hint = TermHint(
        id="term-digital-nomad",
        canonical_text="數位遊牧",
        aliases=("蘇味遊牧",),
        reference_ids=(reference.id,),
    )

    result = reconcile_canonical(
        primary=_evidence(text="蘇味遊牧"),
        corroborating=(_evidence(adapter="secondary", raw_hash="3" * 64, text="蘇味遊牧"),),
        source_audio_hash=H_SOURCE,
        policy_hash=H_POLICY,
        references=(reference,),
        term_hints=(hint,),
    )

    conflict = next(risk for risk in result.risks if risk.issue.code == "reference_audio_conflict")
    assert result.outcome == "needs_review"
    assert conflict.issue.severity == "high"
    assert conflict.conflicting_reference_ids == (reference.id,)
    assert result.transcript.tokens[0].text == "蘇味遊牧"


def test_contextual_reference_can_propose_but_cannot_support_a_high_confidence_change() -> None:
    reference = _reference(
        reference_id="ref-outline-contextual",
        kind="interview_outline",
        digest="4" * 64,
        locator_kind="heading",
        locator_value="Question 3",
        title="訪綱",
        trust_tier="contextual",
    )
    hint = TermHint(
        id="guest-name",
        canonical_text="Mitch",
        aliases=("米奇",),
        reference_ids=(reference.id,),
    )

    result = reconcile_canonical(
        primary=_evidence(text="米奇"),
        source_audio_hash=H_SOURCE,
        policy_hash=H_POLICY,
        references=(reference,),
        term_hints=(hint,),
    )

    risk = next(risk for risk in result.risks if risk.issue.code == "reference_contextual_only")
    assert result.outcome == "needs_review"
    assert set(risk.issue.candidates) == {"米奇", "Mitch"}
    assert risk.supporting_reference_ids == (reference.id,)
    assert risk.conflicting_reference_ids == ()
    assert result.transcript.tokens[0].text == "米奇"


def test_two_to_one_tokenization_with_same_text_is_not_a_disagreement() -> None:
    primary = _multi_token_evidence(
        adapter="primary",
        raw_hash="5" * 64,
        tokens=(("心理", 1000, 1400), ("健康", 1400, 1800)),
    )
    secondary = _multi_token_evidence(
        adapter="secondary",
        raw_hash="6" * 64,
        tokens=(("心理健康", 1000, 1800),),
    )

    preaudit = reconcile_canonical(
        primary=primary,
        corroborating=(secondary,),
        source_audio_hash=H_SOURCE,
        policy_hash=H_POLICY,
    )
    result = attest_full_audit(
        preaudit,
        (_audit_receipt(preaudit, tuple(span.id for span in preaudit.transcript.spans)),),
    )

    assert result.outcome == "accepted"
    assert "recognition_disagreement" not in {risk.issue.code for risk in result.risks}
    assert [token.text for token in result.transcript.tokens] == ["心理", "健康"]
    assert all(len(token.evidence_ids) == 2 for token in result.transcript.tokens)


def test_one_to_two_tokenization_with_same_text_is_not_a_disagreement() -> None:
    primary = _multi_token_evidence(
        adapter="primary",
        raw_hash="7" * 64,
        tokens=(("心理健康", 1000, 1800),),
    )
    secondary = _multi_token_evidence(
        adapter="secondary",
        raw_hash="8" * 64,
        tokens=(("心理", 1000, 1400), ("健康", 1400, 1800)),
    )

    preaudit = reconcile_canonical(
        primary=primary,
        corroborating=(secondary,),
        source_audio_hash=H_SOURCE,
        policy_hash=H_POLICY,
    )
    result = attest_full_audit(
        preaudit,
        (_audit_receipt(preaudit, tuple(span.id for span in preaudit.transcript.spans)),),
    )

    assert result.outcome == "accepted"
    assert "recognition_disagreement" not in {risk.issue.code for risk in result.risks}
    assert len(result.transcript.tokens[0].evidence_ids) == 3


def test_true_multi_token_disagreement_targets_contiguous_stable_spans() -> None:
    primary = _multi_token_evidence(
        adapter="primary",
        raw_hash="9" * 64,
        tokens=(("心理", 1000, 1400), ("健康開始", 1400, 1800)),
    )
    secondary = _multi_token_evidence(
        adapter="secondary",
        raw_hash="0" * 64,
        tokens=(("心理開始健康", 1000, 1800),),
    )

    result = reconcile_canonical(
        primary=primary,
        corroborating=(secondary,),
        source_audio_hash=H_SOURCE,
        policy_hash=H_POLICY,
    )

    disagreement = next(
        risk for risk in result.risks if risk.issue.code == "recognition_disagreement"
    )
    expected_span_ids = tuple(span.id for span in result.transcript.spans)
    assert result.outcome == "needs_review"
    assert disagreement.issue.span_ids == expected_span_ids
    assert disagreement.audio_span_ids == expected_span_ids
    assert set(disagreement.issue.candidates) == {"心理健康開始", "心理開始健康"}


@pytest.mark.parametrize(
    ("primary_tokens", "secondary_tokens", "expected_candidate"),
    [
        (
            (("前", 100, 200), ("後", 400, 500)),
            (("多", 10, 90), ("前", 100, 200), ("後", 400, 500)),
            "多前",
        ),
        (
            (("前", 100, 200), ("後", 400, 500)),
            (("前", 100, 200), ("漏字", 220, 380), ("後", 400, 500)),
            "前漏字後",
        ),
        (
            (("前", 100, 200), ("後", 400, 500)),
            (("前", 100, 200), ("後", 400, 500), ("多", 510, 600)),
            "後多",
        ),
    ],
)
def test_corroborating_unmatched_tokens_are_fail_closed_coverage_gaps(
    primary_tokens: tuple[tuple[str, int, int], ...],
    secondary_tokens: tuple[tuple[str, int, int], ...],
    expected_candidate: str,
) -> None:
    result = reconcile_canonical(
        primary=_multi_token_evidence(adapter="primary", raw_hash="1" * 64, tokens=primary_tokens),
        corroborating=(
            _multi_token_evidence(adapter="secondary", raw_hash="2" * 64, tokens=secondary_tokens),
        ),
        source_audio_hash=H_SOURCE,
        policy_hash=H_POLICY,
    )

    gaps = [risk for risk in result.risks if risk.issue.code == "recognition_coverage_gap"]
    assert result.outcome == "needs_review"
    assert gaps
    assert expected_candidate in gaps[0].issue.candidates
    assert gaps[0].issue.severity == "high"


def test_primary_token_missing_from_corroborator_is_coverage_disagreement() -> None:
    primary = _multi_token_evidence(
        adapter="primary",
        raw_hash="3" * 64,
        tokens=(("都有", 100, 200), ("只有主辨識", 200, 400)),
    )
    secondary = _multi_token_evidence(
        adapter="secondary",
        raw_hash="4" * 64,
        tokens=(("都有", 100, 200),),
    )

    result = reconcile_canonical(
        primary=primary,
        corroborating=(secondary,),
        source_audio_hash=H_SOURCE,
        policy_hash=H_POLICY,
    )

    assert result.outcome == "needs_review"
    assert "recognition_coverage_disagreement" in {risk.issue.code for risk in result.risks}


def _decision(
    result,
    *,
    event_id: str,
    span_ids: tuple[str, ...],
    action: str = "replace",
    replacement_text: str | None = None,
    replacement_lexemes: tuple[str, ...] = (),
    replacement_alignment: tuple[ReplacementLexemeAlignment, ...] = (),
    fingerprint: str | None = None,
    issue_ids: tuple[str, ...] | None = None,
) -> CorrectionDecision:
    spans = {span.id: span for span in result.transcript.spans}
    tokens = {token.id: token for token in result.transcript.tokens}
    selected = [tokens[token_id] for span_id in span_ids for token_id in spans[span_id].token_ids]
    audio_evidence_ids = tuple(
        sorted({evidence_id for token in selected for evidence_id in token.evidence_ids})
    )
    return CorrectionDecision(
        event_id=event_id,
        episode_id=result.transcript.episode_id,
        generation_id=result.transcript.generation_id,
        target_span_ids=span_ids,
        target_start_ms=spans[span_ids[0]].start_ms,
        target_end_ms=spans[span_ids[-1]].end_ms,
        evidence_fingerprint=fingerprint or review_target_fingerprint(result.transcript, span_ids),
        audio_evidence_ids=audio_evidence_ids,
        issue_ids=(
            issue_ids
            if issue_ids is not None
            else tuple(
                issue.id
                for issue in result.transcript.review_issues
                if issue.status == "unresolved" and set(issue.span_ids).issubset(set(span_ids))
            )
        ),
        action=action,
        replacement_text=replacement_text,
        replacement_lexemes=replacement_lexemes,
        replacement_alignment=replacement_alignment,
        actor_kind="human",
        actor="修修",
        rationale="audio relisten",
        timestamp=datetime(2026, 8, 12, tzinfo=timezone.utc),
    )


def test_replace_decision_creates_new_revision_and_replays_without_recurrence(
    tmp_path: Path,
) -> None:
    primary = _evidence(text="哥大 BA 典禮", confidence=None)
    secondary = _evidence(
        adapter="secondary",
        raw_hash="7" * 64,
        text="哥大畢業典禮",
    )
    draft = reconcile_canonical(
        primary=primary,
        corroborating=(secondary,),
        source_audio_hash=H_SOURCE,
        policy_hash=H_POLICY,
    )
    draft = attest_full_audit(
        draft,
        (_audit_receipt(draft, tuple(span.id for span in draft.transcript.spans)),),
    )
    assert draft.transcript.generation_warnings
    assert all(warning.status == "mitigated" for warning in draft.transcript.generation_warnings)
    ledger = CorrectionLedger(tmp_path / "events.ndjson")
    span_ids = tuple(span.id for span in draft.transcript.spans)
    decision = _decision(
        draft,
        event_id="decision-ba",
        span_ids=span_ids,
        replacement_text="哥大畢業典禮",
        replacement_lexemes=("哥大", "畢業典禮"),
    )

    resolved = resolve_canonical(draft, decision, ledger=ledger)

    assert resolved.outcome == "needs_review"
    assert resolved.transcript.status == "draft"
    assert resolved.transcript.full_audit_receipt_set_hash is None
    assert all(
        warning.status == "requires_full_audit" and warning.mitigation_receipt_set_hash is None
        for warning in resolved.transcript.generation_warnings
    )
    assert resolved.transcript.revision == 2
    assert resolved.transcript.generation_id != draft.transcript.generation_id
    assert resolved.transcript.ledger_hash == ledger.head_hash
    assert "".join(token.text for token in resolved.transcript.tokens) == "哥大畢業典禮"
    assert tuple(token.text for token in resolved.transcript.tokens) == ("哥大", "畢業典禮")
    assert all(
        token.start_ms is None and token.end_ms is None for token in resolved.transcript.tokens
    )
    assert all(token.timing_basis == "coarse_span" for token in resolved.transcript.tokens)
    assert (
        resolved.transcript.spans[0].start_ms,
        resolved.transcript.spans[0].end_ms,
    ) == (
        draft.transcript.tokens[0].start_ms,
        draft.transcript.tokens[-1].end_ms,
    )
    assert all(issue.status == "resolved" for issue in resolved.transcript.review_issues)

    replayed = reconcile_canonical(
        primary=primary,
        corroborating=(secondary,),
        source_audio_hash=H_SOURCE,
        policy_hash=H_POLICY,
        ledger=ledger,
    )
    assert replayed.outcome == "needs_review"
    assert replayed.transcript.full_audit_receipt_set_hash is None
    assert "".join(token.text for token in replayed.transcript.tokens) == (
        "\u54e5\u5927\u7562\u696d\u5178\u79ae"
    )


def test_ledger_replays_across_new_generation_on_same_audio_and_fingerprint(
    tmp_path: Path,
) -> None:
    primary = _evidence(text="哥大 BA 典禮")
    secondary = _evidence(adapter="secondary", raw_hash="7" * 64, text="哥大畢業典禮")
    draft = reconcile_canonical(
        primary=primary,
        corroborating=(secondary,),
        source_audio_hash=H_SOURCE,
        policy_hash=H_POLICY,
    )
    draft = attest_full_audit(
        draft,
        (_audit_receipt(draft, tuple(span.id for span in draft.transcript.spans)),),
    )
    ledger = CorrectionLedger(tmp_path / "same-audio-events.ndjson")
    span_ids = tuple(span.id for span in draft.transcript.spans)
    resolved = resolve_canonical(
        draft,
        _decision(
            draft,
            event_id="same-audio-decision",
            span_ids=span_ids,
            replacement_text="哥大畢業典禮",
            replacement_lexemes=("哥大", "畢業典禮"),
        ),
        ledger=ledger,
    )

    replayed = _reconcile_canonical(
        primary=primary.model_copy(update={"invocation_id": "asr-rerun"}),
        corroborating=(secondary.model_copy(update={"invocation_id": "secondary-rerun"}),),
        source_audio_hash=H_SOURCE,
        normalization_receipt_hash="1" * 64,
        policy_hash=H_POLICY,
        ledger=ledger,
    )

    assert replayed.outcome == "needs_review"
    assert replayed.transcript.full_audit_receipt_set_hash is None
    assert "".join(token.text for token in replayed.transcript.tokens) == "哥大畢業典禮"
    assert replayed.transcript.generation_id != resolved.transcript.generation_id
    assert "ledger_replay_stale" not in {risk.issue.code for risk in replayed.risks}


@pytest.mark.parametrize("drift_kind", ("raw_output", "config"))
def test_ledger_replay_requires_current_target_recognition_evidence_membership(
    tmp_path: Path,
    drift_kind: str,
) -> None:
    primary = _evidence(text="original", confidence=0.42)
    draft = reconcile_canonical(
        primary=primary,
        source_audio_hash=H_SOURCE,
        policy_hash=H_POLICY,
    )
    ledger = CorrectionLedger(tmp_path / f"{drift_kind}-drift-events.ndjson")
    span_ids = tuple(span.id for span in draft.transcript.spans)
    decision = _decision(
        draft,
        event_id=f"{drift_kind}-bound-decision",
        span_ids=span_ids,
        replacement_text="corrected",
        replacement_lexemes=("corrected",),
    )
    resolve_canonical(draft, decision, ledger=ledger)

    if drift_kind == "raw_output":
        rerun_evidence = _evidence(
            text="original",
            confidence=0.42,
            raw_hash="1" * 64,
        )
    else:
        rerun_evidence = primary.model_copy(update={"config_hash": "1" * 64})
    replayed = reconcile_canonical(
        primary=rerun_evidence,
        source_audio_hash=H_SOURCE,
        policy_hash=H_POLICY,
        ledger=ledger,
    )

    stale = next(risk for risk in replayed.risks if risk.issue.code == "ledger_replay_stale")
    assert "".join(token.text for token in replayed.transcript.tokens) == "original"
    assert stale.issue.audio_evidence_ids == ()
    assert stale.evidence_ids == ()
    assert stale.issue.reference_evidence_ids == ()
    assert stale.supporting_reference_ids == ()


def test_ledger_replay_requires_current_reference_membership_and_replays_exact_r1(
    tmp_path: Path,
) -> None:
    reference_r1 = _reference(
        reference_id="ref-report-r1",
        kind="research_report",
        digest="1" * 64,
        locator_kind="other",
        locator_value="revision 1",
        title="R1",
        trust_tier="curated",
    )
    reference_r2 = _reference(
        reference_id="ref-report-r2",
        kind="research_report",
        digest="2" * 64,
        locator_kind="other",
        locator_value="revision 2",
        title="R2",
        trust_tier="curated",
    )
    primary = _evidence(text="original", confidence=0.42)
    draft = reconcile_canonical(
        primary=primary,
        source_audio_hash=H_SOURCE,
        policy_hash=H_POLICY,
        references=(reference_r1,),
    )
    ledger = CorrectionLedger(tmp_path / "reference-membership-events.ndjson")
    span_ids = tuple(span.id for span in draft.transcript.spans)
    base_decision = _decision(
        draft,
        event_id="reference-r1-bound-decision",
        span_ids=span_ids,
        replacement_text="corrected",
        replacement_lexemes=("corrected",),
    )
    decision = CorrectionDecision.model_validate(
        {
            **base_decision.model_dump(mode="python"),
            "reference_evidence_ids": (reference_r1.id,),
            "evidence_basis": "audio_and_reference",
        }
    )
    resolve_canonical(draft, decision, ledger=ledger)

    exact = reconcile_canonical(
        primary=primary,
        source_audio_hash=H_SOURCE,
        policy_hash=H_POLICY,
        references=(reference_r1,),
        ledger=ledger,
    )
    changed_reference = reconcile_canonical(
        primary=primary,
        source_audio_hash=H_SOURCE,
        policy_hash=H_POLICY,
        references=(reference_r2,),
        ledger=ledger,
    )

    assert "".join(token.text for token in exact.transcript.tokens) == "corrected"
    assert "ledger_replay_stale" not in {risk.issue.code for risk in exact.risks}
    stale = next(
        risk for risk in changed_reference.risks if risk.issue.code == "ledger_replay_stale"
    )
    assert "".join(token.text for token in changed_reference.transcript.tokens) == "original"
    assert stale.issue.audio_evidence_ids == decision.audio_evidence_ids
    assert stale.evidence_ids == decision.audio_evidence_ids
    assert stale.issue.reference_evidence_ids == ()
    assert stale.supporting_reference_ids == ()


def test_ledger_does_not_replay_across_normalized_audio_lineage(tmp_path: Path) -> None:
    original = _evidence(text="數位遊牧", confidence=0.42)
    draft = reconcile_canonical(
        primary=original,
        source_audio_hash=H_SOURCE,
        policy_hash=H_POLICY,
    )
    draft = attest_full_audit(
        draft,
        (_audit_receipt(draft, tuple(span.id for span in draft.transcript.spans)),),
    )
    ledger = CorrectionLedger(tmp_path / "cross-audio-events.ndjson")
    span_ids = tuple(span.id for span in draft.transcript.spans)
    resolve_canonical(
        draft,
        _decision(
            draft,
            event_id="original-audio-decision",
            span_ids=span_ids,
            action="confirm_original",
        ),
        ledger=ledger,
    )

    other_audio = reconcile_canonical(
        primary=_evidence(
            text="另一段音訊",
            confidence=0.42,
            normalized_audio_hash="9" * 64,
        ),
        source_audio_hash="8" * 64,
        policy_hash=H_POLICY,
        ledger=ledger,
    )

    assert other_audio.outcome == "needs_review"
    assert "".join(token.text for token in other_audio.transcript.tokens) == "另一段音訊"
    assert other_audio.transcript.ledger_hash != ledger.head_hash


def test_stale_same_audio_ledger_event_becomes_review_risk(tmp_path: Path) -> None:
    original = _evidence(text="原始辨識", confidence=0.42)
    draft = reconcile_canonical(
        primary=original,
        source_audio_hash=H_SOURCE,
        policy_hash=H_POLICY,
    )
    ledger = CorrectionLedger(tmp_path / "stale-replay-events.ndjson")
    span_ids = tuple(span.id for span in draft.transcript.spans)
    resolve_canonical(
        draft,
        _decision(
            draft,
            event_id="stale-replay-decision",
            span_ids=span_ids,
            action="confirm_original",
        ),
        ledger=ledger,
    )

    changed = reconcile_canonical(
        primary=_evidence(text="內容已不同", raw_hash="6" * 64),
        source_audio_hash=H_SOURCE,
        policy_hash=H_POLICY,
        ledger=ledger,
    )

    assert changed.outcome == "needs_review"
    stale = next(risk for risk in changed.risks if risk.issue.code == "ledger_replay_stale")
    assert "".join(token.text for token in changed.transcript.tokens) == "內容已不同"

    assert stale.issue.audio_evidence_ids == ()
    assert stale.evidence_ids == ()
    assert stale.issue.reference_evidence_ids == ()
    assert stale.supporting_reference_ids == ()


def test_multi_span_replacement_merges_deterministically_with_evidence_lineage(
    tmp_path: Path,
) -> None:
    primary = _multi_token_evidence(
        adapter="primary",
        raw_hash="8" * 64,
        tokens=(("跟他", 1000, 1400), ("有問", 1400, 1800)),
    )
    secondary = _multi_token_evidence(
        adapter="secondary",
        raw_hash="9" * 64,
        tokens=(("跟他遊牧", 1000, 1800),),
    )
    draft = reconcile_canonical(
        primary=primary,
        corroborating=(secondary,),
        source_audio_hash=H_SOURCE,
        policy_hash=H_POLICY,
    )
    draft = attest_full_audit(
        draft,
        (_audit_receipt(draft, tuple(span.id for span in draft.transcript.spans)),),
    )
    original_span_ids = tuple(span.id for span in draft.transcript.spans)
    original_evidence_ids = {
        evidence_id for token in draft.transcript.tokens for evidence_id in token.evidence_ids
    }
    ledger = CorrectionLedger(tmp_path / "events.ndjson")

    resolved = resolve_canonical(
        draft,
        _decision(
            draft,
            event_id="decision-youmu",
            span_ids=original_span_ids,
            replacement_text="跟他遊牧",
            replacement_lexemes=("跟他", "遊牧"),
        ),
        ledger=ledger,
    )

    assert resolved.outcome == "needs_review"
    assert resolved.transcript.status == "draft"
    assert resolved.transcript.full_audit_receipt_set_hash is None
    assert len(resolved.transcript.tokens) == 2
    assert tuple(token.text for token in resolved.transcript.tokens) == ("跟他", "遊牧")
    assert all(
        token.start_ms is None and token.end_ms is None for token in resolved.transcript.tokens
    )
    assert all(
        set(token.evidence_ids) == original_evidence_ids for token in resolved.transcript.tokens
    )
    assert resolved.transcript.spans[0].lineage == original_span_ids

    replayed = reconcile_canonical(
        primary=primary,
        corroborating=(secondary,),
        source_audio_hash=H_SOURCE,
        policy_hash=H_POLICY,
        ledger=ledger,
    )
    assert replayed.outcome == "needs_review"
    assert replayed.transcript.full_audit_receipt_set_hash is None
    assert tuple(token.text for token in replayed.transcript.tokens) == (
        "\u8ddf\u4ed6",
        "\u904a\u7267",
    )


@pytest.mark.parametrize(
    ("source_tokens", "replacement_lexemes"),
    (
        ((("原", 1000, 1400), ("錯", 1400, 1800)), ("正", "確")),
        (
            (("原", 1000, 1250), ("本", 1250, 1500), ("錯", 1500, 1800)),
            ("正確", "內容"),
        ),
        ((("原本錯", 1000, 1800),), ("正", "確", "內容")),
    ),
)
def test_replacement_cardinality_never_manufactures_lexeme_timestamps(
    tmp_path: Path,
    source_tokens: tuple[tuple[str, int, int], ...],
    replacement_lexemes: tuple[str, ...],
) -> None:
    primary = _multi_token_evidence(adapter="primary", raw_hash="5" * 64, tokens=source_tokens)
    secondary = _multi_token_evidence(
        adapter="secondary",
        raw_hash="6" * 64,
        tokens=(("正確內容", 1000, 1800),),
    )
    draft = reconcile_canonical(
        primary=primary,
        corroborating=(secondary,),
        source_audio_hash=H_SOURCE,
        policy_hash=H_POLICY,
    )
    target_spans = tuple(span.id for span in draft.transcript.spans)
    resolved = resolve_canonical(
        draft,
        _decision(
            draft,
            event_id=f"cardinality-{len(source_tokens)}-{len(replacement_lexemes)}",
            span_ids=target_spans,
            replacement_text="".join(replacement_lexemes),
            replacement_lexemes=replacement_lexemes,
        ),
        ledger=CorrectionLedger(tmp_path / "events.ndjson"),
    )

    assert tuple(token.text for token in resolved.transcript.tokens) == replacement_lexemes
    assert len({token.id for token in resolved.transcript.tokens}) == len(replacement_lexemes)
    assert all(
        token.timing_basis == "coarse_span" and token.start_ms is None and token.end_ms is None
        for token in resolved.transcript.tokens
    )
    assert resolved.transcript.spans[0].alignment == "coarse"
    assert (
        resolved.transcript.spans[0].start_ms,
        resolved.transcript.spans[0].end_ms,
    ) == (1000, 1800)


def test_generic_evidence_cannot_authorize_exact_replacement_boundaries(
    tmp_path: Path,
) -> None:
    draft = reconcile_canonical(
        primary=_evidence(text="原本錯"),
        corroborating=(_evidence(adapter="secondary", raw_hash="7" * 64, text="正確內容"),),
        source_audio_hash=H_SOURCE,
        policy_hash=H_POLICY,
    )
    span_ids = tuple(span.id for span in draft.transcript.spans)
    evidence_id = draft.transcript.tokens[0].evidence_ids[0]
    alignment = (
        ReplacementLexemeAlignment(
            lexeme="正確",
            start_ms=1000,
            end_ms=1400,
            method="forced_alignment",
            evidence_ids=(evidence_id,),
        ),
        ReplacementLexemeAlignment(
            lexeme="內容",
            start_ms=1400,
            end_ms=1800,
            method="forced_alignment",
            evidence_ids=(evidence_id,),
        ),
    )
    decision = _decision(
        draft,
        event_id="forced-aligned",
        span_ids=span_ids,
        replacement_text="正確內容",
        replacement_lexemes=("正確", "內容"),
    )
    forged = decision.model_copy(update={"replacement_alignment": alignment})
    ledger = CorrectionLedger(tmp_path / "events.ndjson")

    with pytest.raises(ValueError, match="typed ReplacementAlignmentReceipt"):
        resolve_canonical(draft, forged, ledger=ledger)
    assert ledger.entries() == ()


def test_stale_decision_is_rejected_before_ledger_append(tmp_path: Path) -> None:
    draft = reconcile_canonical(
        primary=_evidence(text="數位遊牧", confidence=0.42),
        source_audio_hash=H_SOURCE,
        policy_hash=H_POLICY,
    )
    span_ids = tuple(span.id for span in draft.transcript.spans)
    decision = _decision(
        draft,
        event_id="stale-decision",
        span_ids=span_ids,
        action="confirm_original",
        fingerprint="f" * 64,
    )
    ledger = CorrectionLedger(tmp_path / "events.ndjson")

    with pytest.raises(StaleFingerprintError):
        resolve_canonical(draft, decision, ledger=ledger)
    assert ledger.entries() == ()


def test_decision_with_fake_or_out_of_range_audio_evidence_is_rejected(
    tmp_path: Path,
) -> None:
    draft = reconcile_canonical(
        primary=_evidence(text="數位遊牧", confidence=0.42),
        source_audio_hash=H_SOURCE,
        policy_hash=H_POLICY,
    )
    span_ids = tuple(span.id for span in draft.transcript.spans)
    malicious = _decision(
        draft,
        event_id="fake-evidence-decision",
        span_ids=span_ids,
        replacement_text="任意改字",
        replacement_lexemes=("任意", "改字"),
    ).model_copy(update={"audio_evidence_ids": ("evidence:fake:outside",)})
    ledger = CorrectionLedger(tmp_path / "malicious.ndjson")

    with pytest.raises(ValueError, match="outside target spans"):
        resolve_canonical(draft, malicious, ledger=ledger)
    assert ledger.entries() == ()


def test_same_audio_range_has_stable_span_id_across_decision_event_ids(
    tmp_path: Path,
) -> None:
    draft = reconcile_canonical(
        primary=_evidence(text="蘇味遊牧", confidence=0.42),
        source_audio_hash=H_SOURCE,
        policy_hash=H_POLICY,
    )
    span_ids = tuple(span.id for span in draft.transcript.spans)

    first = resolve_canonical(
        draft,
        _decision(
            draft,
            event_id="event-a",
            span_ids=span_ids,
            replacement_text="數位遊牧",
            replacement_lexemes=("數位", "遊牧"),
        ),
        ledger=CorrectionLedger(tmp_path / "event-a.ndjson"),
    )
    second = resolve_canonical(
        draft,
        _decision(
            draft,
            event_id="event-b",
            span_ids=span_ids,
            replacement_text="數位遊牧",
            replacement_lexemes=("數位", "遊牧"),
        ),
        ledger=CorrectionLedger(tmp_path / "event-b.ndjson"),
    )

    assert first.transcript.spans[0].id == second.transcript.spans[0].id
    assert first.transcript.spans[0].id == draft.transcript.spans[0].id


def test_anji_mental_health_gold_remains_needs_review() -> None:
    fixture = json.loads(
        Path("tests/fixtures/podcast_subtitles_v2/anji_review_gold.v1.json").read_text(
            encoding="utf-8"
        )
    )["cases"][0]
    hypotheses = tuple(fixture["candidate_texts"])
    primary = _evidence(text=hypotheses[0])
    corroborating = tuple(
        _evidence(
            adapter=f"review-{index}",
            raw_hash=str(index) * 64,
            text=text,
        )
        for index, text in enumerate(hypotheses[1:], start=1)
    )

    result = reconcile_canonical(
        primary=primary,
        corroborating=corroborating,
        source_audio_hash=H_SOURCE,
        policy_hash=H_POLICY,
    )

    assert fixture["expected_outcome"] == "needs_review"
    assert result.outcome == "needs_review"
    disagreement = next(
        risk for risk in result.risks if risk.issue.code == "recognition_disagreement"
    )
    assert disagreement.issue.severity in {"high", "blocking"}
    assert set(disagreement.issue.candidates) == set(hypotheses)
