from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

import agents.brook.podcast_subtitles.adapters.correction as correction_adapter_module
from agents.brook.podcast_subtitles.adapters import LLMCorrectorAdapter
from agents.brook.podcast_subtitles.canonical import (
    add_correction_proposals,
    reconcile_canonical,
)
from agents.brook.podcast_subtitles.hashing import canonical_json_bytes, sha256_bytes
from agents.brook.podcast_subtitles.ports import (
    AdapterIntegrityError,
    AdapterUnavailableError,
    AdapterWorkPending,
    CorrectionRequest,
    CorrectionRunResult,
)
from shared.schemas.podcast_subtitles_v2 import (
    ArtifactDigest,
    EvidenceToken,
    RecognitionEvidence,
    ReferenceArtifact,
    ReferenceEvidence,
    ReferenceLocator,
    ReferenceLocatorPart,
    ReviewIssue,
    reference_evidence_set_hash,
)
from tests.agents.brook.podcast_subtitles.reference_authority_fixtures import (
    reference_authority_fixture,
)

H_AUDIO = "a" * 64
H_SOURCE = "b" * 64
H_CONFIG = "c" * 64
H_RAW = "d" * 64
H_POLICY = "e" * 64
H_RECEIPT = "f" * 64


def _evidence(
    tokens: tuple[tuple[str, str, int, int], ...] = (("raw-1", "五物之物", 100, 500),),
) -> RecognitionEvidence:
    return RecognitionEvidence(
        episode_id="episode-1",
        invocation_id="asr-run-1",
        adapter="fixture-asr",
        model="fixture-v1",
        language="zh-Hant-TW",
        config_hash=H_CONFIG,
        raw_output=ArtifactDigest(uri="fixture://raw", sha256=H_RAW, size_bytes=10),
        raw_output_hash=H_RAW,
        normalized_audio_hash=H_AUDIO,
        tokens=tuple(
            EvidenceToken(
                id=token_id,
                text=text,
                start_ms=start_ms,
                end_ms=end_ms,
                confidence=0.96,
                speaker="speaker-1",
            )
            for token_id, text, start_ms, end_ms in tokens
        ),
    )


def _canonical(evidence: RecognitionEvidence | None = None):
    evidence = evidence or _evidence()
    return reconcile_canonical(
        primary=evidence,
        source_audio_hash=H_SOURCE,
        normalization_receipt_hash=H_RECEIPT,
        policy_hash=H_POLICY,
    )


def _reference(
    *,
    reference_id: str = "ref-book-1",
    kind: str = "book",
    trust_tier: str = "authoritative",
) -> ReferenceEvidence:
    excerpt = "作者在《無路之路》第三章定義這個書名"
    excerpt_hash = hashlib.sha256(excerpt.encode("utf-8")).hexdigest()
    return ReferenceEvidence(
        id=reference_id,
        artifact=ReferenceArtifact(
            source_id=f"source-{reference_id}",
            kind=kind,
            source_format="text",
            digest=ArtifactDigest(uri=f"kb://{reference_id}", sha256="1" * 64, size_bytes=100),
            extracted_text=ArtifactDigest(
                uri=f"reference-extraction://{reference_id}",
                sha256=excerpt_hash,
                size_bytes=len(excerpt.encode("utf-8")),
            ),
            extractor_name="fixture",
            extractor_version="1",
            extractor_config_hash="2" * 64,
            extractor_code_hash="3" * 64,
            extractor_runtime_hash="4" * 64,
            offset_unit="unicode_scalar_v1",
            extraction_block_count=1,
            title="《無路之路》",
            author="作者",
            publisher="Fixture Publisher" if kind == "book" else None,
            version="edition:1",
            trust_tier=trust_tier,
            authority=reference_authority_fixture(
                source_id=f"source-{reference_id}",
                kind=kind,
                title="《無路之路》",
                author="作者",
                publisher="Fixture Publisher" if kind == "book" else None,
                version="edition:1",
                trust_tier=trust_tier,
            ),
        ),
        locator=ReferenceLocator(
            parts=(
                ReferenceLocatorPart(kind="chapter", value="3"),
                ReferenceLocatorPart(kind="page", value="42"),
            )
        ),
        extraction_block_index=0,
        extraction_block_hash=excerpt_hash,
        excerpt_start=0,
        excerpt_end=len(excerpt),
        excerpt=excerpt,
        excerpt_hash=excerpt_hash,
    )


def _correction_request(*, reference: ReferenceEvidence | None = None) -> CorrectionRequest:
    evidence = _evidence()
    result = _canonical(evidence)
    return CorrectionRequest(
        episode_id="episode-1",
        generation_id=result.transcript.generation_id,
        mode="full_audit",
        transcript=result.transcript,
        target_span_ids=tuple(span.id for span in result.transcript.spans),
        evidence=(evidence,),
        reference_evidence=(reference,) if reference else (),
    )


def _correction_payload(packet: dict[str, object], **overrides: object) -> dict[str, object]:
    span = packet["spans"][0]
    proposal = {
        "audio_span_ids": (span["id"],),
        "evidence_token_ids": tuple(span["evidence_token_ids"]),
        "observed_text": span["observed_text"],
        "candidate_text": "《無路之路》",
        "confidence": 0.91,
        "rationale": "音訊近似且作者書籍提供精確書名",
        "reference_evidence_ids": (),
        "evidence_basis": "recognition",
    }
    proposal.update(overrides)
    return {
        "schema_version": 1,
        "work_packet_id": packet["work_packet_id"],
        "proposals": [proposal],
    }


def test_corrector_located_author_book_proposal_is_traceable_and_not_applied() -> None:
    reference = _reference()
    request = _correction_request(reference=reference)
    adapter = LLMCorrectorAdapter(model="codex", model_version="5.6-sol")
    packet = adapter.export_work_packet(request)

    proposals = adapter.import_work_result(
        request,
        _correction_payload(
            packet,
            reference_evidence_ids=(reference.id,),
            evidence_basis="recognition_and_reference",
        ),
    )

    assert len(proposals) == 1
    assert proposals[0].reference_evidence_ids == (reference.id,)
    assert proposals[0].evidence_token_ids == ("raw-1",)
    assert request.transcript.tokens[0].text == "五物之物"
    assert packet["reference_evidence"][0]["locator"]
    assert "不可信的引用資料" in packet["reference_data_notice"]


def test_corrector_binds_available_reference_set_separately_from_presented_subset() -> None:
    presented = _reference()
    withheld = presented.model_copy(update={"id": "reference-book-2"})
    request = replace(
        _correction_request(reference=presented),
        available_reference_evidence=(presented, withheld),
    )
    adapter = LLMCorrectorAdapter(model="codex", model_version="5.6-sol")

    packet = adapter.export_work_packet(request)

    assert packet["available_reference_evidence_ids"] == [presented.id, withheld.id]
    assert packet["available_reference_evidence_hash"] == reference_evidence_set_hash(
        (presented, withheld)
    )
    assert packet["presented_reference_evidence_ids"] == [presented.id]
    assert packet["presented_reference_evidence_hash"] == reference_evidence_set_hash((presented,))
    assert [item["id"] for item in packet["reference_evidence"]] == [presented.id]


def test_corrector_packet_uses_exact_module_presented_reference_tuple() -> None:
    """Issue citations must not silently narrow the Module-owned least context."""

    first = _reference(reference_id="reference-book-1")
    second = _reference(reference_id="reference-book-2")
    base = _correction_request(reference=first)
    span_id = base.transcript.spans[0].id
    issue = ReviewIssue(
        id="issue-only-cites-first-reference",
        risk="text",
        severity="medium",
        code="reference_spelling_candidate",
        span_ids=(span_id,),
        reference_evidence_ids=(first.id,),
    )
    transcript = base.transcript.model_copy(update={"review_issues": (issue,)})
    request = replace(
        base,
        transcript=transcript,
        generation_id=transcript.generation_id,
        review_issues=(issue,),
        reference_evidence=(first, second),
        available_reference_evidence=(first, second),
    )

    packet = LLMCorrectorAdapter(model="codex", model_version="5.6-sol").export_work_packet(request)

    assert packet["presented_reference_evidence_ids"] == [first.id, second.id]
    assert packet["presented_reference_evidence_hash"] == reference_evidence_set_hash(
        (first, second)
    )
    assert [item["id"] for item in packet["reference_evidence"]] == [
        first.id,
        second.id,
    ]


@pytest.mark.parametrize(
    "tamper_kind",
    ("reorder", "duplicate", "unknown", "scope_drift"),
)
def test_corrector_rejects_packet_reference_binding_tamper(
    monkeypatch: pytest.MonkeyPatch,
    tamper_kind: str,
) -> None:
    first = _reference(reference_id="reference-book-1")
    second = _reference(reference_id="reference-book-2")
    request = replace(
        _correction_request(reference=first),
        reference_evidence=(first, second),
        available_reference_evidence=(first, second),
    )
    adapter = LLMCorrectorAdapter(model="codex", model_version="5.6-sol")
    packet = adapter.export_work_packet(request)
    forged = json.loads(canonical_json_bytes(packet))
    if tamper_kind == "reorder":
        forged["reference_evidence"].reverse()
    elif tamper_kind == "duplicate":
        forged["presented_reference_evidence_ids"] = [first.id, first.id]
    elif tamper_kind == "unknown":
        forged["reference_evidence"][1]["id"] = "reference-unknown"
        forged["presented_reference_evidence_ids"][1] = "reference-unknown"
    else:
        forged["reference_evidence"] = forged["reference_evidence"][:1]
        forged["presented_reference_evidence_ids"] = [first.id]
        forged["presented_reference_evidence_hash"] = reference_evidence_set_hash((first,))
    monkeypatch.setattr(adapter, "export_work_packets", lambda _request: (forged,))

    with pytest.raises(AdapterIntegrityError, match="must match exactly"):
        adapter.import_work_results_with_receipts(
            request,
            (
                {
                    "schema_version": 1,
                    "work_packet_id": forged["work_packet_id"],
                    "proposals": [],
                },
            ),
        )


def test_corrector_replay_rejects_packet_excerpt_removed_while_receipt_claims_it() -> None:
    first = _reference(reference_id="reference-book-1")
    second = _reference(reference_id="reference-book-2")
    request = replace(
        _correction_request(reference=first),
        reference_evidence=(first, second),
        available_reference_evidence=(first, second),
    )
    adapter = LLMCorrectorAdapter(model="codex", model_version="5.6-sol")
    packet = adapter.export_work_packet(request)
    run = adapter.import_work_results_with_receipts(
        request,
        (
            {
                "schema_version": 1,
                "work_packet_id": packet["work_packet_id"],
                "proposals": [],
            },
        ),
    )
    forged_packet = json.loads(run.request_bytes[0])
    forged_packet["reference_evidence"] = forged_packet["reference_evidence"][:1]
    forged_request_bytes = canonical_json_bytes(forged_packet)
    forged_receipt = replace(
        run.execution_receipts[0],
        request=ArtifactDigest(
            uri=(f"generation-artifact://correction/requests/{sha256_bytes(forged_request_bytes)}"),
            sha256=sha256_bytes(forged_request_bytes),
            size_bytes=len(forged_request_bytes),
        ),
    )
    assert forged_receipt.presented_reference_evidence_ids == (first.id, second.id)

    with pytest.raises(AdapterIntegrityError, match="does not replay"):
        adapter.replay(
            request,
            proposals=run.proposals,
            execution_receipts=(forged_receipt,),
            request_bytes=(forged_request_bytes,),
            response_bytes=run.response_bytes,
        )


def test_corrector_rejects_hallucinated_ids_or_reference_only_text_change() -> None:
    reference = _reference(kind="interview_outline", trust_tier="contextual")
    request = _correction_request(reference=reference)
    adapter = LLMCorrectorAdapter(model="codex", model_version="5.6-sol")
    packet = adapter.export_work_packet(request)

    with pytest.raises(AdapterIntegrityError, match="hallucinated Evidence"):
        adapter.import_work_result(
            request,
            _correction_payload(packet, evidence_token_ids=("invented-token",)),
        )
    with pytest.raises(AdapterIntegrityError, match="alone cannot support"):
        adapter.import_work_result(
            request,
            _correction_payload(
                packet,
                reference_evidence_ids=(reference.id,),
                evidence_basis="reference_only",
            ),
        )


@pytest.mark.parametrize(
    ("kind", "trust_tier"),
    [("research_report", "curated"), ("interview_outline", "contextual")],
)
def test_research_or_outline_proposal_stays_fail_closed_review(
    kind: str,
    trust_tier: str,
) -> None:
    reference = _reference(kind=kind, trust_tier=trust_tier)
    request = _correction_request(reference=reference)
    adapter = LLMCorrectorAdapter(model="codex", model_version="5.6-sol")
    packet = adapter.export_work_packet(request)
    proposals = adapter.import_work_result(
        request,
        _correction_payload(
            packet,
            reference_evidence_ids=(reference.id,),
            evidence_basis="recognition_and_reference",
        ),
    )

    reviewed = add_correction_proposals(
        _canonical(request.evidence[0]),
        proposals,
        allowed_reference_ids=(reference.id,),
    )

    assert reviewed.outcome == "needs_review"
    assert reviewed.transcript.tokens[0].text == "五物之物"
    assert reviewed.transcript.review_issues[-1].reference_evidence_ids == (reference.id,)


def test_corrector_default_never_calls_paid_api() -> None:
    adapter = LLMCorrectorAdapter(model="claude-opus", model_version="4.7")
    with pytest.raises(AdapterUnavailableError, match="export_work_packets"):
        adapter.propose(_correction_request())


def test_corrector_workspace_emits_pending_then_strictly_resumes(tmp_path: Path) -> None:
    request = _correction_request()
    adapter = LLMCorrectorAdapter(
        model="codex",
        model_version="5.6-sol",
        workspace_root=tmp_path / "work",
    )

    with pytest.raises(AdapterWorkPending) as pending:
        adapter.propose(request)
    packet_path = pending.value.packet_paths[0]
    response_path = pending.value.response_paths[0]
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    exact_response = (
        json.dumps(_correction_payload(packet), ensure_ascii=False, indent=2).encode("utf-8")
        + b"\n"
    )
    response_path.write_bytes(exact_response)

    run = adapter.propose_with_receipt(request)
    resumed = run.proposals

    assert resumed[0].candidate_text == "《無路之路》"
    assert packet_path.exists()
    assert response_path.exists()
    assert len(run.execution_receipts) == 1
    receipt = run.execution_receipts[0]
    assert receipt.target_span_ids == request.target_span_ids
    assert receipt.proposal_ids == (resumed[0].id,)
    assert run.request_bytes == (packet_path.read_bytes(),)
    assert run.response_bytes == (exact_response,)
    assert receipt.request.sha256 == sha256_bytes(packet_path.read_bytes())
    assert receipt.response.sha256 == sha256_bytes(exact_response)
    assert run.artifacts[receipt.request.uri.removeprefix("generation-artifact://")] == (
        packet_path.read_bytes()
    )
    assert run.artifacts[receipt.response.uri.removeprefix("generation-artifact://")] == (
        exact_response
    )
    assert receipt.adapter_identity == adapter.identity
    assert packet["adapter_identity_hash"] == adapter.identity.content_hash

    stale = _correction_payload(packet)
    stale["work_packet_id"] = "correction-work-stale"
    response_path.write_text(json.dumps(stale, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(AdapterIntegrityError, match="missing Corrector result"):
        adapter.propose(request)


def test_corrector_context_is_bounded_and_cannot_be_targeted() -> None:
    evidence = _evidence(
        (
            ("raw-1", "前文", 100, 200),
            ("raw-2", "五物之物", 200, 500),
            ("raw-3", "後文", 500, 700),
        )
    )
    result = _canonical(evidence)
    request = CorrectionRequest(
        episode_id="episode-1",
        generation_id=result.transcript.generation_id,
        mode="full_audit",
        transcript=result.transcript,
        target_span_ids=(result.transcript.spans[1].id,),
        evidence=(evidence,),
    )
    adapter = LLMCorrectorAdapter(
        model="codex",
        model_version="5.6-sol",
        context_spans_per_side=1,
    )
    packet = adapter.export_work_packet(request)

    assert len(packet["context_before"]) == 1
    assert len(packet["context_after"]) == 1
    assert packet["context_before"][0]["non_target_context"] is True
    with pytest.raises(AdapterIntegrityError, match="Audio Span IDs"):
        adapter.import_work_result(
            request,
            _correction_payload(
                packet,
                audio_span_ids=(packet["context_before"][0]["id"],),
                evidence_token_ids=("raw-1",),
                observed_text="前文",
            ),
        )


def test_corrector_injected_runner_is_deterministic() -> None:
    request = _correction_request()

    def runner(_prompt: str, **kwargs: object) -> object:
        packet = adapter.export_work_packet(request)
        assert kwargs["model"] == "codex"
        return _correction_payload(packet)

    adapter = LLMCorrectorAdapter(model="codex", model_version="5.6-sol", runner=runner)
    first = adapter.propose_with_receipt(request)
    second = adapter.propose_with_receipt(request)
    assert first == second
    assert first.execution_receipts[0].request_hash
    assert first.execution_receipts[0].response_hash


def test_corrector_runner_object_response_has_explicit_canonical_byte_policy() -> None:
    request = _correction_request()
    raw_response: dict[str, object] = {}

    def runner(prompt: str, **_kwargs: object) -> object:
        packet = json.loads(prompt)
        raw_response.update(_correction_payload(packet))
        return raw_response

    adapter = LLMCorrectorAdapter(model="codex", model_version="5.6-sol", runner=runner)
    run = adapter.propose_with_receipt(request)

    assert run.response_bytes == (canonical_json_bytes(raw_response),)
    assert run.execution_receipts[0].response.sha256 == sha256_bytes(
        canonical_json_bytes(raw_response)
    )


def test_corrector_measures_code_runtime_and_config_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = LLMCorrectorAdapter(model="codex", model_version="5.6-sol")
    changed_config = LLMCorrectorAdapter(
        model="codex",
        model_version="5.6-sol",
        max_reference_excerpts_per_packet=7,
    )

    assert first.identity.adapter_code_hash
    assert first.identity.runtime_hash
    assert first.identity.config_hash != changed_config.identity.config_hash
    assert first.identity.execution_mode == "subscription"

    monkeypatch.setattr(correction_adapter_module, "hash_file", lambda _path: "0" * 64)
    with pytest.raises(AdapterIntegrityError, match="code changed"):
        _ = first.identity


def test_corrector_empty_strict_response_still_has_execution_receipt() -> None:
    request = _correction_request()
    adapter = LLMCorrectorAdapter(model="codex", model_version="5.6-sol")
    packet = adapter.export_work_packet(request)

    run = adapter.import_work_result_with_receipt(
        request,
        {
            "schema_version": 1,
            "work_packet_id": packet["work_packet_id"],
            "proposals": [],
        },
    )

    assert run.proposals == ()
    assert len(run.execution_receipts) == 1
    receipt = run.execution_receipts[0]
    assert receipt.proposal_ids == ()
    assert run.request_bytes and run.response_bytes
    assert receipt.request.sha256 == sha256_bytes(run.request_bytes[0])
    assert receipt.response.sha256 == sha256_bytes(run.response_bytes[0])


def _correction_response_bytes(packet: dict[str, object]) -> bytes:
    return (json.dumps(_correction_payload(packet), ensure_ascii=False, indent=2) + "\n").encode(
        "utf-8"
    )


def test_corrector_fresh_adapter_replays_exact_bytes_without_calling_runner() -> None:
    request = _correction_request()
    runner_calls = 0

    def forbidden_runner(*_args: object, **_kwargs: object) -> object:
        nonlocal runner_calls
        runner_calls += 1
        raise AssertionError("replay must never call the Corrector provider")

    original = LLMCorrectorAdapter(
        model="codex",
        model_version="5.6-sol",
        runner=forbidden_runner,
    )
    packet = original.export_work_packet(request)
    run = original.import_work_results_with_receipts(
        request,
        (_correction_response_bytes(packet),),
    )
    fresh = LLMCorrectorAdapter(
        model="codex",
        model_version="5.6-sol",
        runner=forbidden_runner,
    )

    replayed = fresh.replay(
        request,
        proposals=run.proposals,
        execution_receipts=run.execution_receipts,
        request_bytes=run.request_bytes,
        response_bytes=run.response_bytes,
    )

    assert replayed == run
    assert runner_calls == 0


@pytest.mark.parametrize("tampered_field", ["request", "response"])
def test_corrector_request_or_response_byte_tamper_fails_replay(
    tampered_field: str,
) -> None:
    request = _correction_request()
    adapter = LLMCorrectorAdapter(model="codex", model_version="5.6-sol")
    packet = adapter.export_work_packet(request)
    run = adapter.import_work_results_with_receipts(
        request,
        (_correction_response_bytes(packet),),
    )
    replay_kwargs = {
        "proposals": run.proposals,
        "execution_receipts": run.execution_receipts,
        "request_bytes": run.request_bytes,
        "response_bytes": run.response_bytes,
    }
    replay_kwargs[f"{tampered_field}_bytes"] = (replay_kwargs[f"{tampered_field}_bytes"][0] + b" ",)

    with pytest.raises(AdapterIntegrityError, match="stored Corrector execution proof"):
        adapter.replay(request, **replay_kwargs)


def test_corrector_identity_and_typed_proposal_rewrite_fail_replay() -> None:
    request = _correction_request()
    adapter = LLMCorrectorAdapter(model="codex", model_version="5.6-sol")
    packet = adapter.export_work_packet(request)
    run = adapter.import_work_results_with_receipts(
        request,
        (_correction_response_bytes(packet),),
    )
    drifted = LLMCorrectorAdapter(
        model="codex",
        model_version="5.6-sol",
        context_spans_per_side=9,
    )

    with pytest.raises(AdapterIntegrityError, match="identity drifted"):
        drifted.replay(
            request,
            proposals=run.proposals,
            execution_receipts=run.execution_receipts,
            request_bytes=run.request_bytes,
            response_bytes=run.response_bytes,
        )

    rewritten = replace(run.proposals[0], candidate_text="偽造的候選文字")
    with pytest.raises(AdapterIntegrityError, match="does not replay"):
        adapter.replay(
            request,
            proposals=(rewritten,),
            execution_receipts=run.execution_receipts,
            request_bytes=run.request_bytes,
            response_bytes=run.response_bytes,
        )


def test_corrector_replay_is_bound_to_current_generation_content_and_reference_subset() -> None:
    reference = _reference()
    request = _correction_request(reference=reference)
    adapter = LLMCorrectorAdapter(model="codex", model_version="5.6-sol")
    packet = adapter.export_work_packet(request)
    run = adapter.import_work_results_with_receipts(
        request,
        (
            canonical_json_bytes(
                _correction_payload(
                    packet,
                    reference_evidence_ids=(reference.id,),
                    evidence_basis="recognition_and_reference",
                )
            ),
        ),
    )

    generation_id = "generation-replay-drift"
    generation_transcript = request.transcript.model_copy(update={"generation_id": generation_id})
    generation_request = replace(
        request,
        generation_id=generation_id,
        transcript=generation_transcript,
    )
    changed_token = request.transcript.tokens[0].model_copy(update={"text": "不同內容"})
    content_transcript = request.transcript.model_copy(update={"tokens": (changed_token,)})
    content_request = replace(request, transcript=content_transcript)
    reference_request = replace(request, reference_evidence=())

    for current_request in (generation_request, content_request, reference_request):
        with pytest.raises(AdapterIntegrityError):
            adapter.replay(
                current_request,
                proposals=run.proposals,
                execution_receipts=run.execution_receipts,
                request_bytes=run.request_bytes,
                response_bytes=run.response_bytes,
            )


def test_corrector_replay_strictly_rejects_hostile_raw_json() -> None:
    request = _correction_request()
    adapter = LLMCorrectorAdapter(model="codex", model_version="5.6-sol")
    packet = adapter.export_work_packet(request)
    packet_id = packet["work_packet_id"]
    valid_run = adapter.import_work_results_with_receipts(
        request,
        (
            canonical_json_bytes(
                {
                    "schema_version": 1,
                    "work_packet_id": packet_id,
                    "proposals": [],
                }
            ),
        ),
    )
    hostile_payloads = (
        (
            '{"schema_version":1,"work_packet_id":"%s",'
            '"work_packet_id":"%s","proposals":[]}' % (packet_id, packet_id)
        ).encode("utf-8"),
        (
            '{"schema_version":1,"work_packet_id":"%s",'
            '"proposals":[],"unexpected":true}' % packet_id
        ).encode("utf-8"),
        (
            '{"schema_version":1,"work_packet_id":"%s","proposals":[],"unexpected":NaN}' % packet_id
        ).encode("utf-8"),
    )

    for payload in hostile_payloads:
        digest = sha256_bytes(payload)
        forged_receipt = replace(
            valid_run.execution_receipts[0],
            response=ArtifactDigest(
                uri=f"generation-artifact://correction/responses/{digest}",
                sha256=digest,
                size_bytes=len(payload),
            ),
        )
        with pytest.raises(AdapterIntegrityError):
            adapter.replay(
                request,
                proposals=(),
                execution_receipts=(forged_receipt,),
                request_bytes=valid_run.request_bytes,
                response_bytes=(payload,),
            )


def test_corrector_run_constructor_rejects_typed_receipt_rewrite() -> None:
    request = _correction_request()
    adapter = LLMCorrectorAdapter(model="codex", model_version="5.6-sol")
    packet = adapter.export_work_packet(request)
    run = adapter.import_work_results_with_receipts(
        request,
        (_correction_response_bytes(packet),),
    )
    forged_receipt = replace(run.execution_receipts[0], proposal_ids=("unknown-proposal",))

    with pytest.raises(ValueError, match="account for every proposal"):
        CorrectionRunResult(
            proposals=run.proposals,
            execution_receipts=(forged_receipt,),
            request_bytes=run.request_bytes,
            response_bytes=run.response_bytes,
        )
