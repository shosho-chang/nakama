from __future__ import annotations

import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from agents.brook.podcast_subtitles import store as store_module
from agents.brook.podcast_subtitles.adapters.fixtures import (
    FixtureArbiterAdapter,
    FixtureAudioAuditorAdapter,
    FixtureCorrectorAdapter,
    FixtureNormalizerAdapter,
)
from agents.brook.podcast_subtitles.adapters.speaker import MicEnergySpeakerAttributor
from agents.brook.podcast_subtitles.adapters.speech_coverage import (
    FixtureSpeechCoverageAnalyzer,
)
from agents.brook.podcast_subtitles.canonical import review_target_fingerprint
from agents.brook.podcast_subtitles.errors import (
    ArtifactHashMismatchError,
    GenerationIsolationError,
    GenerationNotFoundError,
    ResolutionTransactionError,
)
from agents.brook.podcast_subtitles.hashing import (
    canonical_json_bytes,
    hash_file,
    hash_object,
    sha256_bytes,
)
from agents.brook.podcast_subtitles.module import (
    AcceptedGeneration,
    AdapterIdentity,
    CreateRequest,
    Interrupted,
    ModuleInvariantError,
    NeedsReview,
    PodcastSubtitleV2,
    ProjectRequest,
    ResolveRequest,
    _validate_audio_full_audit_attestation,
    _validate_semantic_partition,
)
from agents.brook.podcast_subtitles.ports import (
    AdapterIntegrityError,
    AdapterWorkPending,
    ArbitrationVerdict,
    CorrectionExecutionReceipt,
    CorrectionModelIdentity,
    CorrectionProposal,
    CorrectionRunResult,
    RecognitionModelIdentity,
    RecognitionRequest,
    SemanticExecutionReceipt,
    SemanticModelIdentity,
    SemanticRunResult,
    SpeakerTrackInput,
)
from agents.brook.podcast_subtitles.profiles import HORIZONTAL_16X9, SubtitlePolicy
from agents.brook.podcast_subtitles.store import GenerationStore
from shared.schemas.podcast_subtitles_v2 import (
    ArbitrationReceipt,
    ArtifactDigest,
    AudioAuditReceipt,
    AudioClockMap,
    BoundaryConstraintReceipt,
    CanonicalTranscript,
    CorrectionAuditReceipt,
    CorrectionDecision,
    EvidenceToken,
    NormalizationParameter,
    NormalizationReceipt,
    RecognitionEvidence,
    ReplacementLexemeAlignment,
    SemanticUnit,
    audio_audit_receipt_set_hash,
    canonical_content_hash,
    normalization_settings_hash,
    reference_evidence_set_hash,
)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _accepted_receipt(source: Path, normalized: Path) -> NormalizationReceipt:
    parameters = (NormalizationParameter(scope="adapter", name="fixture", value=True),)
    return NormalizationReceipt(
        status="accepted",
        provider="fixture",
        production_id="fixture-production",
        production_source="created",
        provider_outcome="completed",
        source_identity_verified=True,
        source_binding_method="upload_in_current_request",
        source=ArtifactDigest(
            uri=source.resolve().as_uri(),
            sha256=hash_file(source),
            size_bytes=source.stat().st_size,
        ),
        normalized=ArtifactDigest(
            uri=normalized.resolve().as_uri(),
            sha256=hash_file(normalized),
            size_bytes=normalized.stat().st_size,
        ),
        source_duration_ms=4_000,
        normalized_duration_ms=4_000,
        request_started_at=datetime(2026, 8, 12, tzinfo=timezone.utc),
        completed_at=datetime(2026, 8, 12, 0, 1, tzinfo=timezone.utc),
        requested_parameters=parameters,
        submitted_parameters=parameters,
        settings_hash=normalization_settings_hash(parameters, preset=None),
        clock_map=AudioClockMap(
            source_origin_ms=0,
            normalized_origin_ms=0,
            verified=True,
            drift_ms=0.0,
        ),
        alignment_method="identity",
    )


def _evidence(
    *,
    episode_id: str,
    invocation_id: str,
    normalized: Path,
    raw: Path,
    adapter: str,
    texts: tuple[str, ...],
    speaker: str | None = "guest",
    confidence: float | None = 0.96,
) -> RecognitionEvidence:
    ranges = tuple((200 + index * 350, 500 + index * 350) for index in range(len(texts)))
    return RecognitionEvidence(
        episode_id=episode_id,
        invocation_id=invocation_id,
        adapter=adapter,
        model="fixture-v1",
        language="zh",
        config_hash=hash_object({"adapter": adapter}),
        raw_output=ArtifactDigest(
            uri=raw.resolve().as_uri(),
            sha256=hash_file(raw),
            size_bytes=raw.stat().st_size,
        ),
        raw_output_hash=hash_file(raw),
        normalized_audio_hash=hash_file(normalized),
        tokens=tuple(
            EvidenceToken(
                id=f"{adapter}-{index}",
                text=text,
                start_ms=ranges[index][0],
                end_ms=ranges[index][1],
                confidence=confidence,
                speaker=speaker,
            )
            for index, text in enumerate(texts)
        ),
    )


def test_verified_receipt_audio_paths_reuse_identical_cas_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module, _audio = _module(tmp_path / "episode")
    source = tmp_path / "same.wav"
    source.write_bytes(b"same-audio")
    receipt = _accepted_receipt(source, source)
    calls: list[tuple[str, int]] = []

    def audio_path(sha256: str, *, size_bytes: int) -> Path:
        calls.append((sha256, size_bytes))
        return source

    monkeypatch.setattr(module.store, "audio_path", audio_path)

    source_path, normalized_path = module._verified_receipt_audio_paths(receipt)

    assert source_path == normalized_path == source
    assert calls == [(receipt.source.sha256, receipt.source.size_bytes)]


class _BoundFixtureRecognizer:
    """Fixture recognizer whose invocation is deliberately bound at call time."""

    def __init__(self, normalized: Path, raw: Path, adapter: str, texts: tuple[str, ...]):
        self.normalized = normalized
        self.raw = raw
        self.adapter = adapter
        self.texts = texts
        self.calls = 0

    @property
    def identity(self) -> RecognitionModelIdentity:
        components = (("fixture_runtime", "test-module"),)
        return RecognitionModelIdentity(
            adapter_name=self.adapter,
            adapter_version="1",
            model="fixture-v1",
            model_version="fixture-pinned",
            aligner="fixture-alignment",
            aligner_version="1",
            runtime_components=components,
            runtime_hash=hash_object({"runtime_components": components}),
            adapter_code_hash=hash_file(Path(__file__)),
            config_hash=hash_object({"adapter": self.adapter}),
            execution_mode="fixture",
        )

    def _result(self, request):
        return _evidence(
            episode_id=request.episode_id,
            invocation_id=request.invocation_id,
            normalized=Path(request.normalized_audio),
            raw=self.raw,
            adapter=self.adapter,
            texts=self.texts,
        )

    def recognize(self, request):
        self.calls += 1
        return self._result(request)

    def verify(self, evidence, *, request, raw_output):
        expected = self._result(request)
        expected = expected.model_copy(
            update={
                "raw_output": expected.raw_output.model_copy(
                    update={"uri": evidence.raw_output.uri}
                )
            }
        )
        if evidence != expected:
            raise AdapterIntegrityError("fixture Recognition Evidence does not replay")
        if sha256_bytes(raw_output) != evidence.raw_output.sha256:
            raise AdapterIntegrityError("fixture Recognition raw output digest mismatch")
        return evidence


class _MutatingNormalizer:
    def __init__(self, delegate) -> None:
        self.delegate = delegate

    def normalize(self, request):
        result = self.delegate.normalize(request)
        request.source_audio.write_bytes(b"source-mutated-during-normalization")
        return result


class _MutatingRecognizer:
    def __init__(self, delegate, normalized: Path) -> None:
        self.delegate = delegate
        self.normalized = normalized

    @property
    def identity(self):
        return self.delegate.identity

    def recognize(self, request):
        evidence = self.delegate.recognize(request)
        self.normalized.write_bytes(b"normalized-mutated-during-recognition")
        return evidence

    def verify(self, evidence, *, request, raw_output):
        return self.delegate.verify(evidence, request=request, raw_output=raw_output)


class _CountingNormalizer:
    def __init__(self, delegate, *, mutate: Path | None = None) -> None:
        self.delegate = delegate
        self.mutate = mutate
        self.calls = 0

    def normalize(self, request):
        self.calls += 1
        result = self.delegate.normalize(request)
        if self.mutate is not None:
            self.mutate.write_bytes(b"track-mutated-during-normalization")
        return result


class _CountingRecognizer:
    def __init__(self, delegate) -> None:
        self.delegate = delegate
        self.calls = 0

    @property
    def identity(self):
        return self.delegate.identity

    def recognize(self, request):
        self.calls += 1
        return self.delegate.recognize(request)

    def verify(self, evidence, *, request, raw_output):
        return self.delegate.verify(evidence, request=request, raw_output=raw_output)


class _CountingCorrector:
    def __init__(self, delegate) -> None:
        self.delegate = delegate
        self.calls = 0
        self.replay_calls = 0

    @property
    def identity(self):
        return self.delegate.identity

    def propose(self, request):
        return self.propose_with_receipt(request).proposals

    def propose_with_receipt(self, request):
        self.calls += 1
        return self.delegate.propose_with_receipt(request)

    def replay(self, request, **kwargs):
        self.replay_calls += 1
        return self.delegate.replay(request, **kwargs)


class _RejectingReplayCorrector(_CountingCorrector):
    def replay(self, request, **kwargs):
        self.replay_calls += 1
        raise AdapterIntegrityError("hostile Corrector replay rejection")


class _TamperingSpeakerAttributor:
    def __init__(self, delegate: MicEnergySpeakerAttributor, mode: str) -> None:
        self.delegate = delegate
        self.mode = mode

    @property
    def adapter_name(self) -> str:
        return self.delegate.adapter_name

    @property
    def adapter_version(self) -> str:
        return self.delegate.adapter_version

    @property
    def adapter_config_hash(self) -> str:
        return self.delegate.adapter_config_hash

    def attribute(self, request):
        attributed = self.delegate.attribute(request)
        if self.mode == "cross_invocation":
            return attributed.model_copy(update={"invocation_id": "another-invocation"})
        if self.mode == "cross_lineage":
            return attributed.model_copy(update={"normalized_audio_hash": "0" * 64})
        if self.mode == "wrong_provenance":
            return attributed
        token = attributed.tokens[0]
        if self.mode == "unknown_speaker":
            token = token.model_copy(update={"speaker": None})
        elif self.mode == "changed_text":
            token = token.model_copy(update={"text": token.text + "偽造"})
        else:  # pragma: no cover - closed fixture modes
            raise AssertionError(self.mode)
        return attributed.model_copy(update={"tokens": (token, *attributed.tokens[1:])})

    def verify(self, attributed, *, base_evidence, raw_output):
        provenance = self.delegate.verify(
            attributed,
            base_evidence=base_evidence,
            raw_output=raw_output,
        )
        if self.mode != "wrong_provenance":
            return provenance
        first, second = provenance.tracks
        return replace(
            provenance,
            tracks=(
                replace(first, speaker_label=second.speaker_label),
                replace(second, speaker_label=first.speaker_label),
            ),
        )


_INLINE_SEMANTIC_IDENTITY = SemanticModelIdentity(
    adapter_name="fixture-semantic",
    adapter_version="1",
    model="fixture",
    model_version="1",
    runtime_hash=hash_object({"runtime": "fixture"}),
    adapter_code_hash=hash_object({"code": "fixture-inline-semantic"}),
    config_hash=hash_object({"fixture": "fixture-semantic"}),
    execution_mode="fixture",
)


class _DynamicSemanticAnalyzer:
    @property
    def identity(self):
        return _INLINE_SEMANTIC_IDENTITY

    def _units(self, request):
        return tuple(
            SemanticUnit(
                id=f"unit-{index}",
                token_ids=(token.id,),
                kind="token",
                strength=1.0,
                forbid_cue_breaks=False,
                forbid_line_breaks=False,
            )
            for index, token in enumerate(request.transcript.tokens)
        )

    def partition_with_receipts(self, request):
        return self._build_run(request)

    def _build_run(self, request):
        units = self._units(request)
        token_ids = tuple(token.id for token in request.transcript.tokens)
        packet = {
            "episode_id": request.transcript.episode_id,
            "generation_id": request.transcript.generation_id,
            "canonical_content_hash": request.transcript.content_hash,
            "policy_hash": request.policy_hash,
            "protected_range_set_hash": request.protected_range_set_hash,
            "protected_ranges": request.protected_ranges,
            "language": request.language,
            "target_token_ids": token_ids,
            "owned_target_token_ids": token_ids,
            "adapter_identity_hash": self.identity.content_hash,
            "units": units,
        }
        packet["work_packet_id"] = f"semantic-work-{hash_object(packet)}"
        request_bytes = canonical_json_bytes(packet)
        response_bytes = canonical_json_bytes(
            {"work_packet_id": packet["work_packet_id"], "units": units}
        )

        def artifact(kind, payload):
            digest = sha256_bytes(payload)
            return ArtifactDigest(
                uri=f"projection-artifact://semantic/{kind}s/{digest}",
                sha256=digest,
                size_bytes=len(payload),
            )

        return SemanticRunResult(
            canonical_token_ids=token_ids,
            units=units,
            execution_receipts=(
                SemanticExecutionReceipt(
                    episode_id=request.transcript.episode_id,
                    generation_id=request.transcript.generation_id,
                    canonical_content_hash=request.transcript.content_hash,
                    work_packet_id=str(packet["work_packet_id"]),
                    packet_index=0,
                    packet_count=1,
                    target_token_ids=token_ids,
                    owned_token_ids=token_ids,
                    request=artifact("request", request_bytes),
                    response=artifact("response", response_bytes),
                    adapter_identity=self.identity,
                    semantic_unit_ids=tuple(unit.id for unit in units),
                ),
            ),
            request_bytes=(request_bytes,),
            response_bytes=(response_bytes,),
        )

    def replay(self, request, *, units, execution_receipts, request_bytes, response_bytes):
        stored = SemanticRunResult(
            canonical_token_ids=tuple(token.id for token in request.transcript.tokens),
            units=units,
            execution_receipts=execution_receipts,
            request_bytes=request_bytes,
            response_bytes=response_bytes,
        )
        expected = self._build_run(request)
        if stored != expected:
            raise AdapterIntegrityError("fixture Semantic execution proof does not replay")
        return expected

    def partition(self, request):
        return self.partition_with_receipts(request).units


_INLINE_CORRECTOR_IDENTITY = CorrectionModelIdentity(
    adapter_name="fixture-corrector",
    adapter_version="1",
    model="fixture",
    model_version="1",
    runtime_hash=hash_object({"runtime": "fixture"}),
    adapter_code_hash=hash_object({"code": "fixture-inline-corrector"}),
    config_hash=hash_object({"fixture": "fixture-corrector"}),
    execution_mode="fixture",
)


class _RecordingCorrector:
    def __init__(self) -> None:
        self.requests = []

    @property
    def identity(self) -> CorrectionModelIdentity:
        return _INLINE_CORRECTOR_IDENTITY

    def propose(self, request):
        self.requests.append(request)
        return ()

    def propose_with_receipt(self, request):
        return _correction_run(request, self.propose(request))

    def replay(self, request, **kwargs):
        return _replay_fixture_correction(request, **kwargs)


class _PendingCorrector(_RecordingCorrector):
    def __init__(self, workspace: Path) -> None:
        super().__init__()
        self.workspace = workspace
        self.ready = False

    def propose(self, request):
        self.requests.append(request)
        if not self.ready:
            raise AdapterWorkPending(
                "fixture correction work pending",
                packet_paths=(self.workspace / "packet.json",),
                response_paths=(self.workspace / "response.json",),
            )
        return ()


class _PendingAudioAuditor:
    def __init__(self, delegate: FixtureAudioAuditorAdapter, workspace: Path) -> None:
        self.delegate = delegate
        self.workspace = workspace
        self.ready = False

    @property
    def identity(self):
        return self.delegate.identity

    def audit(self, request):
        if not self.ready:
            raise AdapterWorkPending(
                "fixture audio audit pending",
                packet_paths=(self.workspace / "audio.packet.json",),
                response_paths=(self.workspace / "audio.response.json",),
            )
        return self.delegate.audit(request)

    def replay(self, request, **kwargs):
        return self.delegate.replay(request, **kwargs)


class _PendingArbiter:
    def __init__(self, delegate: FixtureArbiterAdapter, workspace: Path) -> None:
        self.delegate = delegate
        self.workspace = workspace
        self.ready = False

    @property
    def identity(self):
        return self.delegate.identity

    def decide(self, request):
        return self.decide_with_receipt(request).verdict

    def decide_with_receipt(self, request):
        if not self.ready:
            raise AdapterWorkPending(
                "fixture arbitration pending",
                packet_paths=(self.workspace / "arbiter.packet.json",),
                response_paths=(self.workspace / "arbiter.response.json",),
            )
        return self.delegate.decide_with_receipt(request)

    def replay(self, request, **kwargs):
        return self.delegate.replay(request, **kwargs)


class _ReplayCountingAudioAuditor:
    def __init__(self, delegate: FixtureAudioAuditorAdapter) -> None:
        self.delegate = delegate
        self.audit_calls = 0
        self.replay_calls = 0

    @property
    def identity(self):
        return self.delegate.identity

    def audit(self, request):
        self.audit_calls += 1
        return self.delegate.audit(request)

    def replay(self, request, **kwargs):
        self.replay_calls += 1
        return self.delegate.replay(request, **kwargs)


class _ReplayCountingArbiter:
    def __init__(self, delegate: FixtureArbiterAdapter) -> None:
        self.delegate = delegate
        self.decide_calls = 0
        self.replay_calls = 0

    @property
    def identity(self):
        return self.delegate.identity

    def decide(self, request):
        return self.decide_with_receipt(request).verdict

    def decide_with_receipt(self, request):
        self.decide_calls += 1
        return self.delegate.decide_with_receipt(request)

    def replay(self, request, **kwargs):
        self.replay_calls += 1
        return self.delegate.replay(request, **kwargs)


class _PendingSemanticAnalyzer(_DynamicSemanticAnalyzer):
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.ready = False

    def partition_with_receipts(self, request):
        if not self.ready:
            raise AdapterWorkPending(
                "fixture semantic work pending",
                packet_paths=(self.workspace / "semantic.packet.json",),
                response_paths=(self.workspace / "semantic.response.json",),
            )
        return super().partition_with_receipts(request)


class _ConflictingCorrector:
    @property
    def identity(self) -> CorrectionModelIdentity:
        return _INLINE_CORRECTOR_IDENTITY

    def propose(self, request):
        span = request.transcript.spans[0]
        token_by_id = {token.id: token for token in request.transcript.tokens}
        selected = tuple(token_by_id[token_id] for token_id in span.token_ids)
        raw_ids = tuple(
            sorted(
                {
                    evidence_id.split(":", maxsplit=2)[-1]
                    for token in selected
                    for evidence_id in token.evidence_ids
                }
            )
        )
        common = {
            "audio_span_ids": (span.id,),
            "start_ms": selected[0].start_ms,
            "end_ms": selected[-1].end_ms,
            "evidence_token_ids": raw_ids,
            "observed_text": "".join(token.text for token in selected),
            "confidence": 0.8,
            "rationale": "fixture conflict",
            "source": "fixture-corrector",
        }
        return (
            CorrectionProposal(id="proposal-a", candidate_text="候選甲", **common),
            CorrectionProposal(id="proposal-b", candidate_text="候選乙", **common),
        )

    def propose_with_receipt(self, request):
        return _correction_run(request, self.propose(request))

    def replay(self, request, **kwargs):
        return _replay_fixture_correction(request, **kwargs)


class _LastSpanProposalCorrector:
    def __init__(self) -> None:
        self.proposal: CorrectionProposal | None = None

    @property
    def identity(self) -> CorrectionModelIdentity:
        return _INLINE_CORRECTOR_IDENTITY

    def propose(self, request):
        if request.mode != "full_audit":
            return ()
        span = request.transcript.spans[-1]
        token_by_id = {token.id: token for token in request.transcript.tokens}
        selected = tuple(token_by_id[token_id] for token_id in span.token_ids)
        raw_ids = tuple(
            sorted(
                {
                    evidence_id.split(":", maxsplit=2)[-1]
                    for token in selected
                    for evidence_id in token.evidence_ids
                }
            )
        )
        self.proposal = CorrectionProposal(
            id="proposal-last-span",
            audio_span_ids=(span.id,),
            start_ms=selected[0].start_ms,
            end_ms=selected[-1].end_ms,
            evidence_token_ids=raw_ids,
            observed_text="".join(token.text for token in selected),
            candidate_text="正確候選",
            confidence=0.91,
            rationale="fixture proposal",
            source="fixture-corrector",
        )
        return (self.proposal,)

    def propose_with_receipt(self, request):
        return _correction_run(request, self.propose(request))

    def replay(self, request, **kwargs):
        return _replay_fixture_correction(request, **kwargs)


class _SequentialDisjointCorrector:
    """Reissues only still-observed mistakes with child-bound Proposal IDs."""

    def __init__(self) -> None:
        self.requests = []

    @property
    def identity(self) -> CorrectionModelIdentity:
        return _INLINE_CORRECTOR_IDENTITY

    def propose(self, request):
        self.requests.append(request)
        token_by_id = {token.id: token for token in request.transcript.tokens}
        replacements = {"錯甲": "正甲", "錯乙": "正乙"}
        proposals = []
        for span in request.transcript.spans:
            if span.id not in request.target_span_ids:
                continue
            selected = tuple(token_by_id[item] for item in span.token_ids)
            observed = "".join(token.text for token in selected)
            candidate = replacements.get(observed)
            if candidate is None:
                continue
            raw_ids = tuple(
                sorted(
                    {
                        evidence_id.split(":", maxsplit=2)[-1]
                        for token in selected
                        for evidence_id in token.evidence_ids
                    }
                )
            )
            identity = {
                "generation_id": request.generation_id,
                "span_ids": (span.id,),
                "observed": observed,
                "candidate": candidate,
            }
            proposals.append(
                CorrectionProposal(
                    id=f"proposal-{hash_object(identity)}",
                    audio_span_ids=(span.id,),
                    start_ms=span.start_ms,
                    end_ms=span.end_ms,
                    evidence_token_ids=raw_ids,
                    observed_text=observed,
                    candidate_text=candidate,
                    confidence=0.97,
                    rationale="sequential disjoint fixture",
                    source="fixture-corrector",
                )
            )
        return tuple(proposals)

    def propose_with_receipt(self, request):
        return _correction_run(request, self.propose(request))

    def replay(self, request, **kwargs):
        return _replay_fixture_correction(request, **kwargs)


class _AcceptEveryProposalArbiter:
    """Produce and replay an exact accepted audio verdict for dynamic IDs."""

    def __init__(self) -> None:
        self.delegate = FixtureArbiterAdapter({})

    @property
    def identity(self):
        return self.delegate.identity

    def _bind(self, request) -> None:
        self.delegate._verdicts[request.proposal.id] = ArbitrationVerdict(
            proposal_id=request.proposal.id,
            status="accepted",
            selected_text=request.proposal.candidate_text,
            confidence=0.99,
            rationale="fixture relisten accepted exact candidate",
        )

    def decide(self, request):
        self._bind(request)
        return self.delegate.decide(request)

    def decide_with_receipt(self, request):
        self._bind(request)
        return self.delegate.decide_with_receipt(request)

    def replay(self, request, **kwargs):
        self._bind(request)
        return self.delegate.replay(request, **kwargs)


def _correction_run(request, proposals) -> CorrectionRunResult:
    typed = tuple(proposals)
    identity = _INLINE_CORRECTOR_IDENTITY
    packet: dict[str, object] = {
        "schema_version": 1,
        "task": "podcast_subtitle_v2_fixture_correction",
        "episode_id": request.episode_id,
        "generation_id": request.generation_id,
        "canonical_content_hash": request.transcript.content_hash,
        "recognition_evidence_hash": request.transcript.evidence_hash,
        "reference_evidence_hash": request.transcript.reference_evidence_hash,
        "available_reference_evidence_ids": [
            item.id for item in request.available_reference_evidence
        ],
        "available_reference_evidence_hash": reference_evidence_set_hash(
            request.available_reference_evidence
        ),
        "presented_reference_evidence_ids": [item.id for item in request.reference_evidence],
        "presented_reference_evidence_hash": reference_evidence_set_hash(
            request.reference_evidence
        ),
        "reference_evidence": [{"id": item.id} for item in request.reference_evidence],
        "policy_hash": request.transcript.policy_hash,
        "target_span_ids": list(request.target_span_ids),
        "adapter_identity_hash": identity.content_hash,
    }
    packet["work_packet_id"] = f"fixture-correction-{hash_object(packet)}"
    response = {
        "schema_version": 1,
        "work_packet_id": packet["work_packet_id"],
        "proposals": [
            {
                "audio_span_ids": list(proposal.audio_span_ids),
                "evidence_token_ids": list(proposal.evidence_token_ids),
                "observed_text": proposal.observed_text,
                "candidate_text": proposal.candidate_text,
                "confidence": proposal.confidence,
                "rationale": proposal.rationale,
                "reference_evidence_ids": list(proposal.reference_evidence_ids),
                "evidence_basis": proposal.evidence_basis,
            }
            for proposal in typed
        ],
    }
    request_bytes = canonical_json_bytes(packet)
    response_bytes = canonical_json_bytes(response)

    def artifact(kind: str, payload: bytes) -> ArtifactDigest:
        digest = sha256_bytes(payload)
        return ArtifactDigest(
            uri=f"generation-artifact://correction/{kind}s/{digest}",
            sha256=digest,
            size_bytes=len(payload),
        )

    return CorrectionRunResult(
        proposals=typed,
        execution_receipts=(
            CorrectionExecutionReceipt(
                work_packet_id=str(packet["work_packet_id"]),
                target_span_ids=request.target_span_ids,
                presented_reference_evidence_ids=tuple(
                    item.id for item in request.reference_evidence
                ),
                presented_reference_evidence_hash=reference_evidence_set_hash(
                    request.reference_evidence
                ),
                request=artifact("request", request_bytes),
                response=artifact("response", response_bytes),
                adapter_identity=identity,
                proposal_ids=tuple(proposal.id for proposal in typed),
            ),
        ),
        request_bytes=(request_bytes,),
        response_bytes=(response_bytes,),
    )


def _replay_fixture_correction(
    request,
    *,
    proposals,
    execution_receipts,
    request_bytes,
    response_bytes,
) -> CorrectionRunResult:
    stored = CorrectionRunResult(
        proposals=tuple(proposals),
        execution_receipts=tuple(execution_receipts),
        request_bytes=tuple(request_bytes),
        response_bytes=tuple(response_bytes),
    )
    expected = _correction_run(request, proposals)
    if stored != expected:
        raise AdapterIntegrityError("fixture Corrector execution proof does not replay")
    return expected


def _adapter_identity(name: str) -> AdapterIdentity:
    return AdapterIdentity(
        name=name,
        version="1",
        config_hash=hash_object({"fixture": name}),
        execution_mode="fixture",
    )


_DEFAULT_SPEECH_COVERAGE = object()


def _module(
    tmp_path: Path,
    *,
    draft_receipt: bool = False,
    corrector=None,
    semantic_analyzer=None,
    corrector_identity: AdapterIdentity | None = None,
    speaker_attributor=None,
    speech_coverage_analyzer=_DEFAULT_SPEECH_COVERAGE,
) -> tuple[PodcastSubtitleV2, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    source = tmp_path / "source.wav"
    normalized = tmp_path / "normalized.wav"
    raw_one = tmp_path / "primary.json"
    raw_two = tmp_path / "secondary.json"
    source.write_bytes(b"source-audio")
    normalized.write_bytes(b"normalized-audio")
    raw_one.write_text('{"adapter":"primary"}', encoding="utf-8")
    raw_two.write_text('{"adapter":"secondary"}', encoding="utf-8")
    receipt = _accepted_receipt(source, normalized)
    if draft_receipt:
        receipt = receipt.model_copy(update={"status": "draft"})
    module = PodcastSubtitleV2(
        tmp_path,
        normalizer=FixtureNormalizerAdapter(normalized, receipt),
        recognizers=(
            _BoundFixtureRecognizer(normalized, raw_one, "primary", ("哥大", "BA典禮")),
            _BoundFixtureRecognizer(normalized, raw_two, "secondary", ("哥大", "畢業典禮")),
        ),
        corrector=corrector or FixtureCorrectorAdapter(),
        audio_auditor=FixtureAudioAuditorAdapter(),
        speech_coverage_analyzer=(
            FixtureSpeechCoverageAnalyzer()
            if speech_coverage_analyzer is _DEFAULT_SPEECH_COVERAGE
            else speech_coverage_analyzer
        ),
        semantic_analyzer=semantic_analyzer or _DynamicSemanticAnalyzer(),
        corrector_identity=corrector_identity or _adapter_identity("fixture-corrector"),
        semantic_analyzer_identity=AdapterIdentity(
            name="fixture-semantic",
            version="1",
            config_hash=(semantic_analyzer or _DynamicSemanticAnalyzer()).identity.config_hash,
            execution_mode="fixture",
        ),
        speaker_attributor=speaker_attributor,
        speaker_attributor_identity=(
            AdapterIdentity(
                name=speaker_attributor.adapter_name,
                version=speaker_attributor.adapter_version,
                config_hash=speaker_attributor.adapter_config_hash,
                execution_mode="fixture",
            )
            if speaker_attributor is not None
            else None
        ),
    )
    return module, source


def _single_stream_module(
    tmp_path: Path,
    *,
    texts: tuple[str, ...],
    corrector,
    semantic_analyzer=None,
    speaker: str | None = "guest",
    audio_auditor=None,
    arbiter=None,
    speech_coverage_analyzer=_DEFAULT_SPEECH_COVERAGE,
) -> tuple[PodcastSubtitleV2, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    source = tmp_path / "source.wav"
    normalized = tmp_path / "normalized.wav"
    raw = tmp_path / "primary.json"
    source.write_bytes(b"source-audio")
    normalized.write_bytes(b"normalized-audio")
    raw.write_text('{"adapter":"primary"}', encoding="utf-8")

    class _SingleRecognizer(_BoundFixtureRecognizer):
        def _result(self, request):
            return _evidence(
                episode_id=request.episode_id,
                invocation_id=request.invocation_id,
                normalized=normalized,
                raw=raw,
                adapter="primary",
                texts=texts,
                speaker=speaker,
            )

    return (
        PodcastSubtitleV2(
            tmp_path,
            normalizer=FixtureNormalizerAdapter(
                normalized,
                _accepted_receipt(source, normalized),
            ),
            recognizers=(_SingleRecognizer(normalized, raw, "primary", texts),),
            corrector=corrector,
            audio_auditor=audio_auditor or FixtureAudioAuditorAdapter(),
            arbiter=arbiter,
            speech_coverage_analyzer=(
                FixtureSpeechCoverageAnalyzer()
                if speech_coverage_analyzer is _DEFAULT_SPEECH_COVERAGE
                else speech_coverage_analyzer
            ),
            semantic_analyzer=semantic_analyzer or _DynamicSemanticAnalyzer(),
            corrector_identity=_adapter_identity("fixture-corrector"),
            semantic_analyzer_identity=AdapterIdentity(
                name="fixture-semantic",
                version="1",
                config_hash=(semantic_analyzer or _DynamicSemanticAnalyzer()).identity.config_hash,
                execution_mode="fixture",
            ),
        ),
        source,
    )


def _decision_for_created(created: NeedsReview, *, event_id: str) -> CorrectionDecision:
    span_ids = tuple(span.id for span in created.transcript.spans)
    selected = created.transcript.tokens
    return CorrectionDecision(
        event_id=event_id,
        episode_id=created.transcript.episode_id,
        generation_id=created.generation_id,
        target_span_ids=span_ids,
        target_start_ms=selected[0].start_ms,
        target_end_ms=selected[-1].end_ms,
        evidence_fingerprint=review_target_fingerprint(created.transcript, span_ids),
        issue_ids=tuple(
            issue.id for issue in created.transcript.review_issues if issue.status == "unresolved"
        ),
        audio_evidence_ids=tuple(
            sorted({item for token in selected for item in token.evidence_ids})
        ),
        evidence_basis="audio",
        action="replace",
        replacement_text="哥大畢業典禮",
        replacement_lexemes=("哥大", "畢業典禮"),
        actor_kind="human",
        actor="reviewer",
        rationale="audio relisten",
        timestamp=datetime(2026, 8, 12, tzinfo=timezone.utc),
    )


def _replace_decision_for_proposal(
    transcript: CanonicalTranscript,
    proposal: CorrectionProposal,
    *,
    event_id: str,
) -> CorrectionDecision:
    span_by_id = {span.id: span for span in transcript.spans}
    token_by_id = {token.id: token for token in transcript.tokens}
    selected = tuple(
        token_by_id[token_id]
        for span_id in proposal.audio_span_ids
        for token_id in span_by_id[span_id].token_ids
    )
    target_set = set(proposal.audio_span_ids)
    issue_ids = tuple(
        issue.id
        for issue in transcript.review_issues
        if issue.status == "unresolved"
        and issue.severity in {"medium", "high", "blocking"}
        and set(issue.span_ids).issubset(target_set)
    )
    return CorrectionDecision(
        event_id=event_id,
        episode_id=transcript.episode_id,
        generation_id=transcript.generation_id,
        target_span_ids=proposal.audio_span_ids,
        target_start_ms=proposal.start_ms,
        target_end_ms=proposal.end_ms,
        evidence_fingerprint=review_target_fingerprint(transcript, proposal.audio_span_ids),
        proposal_ids=(proposal.id,),
        issue_ids=issue_ids,
        audio_evidence_ids=tuple(
            sorted({item for token in selected for item in token.evidence_ids})
        ),
        evidence_basis="audio",
        action="replace",
        replacement_text=proposal.candidate_text,
        replacement_lexemes=(proposal.candidate_text,),
        actor_kind="human",
        actor="reviewer",
        rationale="accepted after exact normalized-audio relisten",
        timestamp=datetime(2026, 8, 12, tzinfo=timezone.utc),
    )


def _artifact_for_rewritten_bytes(uri_prefix: str, payload: bytes) -> ArtifactDigest:
    digest = sha256_bytes(payload)
    return ArtifactDigest(
        uri=f"generation-artifact://{uri_prefix}/{digest}",
        sha256=digest,
        size_bytes=len(payload),
    )


def _audio_execution_stage_hash(
    directory: Path,
    audio_audits: tuple[AudioAuditReceipt, ...],
    arbitrations: tuple[ArbitrationReceipt, ...],
) -> str:
    artifacts: dict[str, bytes] = {}
    for receipt in (*audio_audits, *arbitrations):
        for artifact in (receipt.request, receipt.response, receipt.clip):
            name = artifact.uri.removeprefix("generation-artifact://")
            artifacts[name] = (directory / Path(name)).read_bytes()
    return hash_object(
        tuple((name, sha256_bytes(payload)) for name, payload in sorted(artifacts.items()))
    )


def _rewrite_generation_consistently(
    module: PodcastSubtitleV2,
    generation_id: str,
    *,
    artifacts: dict[str, bytes],
    stage_hashes: dict[str, str],
) -> None:
    """Coordinate artifact, manifest-stage, and store-record hashes like an attacker."""

    directory = module.store.generation_dir(generation_id)
    record_path = directory / "generation.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    for name, payload in artifacts.items():
        path = directory / Path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        record["artifact_hashes"][name] = sha256_bytes(payload)
    record["manifest"]["stage_artifact_hashes"].update(stage_hashes)
    record["artifact_set_hash"] = hash_object(
        {
            "store_version": record["store_version"],
            "manifest": record["manifest"],
            "artifact_hashes": record["artifact_hashes"],
        }
    )
    record_path.write_bytes(canonical_json_bytes(record))
    active_path = module.store.root / "active-generation.json"
    active = json.loads(active_path.read_text(encoding="utf-8"))
    if active.get("generation_id") == generation_id:
        active_payload = {
            "generation_id": generation_id,
            "artifact_set_hash": record["artifact_set_hash"],
            "generation_record_hash": hash_file(record_path),
        }
        active_path.write_bytes(
            canonical_json_bytes({**active_payload, "pointer_hash": hash_object(active_payload)})
        )


def _coordinated_correction_byte_tamper(
    module: PodcastSubtitleV2,
    generation_id: str,
    *,
    artifact_field: str,
) -> None:
    """Rewrite bytes, typed receipt, stage hashes, record, and active pointer."""

    directory = module.store.generation_dir(generation_id)
    audit_payloads = json.loads(
        (directory / "correction_audit_receipts.json").read_text(encoding="utf-8")
    )
    audit = audit_payloads[0]
    artifact = audit[artifact_field]
    name = artifact["uri"].removeprefix("generation-artifact://")
    payload = json.loads((directory / Path(name)).read_text(encoding="utf-8"))
    payload["hostile_extra_field"] = f"forged-{artifact_field}"
    changed_bytes = canonical_json_bytes(payload)
    artifact["sha256"] = sha256_bytes(changed_bytes)
    artifact["size_bytes"] = len(changed_bytes)
    audit[f"{artifact_field}_hash"] = artifact["sha256"]
    typed_audits = tuple(CorrectionAuditReceipt.model_validate(item) for item in audit_payloads)
    audit_bytes = canonical_json_bytes(typed_audits)
    execution_artifacts: dict[str, bytes] = {}
    for typed in typed_audits:
        for digest in (typed.request, typed.response):
            artifact_name = digest.uri.removeprefix("generation-artifact://")
            execution_artifacts[artifact_name] = (
                changed_bytes
                if artifact_name == name
                else (directory / Path(artifact_name)).read_bytes()
            )
    _rewrite_generation_consistently(
        module,
        generation_id,
        artifacts={
            name: changed_bytes,
            "correction_audit_receipts.json": audit_bytes,
        },
        stage_hashes={
            "correction_audit_receipts": hash_object(typed_audits),
            "correction_execution_artifacts": hash_object(
                tuple(
                    (artifact_name, sha256_bytes(artifact_bytes))
                    for artifact_name, artifact_bytes in sorted(execution_artifacts.items())
                )
            ),
        },
    )


def _coordinated_audio_audit_tamper(
    module: PodcastSubtitleV2,
    generation_id: str,
    *,
    mode: str,
) -> None:
    directory = module.store.generation_dir(generation_id)
    audit_path = directory / "audio_audit_receipts.json"
    audits = tuple(
        AudioAuditReceipt.model_validate(item)
        for item in json.loads(audit_path.read_text(encoding="utf-8"))
    )
    receipt = audits[0]
    changed_artifacts: dict[str, bytes] = {}
    if mode == "clip":
        payload = b"coordinated-but-wrong-audio-clip"
        artifact = _artifact_for_rewritten_bytes("audio_audit/clips", payload)
        updated = receipt.model_copy(update={"clip": artifact})
    elif mode == "request":
        request_name = receipt.request.uri.removeprefix("generation-artifact://")
        request_payload = json.loads((directory / Path(request_name)).read_bytes())
        request_payload["attacker_supplied_field"] = True
        payload = canonical_json_bytes(request_payload)
        artifact = _artifact_for_rewritten_bytes("audio_audit/requests", payload)
        updated = receipt.model_copy(update={"request": artifact})
    elif mode == "response":
        response_name = receipt.response.uri.removeprefix("generation-artifact://")
        response_payload = json.loads((directory / Path(response_name)).read_bytes())
        response_payload["rationale"] = "coordinated semantic rewrite"
        payload = canonical_json_bytes(response_payload)
        artifact = _artifact_for_rewritten_bytes("audio_audit/responses", payload)
        updated = receipt.model_copy(
            update={
                "response": artifact,
                "rationale": "coordinated semantic rewrite",
            }
        )
    else:  # pragma: no cover - closed test modes
        raise AssertionError(mode)
    artifact_name = artifact.uri.removeprefix("generation-artifact://")
    changed_artifacts[artifact_name] = payload
    changed_audits = (updated, *audits[1:])
    changed_audit_bytes = canonical_json_bytes(changed_audits)
    changed_artifacts["audio_audit_receipts.json"] = changed_audit_bytes

    transcript_path = directory / "canonical_transcript.json"
    transcript = CanonicalTranscript.model_validate_json(transcript_path.read_bytes())
    transcript = transcript.model_copy(
        update={"full_audit_receipt_set_hash": audio_audit_receipt_set_hash(changed_audits)}
    )
    changed_artifacts["canonical_transcript.json"] = canonical_json_bytes(transcript)
    for name, payload_item in changed_artifacts.items():
        path = directory / Path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload_item)
    arbitrations = tuple(
        ArbitrationReceipt.model_validate(item)
        for item in json.loads(
            (directory / "arbitration_receipts.json").read_text(encoding="utf-8")
        )
    )
    _rewrite_generation_consistently(
        module,
        generation_id,
        artifacts=changed_artifacts,
        stage_hashes={
            "audio_audit_receipts": hash_object(changed_audits),
            "audio_execution_artifacts": _audio_execution_stage_hash(
                directory,
                changed_audits,
                arbitrations,
            ),
            "canonical_transcript": hash_object(transcript),
        },
    )


def _coordinated_arbitration_response_tamper(
    module: PodcastSubtitleV2,
    generation_id: str,
) -> None:
    directory = module.store.generation_dir(generation_id)
    arbitration_path = directory / "arbitration_receipts.json"
    arbitrations = tuple(
        ArbitrationReceipt.model_validate(item)
        for item in json.loads(arbitration_path.read_text(encoding="utf-8"))
    )
    receipt = arbitrations[0]
    response_name = receipt.response.uri.removeprefix("generation-artifact://")
    response_payload = json.loads((directory / Path(response_name)).read_bytes())
    response_payload["rationale"] = "coordinated arbitration rewrite"
    response_bytes = canonical_json_bytes(response_payload)
    response_artifact = _artifact_for_rewritten_bytes(
        "arbitration/responses",
        response_bytes,
    )
    changed = receipt.model_copy(
        update={
            "response": response_artifact,
            "rationale": "coordinated arbitration rewrite",
        }
    )
    changed_arbitrations = (changed, *arbitrations[1:])
    changed_artifacts = {
        response_artifact.uri.removeprefix("generation-artifact://"): response_bytes,
        "arbitration_receipts.json": canonical_json_bytes(changed_arbitrations),
    }
    for name, payload in changed_artifacts.items():
        path = directory / Path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    audits = tuple(
        AudioAuditReceipt.model_validate(item)
        for item in json.loads(
            (directory / "audio_audit_receipts.json").read_text(encoding="utf-8")
        )
    )
    _rewrite_generation_consistently(
        module,
        generation_id,
        artifacts=changed_artifacts,
        stage_hashes={
            "arbitration_receipts": hash_object(changed_arbitrations),
            "audio_execution_artifacts": _audio_execution_stage_hash(
                directory,
                audits,
                changed_arbitrations,
            ),
        },
    )


def test_vertical_slice_is_restartable_and_keeps_one_generation_identity(tmp_path: Path) -> None:
    module, source = _module(tmp_path)
    created = module.create(CreateRequest(episode_id="episode-anji", source_audio=source))

    assert isinstance(created, NeedsReview)
    assert created.generation_id == created.transcript.generation_id
    assert created.generation_id == created.manifest.generation_id
    assert module.store.active_generation_id() == created.generation_id
    old_dir = module.store.generation_dir(created.generation_id)
    old_bytes = {path.name: path.read_bytes() for path in old_dir.iterdir() if path.is_file()}

    span_ids = tuple(span.id for span in created.transcript.spans)
    selected = tuple(created.transcript.tokens)
    decision = CorrectionDecision(
        event_id="decision-graduation",
        episode_id="episode-anji",
        generation_id=created.generation_id,
        target_span_ids=span_ids,
        target_start_ms=selected[0].start_ms,
        target_end_ms=selected[-1].end_ms,
        evidence_fingerprint=review_target_fingerprint(created.transcript, span_ids),
        issue_ids=tuple(
            issue.id for issue in created.transcript.review_issues if issue.status == "unresolved"
        ),
        audio_evidence_ids=tuple(
            sorted({item for token in selected for item in token.evidence_ids})
        ),
        evidence_basis="audio",
        action="replace",
        replacement_text="哥大畢業典禮",
        replacement_lexemes=("哥大", "畢業典禮"),
        actor_kind="human",
        actor="reviewer",
        rationale="audio relisten",
        timestamp=datetime(2026, 8, 12, tzinfo=timezone.utc),
    )
    resolved = module.resolve(
        ResolveRequest(generation_id=created.generation_id, decisions=(decision,))
    )

    assert isinstance(resolved, AcceptedGeneration)
    assert (
        resolved.transcript.verified_speech_coverage_receipt_hash
        == created.transcript.verified_speech_coverage_receipt_hash
    )
    assert resolved.generation_id != created.generation_id
    assert resolved.generation_id == resolved.transcript.generation_id
    assert resolved.generation_id == resolved.manifest.generation_id
    assert module.store.active_generation_id() == resolved.generation_id
    assert {
        path.name: path.read_bytes() for path in old_dir.iterdir() if path.is_file()
    } == old_bytes

    verified = module.project(
        ProjectRequest(generation_id=resolved.generation_id, profile=HORIZONTAL_16X9)
    )
    assert verified.generation_id == resolved.generation_id
    assert verified.projection.generation_id == resolved.generation_id
    assert verified.manifest.generation_id == resolved.generation_id
    assert verified.quality_report.passed
    assert all(cue.end_ms <= 4_000 for cue in verified.projection.cues)

    reopened, _ = _module(tmp_path)
    assert reopened.store.active_generation_id() == resolved.generation_id
    loaded = reopened._load_generation(resolved.generation_id, require_active=True).result
    assert loaded.transcript == resolved.transcript
    reopened._verify_projection_directory(
        verified.projection_id,
        expected_generation_id=resolved.generation_id,
    )


def test_text_changing_resolve_requires_fresh_child_audio_audit_and_resumes_cleanly(
    tmp_path: Path,
) -> None:
    module, source = _module(tmp_path)
    delegate = module._audio_auditor
    assert isinstance(delegate, FixtureAudioAuditorAdapter)
    created = module.create(CreateRequest(episode_id="episode-child-audit", source_audio=source))
    assert isinstance(created, NeedsReview)
    parent_loaded = module._load_generation(created.generation_id, require_active=True)
    parent_audits = parent_loaded.audit.audio_audits
    parent_generation_ids = {receipt.preaudit_generation_id for receipt in parent_audits}

    auditor = _PendingAudioAuditor(delegate, tmp_path / "resolve-audio-work")
    module._audio_auditor = auditor
    decision = _decision_for_created(created, event_id="child-audit-required")
    active_before = module.store.active_generation_id()
    ledger_before = module.ledger.entries()

    pending = module.resolve(ResolveRequest(created.generation_id, (decision,)))

    assert isinstance(pending, Interrupted)
    assert pending.operation == "resolve"
    assert pending.generation_id is not None
    assert pending.generation_id != created.generation_id
    assert module.store.active_generation_id() == active_before
    assert module.ledger.entries() == ledger_before
    assert not module.store.generation_dir(pending.generation_id).exists()
    assert not module.store.resolution_journal_exists()
    with pytest.raises(ModuleInvariantError, match="accepted active"):
        module.project(ProjectRequest(created.generation_id, HORIZONTAL_16X9))
    with pytest.raises(GenerationIsolationError, match="not active"):
        module.project(ProjectRequest(pending.generation_id, HORIZONTAL_16X9))

    auditor.ready = True
    resolved = module.resolve(ResolveRequest(created.generation_id, (decision,)))

    assert isinstance(resolved, AcceptedGeneration)
    assert module.store.active_generation_id() == resolved.generation_id
    assert len(module.ledger.entries()) == len(ledger_before) + 1
    child_loaded = module._load_generation(resolved.generation_id, require_active=True)
    child_audits = child_loaded.audit.audio_audits
    assert tuple(
        span_id for receipt in child_audits for span_id in receipt.target_span_ids
    ) == tuple(span.id for span in resolved.transcript.spans)
    assert {receipt.preaudit_content_hash for receipt in child_audits} == {
        resolved.transcript.content_hash
    }
    assert {receipt.preaudit_generation_id for receipt in child_audits} == {pending.generation_id}
    assert {receipt.preaudit_generation_id for receipt in child_audits}.isdisjoint(
        parent_generation_ids
    )
    with pytest.raises(GenerationIsolationError, match="exactly partition"):
        _validate_audio_full_audit_attestation(
            resolved.transcript,
            parent_audits,
            delegate.identity,
        )
    stale_child_audits = tuple(
        receipt.model_copy(update={"preaudit_content_hash": created.transcript.content_hash})
        for receipt in child_audits
    )
    forged_transcript = resolved.transcript.model_copy(
        update={
            "full_audit_receipt_set_hash": audio_audit_receipt_set_hash(list(stale_child_audits))
        }
    )
    with pytest.raises(GenerationIsolationError, match="current immutable truth"):
        _validate_audio_full_audit_attestation(
            forged_transcript,
            stale_child_audits,
            delegate.identity,
        )

    reopened, _ = _module(tmp_path)
    fresh = reopened._load_generation(resolved.generation_id, require_active=True)
    assert fresh.result.transcript == resolved.transcript
    assert fresh.audit.audio_audits == child_audits


def test_resolve_pending_child_text_audit_has_no_durable_side_effects_and_retries(
    tmp_path: Path,
) -> None:
    corrector = _PendingCorrector(tmp_path / "resolve-text-work")
    corrector.ready = True
    module, source = _module(tmp_path, corrector=corrector)
    created = module.create(
        CreateRequest(episode_id="episode-child-text-pending", source_audio=source)
    )
    assert isinstance(created, NeedsReview)
    decision = _decision_for_created(created, event_id="child-text-pending")

    corrector.ready = False
    active_before = module.store.active_generation_id()
    ledger_before = module.ledger.entries()
    generations_before = tuple(sorted(module.store.generations_dir.iterdir()))
    pending = module.resolve(ResolveRequest(created.generation_id, (decision,)))

    assert isinstance(pending, Interrupted)
    assert pending.operation == "resolve"
    assert pending.generation_id is not None
    assert pending.generation_id != created.generation_id
    assert module.store.active_generation_id() == active_before
    assert module.ledger.entries() == ledger_before
    assert tuple(sorted(module.store.generations_dir.iterdir())) == generations_before
    assert not module.store.generation_dir(pending.generation_id).exists()
    assert not module.store.resolution_journal_exists()

    corrector.ready = True
    resolved = module.resolve(ResolveRequest(created.generation_id, (decision,)))

    assert isinstance(resolved, AcceptedGeneration)
    assert resolved.generation_id != pending.generation_id
    assert not module.store.generation_dir(pending.generation_id).exists()
    assert module.store.active_generation_id() == resolved.generation_id
    assert len(module.ledger.entries()) == len(ledger_before) + 1


def test_resolve_pending_child_arbitration_has_no_durable_side_effects_and_retries(
    tmp_path: Path,
) -> None:
    corrector = _SequentialDisjointCorrector()
    delegate = _AcceptEveryProposalArbiter()
    module, source = _single_stream_module(
        tmp_path,
        texts=("錯甲", "錯乙"),
        corrector=corrector,
        arbiter=delegate,
    )
    created = module.create(
        CreateRequest(
            episode_id="episode-child-arbitration-pending",
            source_audio=source,
        )
    )
    assert isinstance(created, NeedsReview)
    parent = module._load_generation(created.generation_id, require_active=True)
    assert len(parent.audit.proposals) == 2
    arbiter = _PendingArbiter(delegate, tmp_path / "resolve-arbitration-work")
    module._arbiter = arbiter
    decision = _replace_decision_for_proposal(
        created.transcript,
        parent.audit.proposals[0],
        event_id="child-arbitration-pending",
    )

    arbiter.ready = False
    active_before = module.store.active_generation_id()
    ledger_before = module.ledger.entries()
    generations_before = tuple(sorted(module.store.generations_dir.iterdir()))
    pending = module.resolve(ResolveRequest(created.generation_id, (decision,)))

    assert isinstance(pending, Interrupted)
    assert pending.operation == "resolve"
    assert pending.generation_id is not None
    assert pending.generation_id != created.generation_id
    assert module.store.active_generation_id() == active_before
    assert module.ledger.entries() == ledger_before
    assert tuple(sorted(module.store.generations_dir.iterdir())) == generations_before
    assert not module.store.generation_dir(pending.generation_id).exists()
    assert not module.store.resolution_journal_exists()

    arbiter.ready = True
    resolved = module.resolve(ResolveRequest(created.generation_id, (decision,)))

    assert isinstance(resolved, NeedsReview)
    assert resolved.generation_id == pending.generation_id
    assert module.store.active_generation_id() == resolved.generation_id
    assert len(module.ledger.entries()) == len(ledger_before) + 1
    child = module._load_generation(resolved.generation_id, require_active=True)
    assert len(child.audit.proposals) == 1
    assert child.audit.proposals[0].candidate_text == "正乙"
    assert child.audit.proposals[0].id != parent.audit.proposals[1].id


def test_disjoint_proposals_are_regenerated_and_arbitrated_per_child_generation(
    tmp_path: Path,
) -> None:
    corrector = _SequentialDisjointCorrector()
    arbiter = _AcceptEveryProposalArbiter()
    module, source = _single_stream_module(
        tmp_path,
        texts=("錯甲", "錯乙"),
        corrector=corrector,
        arbiter=arbiter,
    )
    created = module.create(
        CreateRequest(episode_id="episode-sequential-disjoint", source_audio=source)
    )
    assert isinstance(created, NeedsReview)
    parent = module._load_generation(created.generation_id, require_active=True)
    parent_proposals = parent.audit.proposals
    assert tuple(item.candidate_text for item in parent_proposals) == ("正甲", "正乙")

    def decision_for(
        transcript: CanonicalTranscript,
        proposal: CorrectionProposal,
        *,
        event_id: str,
    ) -> CorrectionDecision:
        span_by_id = {span.id: span for span in transcript.spans}
        token_by_id = {token.id: token for token in transcript.tokens}
        selected = tuple(
            token_by_id[token_id]
            for span_id in proposal.audio_span_ids
            for token_id in span_by_id[span_id].token_ids
        )
        target_set = set(proposal.audio_span_ids)
        issue_ids = tuple(
            issue.id
            for issue in transcript.review_issues
            if issue.status == "unresolved"
            and issue.severity in {"medium", "high", "blocking"}
            and set(issue.span_ids).issubset(target_set)
        )
        return CorrectionDecision(
            event_id=event_id,
            episode_id=transcript.episode_id,
            generation_id=transcript.generation_id,
            target_span_ids=proposal.audio_span_ids,
            target_start_ms=proposal.start_ms,
            target_end_ms=proposal.end_ms,
            evidence_fingerprint=review_target_fingerprint(transcript, proposal.audio_span_ids),
            proposal_ids=(proposal.id,),
            issue_ids=issue_ids,
            audio_evidence_ids=tuple(
                sorted({item for token in selected for item in token.evidence_ids})
            ),
            evidence_basis="audio",
            action="replace",
            replacement_text=proposal.candidate_text,
            replacement_lexemes=(proposal.candidate_text,),
            actor_kind="human",
            actor="reviewer",
            rationale="accepted after exact normalized-audio relisten",
            timestamp=datetime(2026, 8, 12, tzinfo=timezone.utc),
        )

    d1 = decision_for(created.transcript, parent_proposals[0], event_id="accept-d1")
    old_d2 = decision_for(created.transcript, parent_proposals[1], event_id="old-d2")
    first_child = module.resolve(ResolveRequest(created.generation_id, (d1,)))
    assert isinstance(first_child, NeedsReview)
    child = module._load_generation(first_child.generation_id, require_active=True)
    child_proposals = child.audit.proposals
    assert len(child_proposals) == 1
    assert child_proposals[0].candidate_text == "正乙"
    assert child_proposals[0].id != parent_proposals[1].id

    active_before = module.store.active_generation_id()
    ledger_before = module.ledger.entries()
    generations_before = tuple(sorted(module.store.generations_dir.iterdir()))
    with pytest.raises(GenerationIsolationError, match="stale or cross-generation"):
        module.resolve(ResolveRequest(first_child.generation_id, (old_d2,)))
    assert module.store.active_generation_id() == active_before
    assert module.ledger.entries() == ledger_before
    assert tuple(sorted(module.store.generations_dir.iterdir())) == generations_before

    d2_prime = decision_for(
        first_child.transcript,
        child_proposals[0],
        event_id="accept-d2-prime",
    )
    final = module.resolve(ResolveRequest(first_child.generation_id, (d2_prime,)))
    assert isinstance(final, AcceptedGeneration)
    assert "".join(token.text for token in final.transcript.tokens) == "正甲正乙"
    grandchild = module._load_generation(final.generation_id, require_active=True)
    assert grandchild.audit.proposals == ()

    for loaded, expected_count in ((parent, 2), (child, 1), (grandchild, 0)):
        transcript = loaded.result.transcript
        proposals = loaded.audit.proposals
        correction_audits = loaded.audit.correction_audits
        audio_audits = loaded.audit.audio_audits
        arbitrations = loaded.audit.arbitrations
        owned = tuple(
            proposal_id
            for receipt in (*correction_audits, *audio_audits)
            for proposal_id in receipt.proposal_ids
        )
        assert owned == tuple(proposal.id for proposal in proposals)
        assert len(proposals) == expected_count
        assert tuple(receipt.proposal_id for receipt in arbitrations) == tuple(
            proposal.id for proposal in proposals
        )
        assert all(receipt.generation_id == transcript.generation_id for receipt in arbitrations)

    assert {receipt.request.sha256 for receipt in parent.audit.correction_audits}.isdisjoint(
        receipt.request.sha256 for receipt in child.audit.correction_audits
    )
    assert {receipt.request.sha256 for receipt in parent.audit.audio_audits}.isdisjoint(
        receipt.request.sha256 for receipt in child.audit.audio_audits
    )


def test_module_rejects_caller_supplied_replacement_alignment_before_ledger_append(
    tmp_path: Path,
) -> None:
    module, source = _module(tmp_path)
    created = module.create(
        CreateRequest(episode_id="episode-generic-alignment", source_audio=source)
    )
    assert isinstance(created, NeedsReview)
    decision = _decision_for_created(created, event_id="generic-alignment")
    evidence_id = decision.audio_evidence_ids[0]
    midpoint = (decision.target_start_ms + decision.target_end_ms) // 2
    alignment = (
        ReplacementLexemeAlignment(
            lexeme=decision.replacement_lexemes[0],
            start_ms=decision.target_start_ms,
            end_ms=midpoint,
            method="forced_alignment",
            evidence_ids=(evidence_id,),
        ),
        ReplacementLexemeAlignment(
            lexeme=decision.replacement_lexemes[1],
            start_ms=midpoint,
            end_ms=decision.target_end_ms,
            method="forced_alignment",
            evidence_ids=(evidence_id,),
        ),
    )
    forged = decision.model_copy(update={"replacement_alignment": alignment})
    ledger_before = module.ledger.entries()

    with pytest.raises(ModuleInvariantError, match="typed ReplacementAlignmentReceipt"):
        module.resolve(ResolveRequest(created.generation_id, (forged,)))

    assert module.ledger.entries() == ledger_before
    assert module.store.active_generation_id() == created.generation_id


def test_fresh_load_rejects_forged_exact_timing_on_replacement_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module, source = _module(tmp_path)
    created = module.create(
        CreateRequest(episode_id="episode-forged-child-clock", source_audio=source)
    )
    assert isinstance(created, NeedsReview)
    decision = _decision_for_created(created, event_id="coarse-child")
    resolved = module.resolve(ResolveRequest(created.generation_id, (decision,)))
    assert isinstance(resolved, AcceptedGeneration)
    span = resolved.transcript.spans[0]
    midpoint = (span.start_ms + span.end_ms) // 2
    generic_evidence_id = resolved.transcript.tokens[0].evidence_ids[0]
    forged_tokens = tuple(
        token.model_copy(
            update={
                "start_ms": start_ms,
                "end_ms": end_ms,
                "timing_basis": "forced_alignment",
                "alignment_evidence_ids": (generic_evidence_id,),
            }
        )
        for token, start_ms, end_ms in zip(
            resolved.transcript.tokens,
            (span.start_ms, midpoint),
            (midpoint, span.end_ms),
            strict=True,
        )
    )
    forged_span = span.model_copy(
        update={
            "alignment": "lexeme_exact",
            "alignment_evidence_ids": (generic_evidence_id,),
        }
    )
    forged_transcript = resolved.transcript.model_copy(
        update={
            "tokens": forged_tokens,
            "spans": (forged_span,),
            "content_hash": canonical_content_hash(forged_tokens),
        }
    )
    forged_bytes = canonical_json_bytes(forged_transcript)
    original_read_artifact = GenerationStore.read_artifact

    def forged_read_artifact(
        store: GenerationStore,
        generation_id: str,
        name: str,
    ) -> bytes:
        if generation_id == resolved.generation_id and name == "canonical_transcript.json":
            return forged_bytes
        return original_read_artifact(store, generation_id, name)

    monkeypatch.setattr(GenerationStore, "read_artifact", forged_read_artifact)

    with pytest.raises(GenerationIsolationError, match="typed ReplacementAlignmentReceipt"):
        _module(tmp_path)


def test_missing_speech_coverage_is_durable_and_cannot_be_human_accepted(
    tmp_path: Path,
) -> None:
    module, source = _module(tmp_path, speech_coverage_analyzer=None)

    created = module.create(CreateRequest(episode_id="episode-no-coverage", source_audio=source))

    assert isinstance(created, NeedsReview)
    assert created.transcript.verified_speech_coverage_receipt_hash is None
    assert {issue.code for issue in created.issues} >= {"speech_coverage_receipt_missing"}
    reopened, _ = _module(tmp_path, speech_coverage_analyzer=None)
    loaded = reopened._load_generation(created.generation_id, require_active=True).result
    assert loaded.transcript == created.transcript

    reviewed = reopened.resolve(
        ResolveRequest(
            created.generation_id,
            (_decision_for_created(created, event_id="cannot-waive-coverage"),),
        )
    )
    assert isinstance(reviewed, NeedsReview)
    assert reviewed.transcript.verified_speech_coverage_receipt_hash is None


def test_failed_speech_coverage_analyzer_persists_blocking_receipt(
    tmp_path: Path,
) -> None:
    analyzer = FixtureSpeechCoverageAnalyzer(failure_reason="fixture analyzer unavailable")
    module, source = _module(tmp_path, speech_coverage_analyzer=analyzer)

    created = module.create(
        CreateRequest(episode_id="episode-coverage-failed", source_audio=source)
    )

    assert isinstance(created, NeedsReview)
    assert created.transcript.verified_speech_coverage_receipt_hash is None
    assert {issue.code for issue in created.issues} >= {"speech_coverage_analyzer_failed"}
    stored = json.loads(
        module.store.read_artifact(
            created.generation_id,
            "speech_coverage_receipt.json",
        )
    )
    assert stored["status"] == "failed"
    reopened, _ = _module(
        tmp_path,
        speech_coverage_analyzer=FixtureSpeechCoverageAnalyzer(
            failure_reason="fixture analyzer unavailable"
        ),
    )
    assert reopened._load_generation(
        created.generation_id, require_active=True
    ).result.transcript == (created.transcript)


@pytest.mark.parametrize(
    ("activity", "expected_gap"),
    (
        (((0, 500),), (0, 200)),
        (((200, 850),), (500, 550)),
        (((550, 1_000),), (850, 1_000)),
    ),
    ids=("leading", "middle", "trailing"),
)
def test_uncovered_speech_activity_is_stable_across_restart(
    tmp_path: Path,
    activity: tuple[tuple[int, int], ...],
    expected_gap: tuple[int, int],
) -> None:
    analyzer = FixtureSpeechCoverageAnalyzer(activity)
    module, source = _module(tmp_path, speech_coverage_analyzer=analyzer)

    created = module.create(CreateRequest(episode_id="episode-coverage-gap", source_audio=source))

    assert isinstance(created, NeedsReview)
    gap_issues = tuple(
        issue for issue in created.issues if issue.code == "uncovered_speech_activity"
    )
    assert len(gap_issues) == 1
    stored = json.loads(
        module.store.read_artifact(
            created.generation_id,
            "speech_coverage_receipt.json",
        )
    )
    assert [(item["start_ms"], item["end_ms"]) for item in stored["uncovered_intervals"]] == [
        expected_gap
    ]

    reopened, _ = _module(
        tmp_path,
        speech_coverage_analyzer=FixtureSpeechCoverageAnalyzer(activity),
    )
    reloaded = reopened._load_generation(created.generation_id, require_active=True).result
    assert tuple(
        issue.id
        for issue in reloaded.transcript.review_issues
        if issue.code == "uncovered_speech_activity"
    ) == (gap_issues[0].id,)


def test_restart_rejects_changed_speech_coverage_analyzer_identity(
    tmp_path: Path,
) -> None:
    module, source = _module(tmp_path)
    module.create(CreateRequest(episode_id="episode-coverage-identity", source_audio=source))

    with pytest.raises(GenerationIsolationError, match="runtime Adapter identities differ"):
        _module(
            tmp_path,
            speech_coverage_analyzer=FixtureSpeechCoverageAnalyzer(coverage_tolerance_ms=1),
        )


def test_concurrent_resolve_allows_one_child_and_keeps_ledger_active_consistent(
    tmp_path: Path,
) -> None:
    module, source = _module(tmp_path)
    created = module.create(CreateRequest(episode_id="episode-anji", source_audio=source))
    assert isinstance(created, NeedsReview)
    contender, _ = _module(tmp_path)
    decision_a = _decision_for_created(created, event_id="concurrent-a")
    decision_b = _decision_for_created(created, event_id="concurrent-b")
    barrier = threading.Barrier(2)

    def resolve(instance: PodcastSubtitleV2, decision: CorrectionDecision):
        barrier.wait(timeout=5)
        return instance.resolve(
            ResolveRequest(generation_id=created.generation_id, decisions=(decision,))
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(resolve, instance, decision)
            for instance, decision in ((module, decision_a), (contender, decision_b))
        ]
        outcomes: list[object] = []
        failures: list[BaseException] = []
        for future in futures:
            try:
                outcomes.append(future.result(timeout=20))
            except BaseException as exc:  # noqa: BLE001 - asserting the losing transaction
                failures.append(exc)

    assert len(outcomes) == 1
    assert isinstance(outcomes[0], AcceptedGeneration)
    assert len(failures) == 1
    assert isinstance(failures[0], GenerationIsolationError)
    assert len(module.ledger.entries()) == 1
    active_id = module.store.active_generation_id()
    assert active_id == outcomes[0].generation_id
    active = module._load_generation(active_id, require_active=True).result
    assert active.transcript.ledger_hash == module.ledger.head_hash


def test_append_then_activate_fault_recovers_exact_committed_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module, source = _module(tmp_path)
    created = module.create(CreateRequest(episode_id="episode-anji", source_audio=source))
    assert isinstance(created, NeedsReview)
    decision = _decision_for_created(created, event_id="crash-after-ledger-append")

    def crash_before_activate(*_args, **_kwargs) -> None:
        raise RuntimeError("injected crash after Ledger append")

    monkeypatch.setattr(module.store, "set_active_cas_locked", crash_before_activate)
    with pytest.raises(RuntimeError, match="injected crash"):
        module.resolve(ResolveRequest(generation_id=created.generation_id, decisions=(decision,)))

    assert len(module.ledger.entries()) == 1
    assert module.store.active_generation_id() == created.generation_id
    with module.store.episode_transaction():
        journal = module.store.read_resolution_journal_locked()
    assert journal is not None and journal["state"] == "prepared"
    child_id = str(journal["child_generation_id"])
    assert module.store.load(child_id).generation_id == child_id

    reopened, _ = _module(tmp_path)

    assert reopened.store.active_generation_id() == child_id
    assert reopened.ledger.head_hash == journal["child_ledger_hash"]
    recovered = reopened._load_generation(child_id, require_active=True).result
    assert recovered.transcript.ledger_hash == reopened.ledger.head_hash
    with reopened.store.episode_transaction():
        completed = reopened.store.read_resolution_journal_locked()
    assert completed is not None and completed["state"] == "completed"


def test_reopen_replays_every_parent_in_a_multi_decision_chain(tmp_path: Path) -> None:
    module, source = _module(tmp_path)
    created = module.create(CreateRequest(episode_id="episode-anji", source_audio=source))
    assert isinstance(created, NeedsReview)
    defer_payload = _decision_for_created(created, event_id="defer-first").model_dump(mode="python")
    defer_payload.update(
        {
            "action": "defer",
            "replacement_text": None,
            "replacement_lexemes": (),
            "replacement_alignment": (),
        }
    )
    deferred = module.resolve(
        ResolveRequest(
            created.generation_id,
            (CorrectionDecision.model_validate(defer_payload),),
        )
    )
    assert isinstance(deferred, NeedsReview)
    accepted = module.resolve(
        ResolveRequest(
            deferred.generation_id,
            (_decision_for_created(deferred, event_id="accept-second"),),
        )
    )
    assert isinstance(accepted, AcceptedGeneration)
    assert len(module.ledger.entries()) == 2

    reopened, _ = _module(tmp_path)

    assert reopened.store.active_generation_id() == accepted.generation_id
    loaded = reopened._load_generation(accepted.generation_id, require_active=True).result
    assert loaded.transcript == accepted.transcript


def test_active_load_rejects_unjournaled_ledger_head(tmp_path: Path) -> None:
    module, source = _module(tmp_path)
    created = module.create(CreateRequest(episode_id="episode-anji", source_audio=source))
    assert isinstance(created, NeedsReview)
    decision = _decision_for_created(created, event_id="unjournaled-event")
    module.ledger.append(
        decision,
        current_fingerprint=decision.evidence_fingerprint,
    )

    with pytest.raises(GenerationIsolationError, match="Ledger head"):
        module._load_generation(created.generation_id, require_active=True)
    with pytest.raises(GenerationIsolationError, match="Ledger head"):
        _module(tmp_path)


def test_terminal_loader_requires_exact_ledger_head_only_for_active(
    tmp_path: Path,
) -> None:
    module, source = _module(tmp_path)
    created = module.create(CreateRequest(episode_id="episode-anji", source_audio=source))
    assert isinstance(created, NeedsReview)
    terminal = module.store.load_latest_create_checkpoint()
    assert terminal is not None and terminal.checkpoint.stage == "complete"
    decision = _decision_for_created(created, event_id="terminal-prefix-event")
    module.ledger.append(
        decision,
        current_fingerprint=decision.evidence_fingerprint,
    )

    with pytest.raises(GenerationIsolationError, match="Ledger head"):
        module._load_terminal_create_checkpoint(terminal, require_active=True)

    historical, _ = module._load_terminal_create_checkpoint(
        terminal,
        require_active=False,
    )
    assert historical == created


def test_tampered_resolution_journal_blocks_reopen(tmp_path: Path) -> None:
    module, source = _module(tmp_path)
    created = module.create(CreateRequest(episode_id="episode-anji", source_audio=source))
    assert isinstance(created, NeedsReview)
    decision = _decision_for_created(created, event_id="journal-tamper")
    original = module.store.set_active_cas_locked

    def crash_before_activate(*_args, **_kwargs) -> None:
        raise RuntimeError("injected")

    module.store.set_active_cas_locked = crash_before_activate  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        module.resolve(ResolveRequest(generation_id=created.generation_id, decisions=(decision,)))
    module.store.set_active_cas_locked = original  # type: ignore[method-assign]
    journal_path = module.store.resolution_journal_path
    wrapper = json.loads(journal_path.read_text(encoding="utf-8"))
    wrapper["payload"]["child_generation_id"] = f"generation-{'f' * 64}"
    journal_path.write_text(json.dumps(wrapper), encoding="utf-8")

    with pytest.raises(ResolutionTransactionError, match="hash mismatch"):
        _module(tmp_path)


def test_draft_receipt_and_cross_generation_resolution_fail_closed(tmp_path: Path) -> None:
    module, source = _module(tmp_path, draft_receipt=True)
    with pytest.raises(ModuleInvariantError, match="accepted Normalization"):
        module.create(CreateRequest(episode_id="episode-anji", source_audio=source))

    accepted_module, source = _module(tmp_path / "accepted")
    created = accepted_module.create(CreateRequest(episode_id="episode-anji", source_audio=source))
    assert isinstance(created, NeedsReview)
    span_ids = tuple(span.id for span in created.transcript.spans)
    decision = CorrectionDecision(
        event_id="cross-generation",
        episode_id="episode-anji",
        generation_id=f"generation-{'f' * 64}",
        target_span_ids=span_ids,
        target_start_ms=created.transcript.tokens[0].start_ms,
        target_end_ms=created.transcript.tokens[-1].end_ms,
        evidence_fingerprint=review_target_fingerprint(created.transcript, span_ids),
        issue_ids=tuple(
            issue.id for issue in created.transcript.review_issues if issue.status == "unresolved"
        ),
        audio_evidence_ids=tuple(
            sorted({item for token in created.transcript.tokens for item in token.evidence_ids})
        ),
        evidence_basis="audio",
        action="replace",
        replacement_text="哥大畢業典禮",
        replacement_lexemes=("哥大", "畢業典禮"),
        actor_kind="human",
        actor="reviewer",
        rationale="test",
        timestamp=datetime(2026, 8, 12, tzinfo=timezone.utc),
    )
    with pytest.raises(GenerationIsolationError, match="stale|cross-generation"):
        accepted_module.resolve(
            ResolveRequest(generation_id=created.generation_id, decisions=(decision,))
        )


def test_tampered_generation_fails_before_projection(tmp_path: Path) -> None:
    module, source = _module(tmp_path)
    created = module.create(CreateRequest(episode_id="episode-anji", source_audio=source))
    canonical = module.store.generation_dir(created.generation_id) / "canonical_transcript.json"
    canonical.write_bytes(canonical.read_bytes() + b" ")
    with pytest.raises(ArtifactHashMismatchError):
        module.store.load(created.generation_id)


@pytest.mark.parametrize("orphan_kind", ("recognition", "reference"))
def test_fresh_load_rejects_coordinated_orphan_risk_evidence_id(
    tmp_path: Path,
    orphan_kind: str,
) -> None:
    module, source = _module(tmp_path)
    created = module.create(
        CreateRequest(
            episode_id=f"episode-orphan-{orphan_kind}-risk-evidence",
            source_audio=source,
        )
    )
    directory = module.store.generation_dir(created.generation_id)
    risks = json.loads((directory / "risks.json").read_text(encoding="utf-8"))
    risk = next(
        item
        for item in risks
        if item["issue"]["code"]
        not in {
            "speech_coverage_receipt_missing",
            "speech_coverage_analyzer_failed",
            "uncovered_speech_activity",
            "recognition_outside_audio_activity",
        }
    )
    if orphan_kind == "recognition":
        orphan_id = f"evidence:{'f' * 64}:orphan-token"
        risk["evidence_ids"] = [orphan_id]
        risk["issue"]["audio_evidence_ids"] = [orphan_id]
        issue_field = "audio_evidence_ids"
    else:
        orphan_id = "reference-orphan-r9"
        risk["supporting_reference_ids"] = [orphan_id]
        risk["issue"]["reference_evidence_ids"] = [orphan_id]
        issue_field = "reference_evidence_ids"

    transcript_payload = json.loads(
        (directory / "canonical_transcript.json").read_text(encoding="utf-8")
    )
    matching_issue = next(
        item for item in transcript_payload["review_issues"] if item["id"] == risk["issue"]["id"]
    )
    matching_issue[issue_field] = [orphan_id]
    transcript = CanonicalTranscript.model_validate(transcript_payload)
    risks_bytes = canonical_json_bytes(tuple(risks))
    transcript_bytes = canonical_json_bytes(transcript)
    _rewrite_generation_consistently(
        module,
        created.generation_id,
        artifacts={
            "risks.json": risks_bytes,
            "canonical_transcript.json": transcript_bytes,
        },
        stage_hashes={
            "risks": hash_object(tuple(risks)),
            "canonical_transcript": hash_object(transcript),
        },
    )

    with pytest.raises(
        GenerationIsolationError,
        match="orphan.*Evidence|complete checkpoint Generation binding",
    ):
        _module(tmp_path)


@pytest.mark.parametrize("artifact_field", ["request", "response"])
def test_fresh_process_rejects_tampered_exact_correction_execution_bytes(
    tmp_path: Path,
    artifact_field: str,
) -> None:
    module, source = _module(tmp_path)
    created = module.create(
        CreateRequest(episode_id=f"episode-correction-{artifact_field}", source_audio=source)
    )
    audits = json.loads(
        module.store.read_artifact(created.generation_id, "correction_audit_receipts.json")
    )
    artifact = audits[0][artifact_field]
    name = artifact["uri"].removeprefix("generation-artifact://")
    path = module.store.generation_dir(created.generation_id) / name
    path.write_bytes(path.read_bytes() + b" ")

    with pytest.raises(ArtifactHashMismatchError, match="artifact hash mismatch"):
        _module(tmp_path)


def test_fresh_process_rejects_changed_corrector_executable_identity(tmp_path: Path) -> None:
    module, source = _module(tmp_path)
    module.create(CreateRequest(episode_id="episode-correction-identity", source_audio=source))
    changed = FixtureCorrectorAdapter()
    changed._identity = replace(  # type: ignore[attr-defined]  # hostile runtime fixture
        changed.identity,
        runtime_hash=hash_object({"runtime": "changed-after-restart"}),
    )
    declared = AdapterIdentity(
        name=changed.identity.adapter_name,
        version=changed.identity.adapter_version,
        config_hash=changed.identity.config_hash,
        execution_mode=changed.identity.execution_mode,
    )

    with pytest.raises(GenerationIsolationError, match="runtime Adapter identities differ"):
        _module(tmp_path, corrector=changed, corrector_identity=declared)


def test_corrector_replay_runs_before_commit_and_fresh_load_without_new_work(
    tmp_path: Path,
) -> None:
    corrector = _CountingCorrector(FixtureCorrectorAdapter())
    module, source = _module(tmp_path, corrector=corrector)

    created = module.create(
        CreateRequest(episode_id="episode-corrector-replay", source_audio=source)
    )

    assert not isinstance(created, Interrupted)
    assert corrector.calls == 1
    assert corrector.replay_calls >= 3

    fresh_corrector = _CountingCorrector(FixtureCorrectorAdapter())
    fresh, _ = _module(tmp_path, corrector=fresh_corrector)
    assert fresh.store.active_generation_id() == created.generation_id
    assert fresh_corrector.calls == 0
    # A terminal checkpoint seals the already-verified execution proof.  A
    # fresh terminal replay is storage-only and must not call the Adapter.
    assert fresh_corrector.replay_calls == 0


@pytest.mark.parametrize(
    ("texts", "policy", "expected_type"),
    (
        (
            ("Podcast",),
            SubtitlePolicy(permit_unresolved_low_risk=False),
            NeedsReview,
        ),
        (
            ("Podcast",),
            SubtitlePolicy(permit_unresolved_low_risk=True),
            AcceptedGeneration,
        ),
    ),
)
def test_complete_checkpoint_fresh_create_replay_calls_no_adapter(
    tmp_path: Path,
    texts: tuple[str, ...],
    policy: SubtitlePolicy,
    expected_type: type,
) -> None:
    first_corrector = _CountingCorrector(FixtureCorrectorAdapter())
    first_auditor = _ReplayCountingAudioAuditor(FixtureAudioAuditorAdapter())
    first, source = _single_stream_module(
        tmp_path,
        texts=texts,
        corrector=first_corrector,
        audio_auditor=first_auditor,
    )
    request = CreateRequest(
        episode_id="episode-terminal-replay",
        source_audio=source,
        policy=policy,
    )
    created = first.create(request)
    assert isinstance(created, expected_type)
    terminal = first.store.load_latest_create_checkpoint()
    assert terminal is not None and terminal.checkpoint.stage == "complete"

    fresh_corrector = _CountingCorrector(FixtureCorrectorAdapter())
    fresh_auditor = _ReplayCountingAudioAuditor(FixtureAudioAuditorAdapter())
    fresh, fresh_source = _single_stream_module(
        tmp_path,
        texts=texts,
        corrector=fresh_corrector,
        audio_auditor=fresh_auditor,
    )
    normalizer = fresh._normalizer
    recognizers = fresh._recognizers
    with (
        patch.object(normalizer, "normalize", wraps=normalizer.normalize) as normalize,
        patch.object(recognizers[0], "recognize", wraps=recognizers[0].recognize) as recognize,
    ):
        replayed = fresh.create(
            CreateRequest(
                episode_id="episode-terminal-replay",
                source_audio=fresh_source,
                policy=policy,
            )
        )

    assert isinstance(replayed, expected_type)
    assert replayed == created
    assert normalize.call_count == 0
    assert recognize.call_count == 0
    assert fresh_corrector.calls == 0
    assert fresh_corrector.replay_calls == 0
    assert fresh_auditor.audit_calls == 0
    assert fresh_auditor.replay_calls == 0


def test_complete_create_replay_preserves_verified_resolved_descendant(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module, source = _module(tmp_path)
    request = CreateRequest(episode_id="episode-anji", source_audio=source)
    created = module.create(request)
    assert isinstance(created, NeedsReview)
    resolved = module.resolve(
        ResolveRequest(
            created.generation_id,
            (_decision_for_created(created, event_id="terminal-replay-descendant"),),
        )
    )
    assert isinstance(resolved, AcceptedGeneration)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("complete Create replay attempted new Adapter work")

    monkeypatch.setattr(module._normalizer, "normalize", forbidden)
    for recognizer in module._recognizers:
        monkeypatch.setattr(recognizer, "recognize", forbidden)
    monkeypatch.setattr(module._corrector, "propose_with_receipt", forbidden)
    monkeypatch.setattr(module._audio_auditor, "audit", forbidden)

    replayed = module.create(request)

    assert replayed == resolved
    assert module.store.active_generation_id() == resolved.generation_id


def test_generation_commit_crash_recovers_without_repeating_paid_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_corrector = _CountingCorrector(FixtureCorrectorAdapter())
    first_auditor = _ReplayCountingAudioAuditor(FixtureAudioAuditorAdapter())
    first, source = _single_stream_module(
        tmp_path,
        texts=("Podcast",),
        corrector=first_corrector,
        audio_auditor=first_auditor,
    )

    def crash_after_generation_commit(**_kwargs):
        raise RuntimeError("injected after inactive Generation commit")

    monkeypatch.setattr(first, "_seal_and_activate_create", crash_after_generation_commit)
    request = CreateRequest(
        episode_id="episode-generation-commit-crash",
        source_audio=source,
    )
    with pytest.raises(RuntimeError, match="inactive Generation"):
        first.create(request)
    partial = first.store.load_latest_create_checkpoint()
    assert partial is not None and partial.checkpoint.stage == "audio_audit_ready"
    inactive = tuple(path.name for path in first.store.generations_dir.iterdir() if path.is_dir())
    assert len(inactive) == 1
    assert first.store.load(inactive[0]).generation_id == inactive[0]
    with pytest.raises(GenerationNotFoundError):
        first.store.active_generation_id()

    fresh_corrector = _CountingCorrector(FixtureCorrectorAdapter())
    fresh_auditor = _ReplayCountingAudioAuditor(FixtureAudioAuditorAdapter())
    fresh, fresh_source = _single_stream_module(
        tmp_path,
        texts=("Podcast",),
        corrector=fresh_corrector,
        audio_auditor=fresh_auditor,
    )
    recovered = fresh.create(
        CreateRequest(
            episode_id="episode-generation-commit-crash",
            source_audio=fresh_source,
        )
    )
    assert not isinstance(recovered, Interrupted)
    assert fresh_corrector.calls == 0
    assert fresh_auditor.audit_calls == 0
    assert fresh.store.active_generation_id() == recovered.generation_id
    terminal = fresh.store.load_latest_create_checkpoint()
    assert terminal is not None and terminal.checkpoint.stage == "complete"


def test_generation_commit_crash_does_not_repeat_arbitration_decision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verdict = ArbitrationVerdict(
        proposal_id="proposal-last-span",
        status="accepted",
        selected_text="正確候選",
        confidence=0.99,
        rationale="fixture exact audio",
    )
    first_arbiter = _ReplayCountingArbiter(FixtureArbiterAdapter({"proposal-last-span": verdict}))
    first, source = _single_stream_module(
        tmp_path,
        texts=("原始", "文字"),
        corrector=_LastSpanProposalCorrector(),
        arbiter=first_arbiter,
    )

    def crash_after_generation_commit(**_kwargs):
        raise RuntimeError("injected after arbitrated Generation commit")

    monkeypatch.setattr(first, "_seal_and_activate_create", crash_after_generation_commit)
    request = CreateRequest(
        episode_id="episode-arbitration-generation-crash",
        source_audio=source,
    )
    with pytest.raises(RuntimeError, match="arbitrated Generation"):
        first.create(request)
    assert first_arbiter.decide_calls == 1
    monkeypatch.undo()

    fresh_arbiter = _ReplayCountingArbiter(FixtureArbiterAdapter({"proposal-last-span": verdict}))
    fresh, fresh_source = _single_stream_module(
        tmp_path,
        texts=("原始", "文字"),
        corrector=_LastSpanProposalCorrector(),
        arbiter=fresh_arbiter,
    )
    recovered = fresh.create(
        CreateRequest(
            episode_id="episode-arbitration-generation-crash",
            source_audio=fresh_source,
        )
    )
    assert not isinstance(recovered, Interrupted)
    assert fresh_arbiter.decide_calls == 0
    assert fresh_arbiter.replay_calls >= 1


@pytest.mark.parametrize("crash_after_cas", (False, True))
def test_complete_checkpoint_activation_crash_is_idempotently_repairable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    crash_after_cas: bool,
) -> None:
    first, source = _single_stream_module(
        tmp_path,
        texts=("Podcast",),
        corrector=FixtureCorrectorAdapter(),
    )
    original = first.store.set_active_cas_locked

    def crash(generation_id: str, *, expected_generation_id: str | None) -> None:
        if crash_after_cas:
            original(
                generation_id,
                expected_generation_id=expected_generation_id,
            )
        raise RuntimeError("injected activation crash")

    monkeypatch.setattr(first.store, "set_active_cas_locked", crash)
    with pytest.raises(RuntimeError, match="activation crash"):
        first.create(
            CreateRequest(
                episode_id="episode-activation-crash",
                source_audio=source,
            )
        )
    terminal = first.store.load_latest_create_checkpoint()
    assert terminal is not None and terminal.checkpoint.stage == "complete"
    if crash_after_cas:
        assert first.store.active_generation_id() == terminal.checkpoint.generation_id
    else:
        with pytest.raises(GenerationNotFoundError):
            first.store.active_generation_id()

    fresh_corrector = _CountingCorrector(FixtureCorrectorAdapter())
    fresh_auditor = _ReplayCountingAudioAuditor(FixtureAudioAuditorAdapter())
    fresh, fresh_source = _single_stream_module(
        tmp_path,
        texts=("Podcast",),
        corrector=fresh_corrector,
        audio_auditor=fresh_auditor,
    )
    recovered = fresh.create(
        CreateRequest(
            episode_id="episode-activation-crash",
            source_audio=fresh_source,
        )
    )
    assert not isinstance(recovered, Interrupted)
    assert fresh_corrector.calls == fresh_corrector.replay_calls == 0
    assert fresh_auditor.audit_calls == fresh_auditor.replay_calls == 0
    assert fresh.store.active_generation_id() == recovered.generation_id


@pytest.mark.parametrize("tamper_target", ("predecessor", "complete_binding"))
def test_complete_seal_is_fully_reopened_before_active_pointer_moves(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tamper_target: str,
) -> None:
    module, source = _single_stream_module(
        tmp_path,
        texts=("Podcast",),
        corrector=FixtureCorrectorAdapter(),
    )
    original = module.store.commit_create_checkpoint

    def commit_then_tamper(checkpoint, *, artifacts):
        stored = original(checkpoint, artifacts=artifacts)
        if checkpoint.stage == "complete":
            if tamper_target == "predecessor":
                assert checkpoint.previous_checkpoint_id is not None
                target = (
                    module.store.create_checkpoint_dir(checkpoint.previous_checkpoint_id)
                    / "checkpoint.json"
                )
            else:
                target = stored.directory / "checkpoint" / "complete_binding.json"
            target.write_bytes(target.read_bytes() + b" ")
        return stored

    monkeypatch.setattr(module.store, "commit_create_checkpoint", commit_then_tamper)
    with pytest.raises((ArtifactHashMismatchError, GenerationIsolationError)):
        module.create(
            CreateRequest(
                episode_id=f"episode-seal-{tamper_target}",
                source_audio=source,
            )
        )
    with pytest.raises(GenerationNotFoundError):
        module.store.active_generation_id()


def test_complete_seal_rejects_ledger_advance_before_active_cas(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module, source = _module(tmp_path)
    first = module.create(CreateRequest(episode_id="episode-seal-ledger-race", source_audio=source))
    assert isinstance(first, NeedsReview)
    original_terminal_outcome = module._terminal_generation_outcome
    injected = False

    def verify_then_advance_ledger(checkpoint):
        nonlocal injected
        outcome = original_terminal_outcome(checkpoint)
        if not injected:
            assert isinstance(outcome, NeedsReview)
            decision = _decision_for_created(outcome, event_id="seal-ledger-race")
            module.ledger.append(
                decision,
                current_fingerprint=decision.evidence_fingerprint,
            )
            injected = True
        return outcome

    monkeypatch.setattr(
        module,
        "_terminal_generation_outcome",
        verify_then_advance_ledger,
    )
    with patch.object(
        module.store,
        "set_active_cas_locked",
        wraps=module.store.set_active_cas_locked,
    ) as active_cas:
        with pytest.raises(GenerationIsolationError, match="Ledger head"):
            module.create(
                CreateRequest(
                    episode_id="episode-seal-ledger-race",
                    source_audio=source,
                    vocabulary=("new-create-operation",),
                )
            )

    assert injected
    assert active_cas.call_count == 0
    assert module.store.active_generation_id() == first.generation_id


def test_complete_checkpoint_never_overwrites_third_party_active(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module, source = _single_stream_module(
        tmp_path,
        texts=("Podcast",),
        corrector=FixtureCorrectorAdapter(),
    )

    def crash_before_cas(*_args, **_kwargs) -> None:
        raise RuntimeError("injected before CAS")

    monkeypatch.setattr(module.store, "set_active_cas_locked", crash_before_cas)
    request = CreateRequest(
        episode_id="episode-stale-complete",
        source_audio=source,
    )
    with pytest.raises(RuntimeError, match="before CAS"):
        module.create(request)
    terminal = module.store.load_latest_create_checkpoint()
    assert terminal is not None and terminal.checkpoint.stage == "complete"
    third = module.store.commit(
        manifest={"stage": "third-party"},
        artifacts={"value.json": {"owner": "third-party"}},
    )
    module.store.set_active(third.generation_id)
    monkeypatch.undo()

    with pytest.raises(GenerationIsolationError, match="active generation changed"):
        module.create(request)
    assert module.store.active_generation_id() == third.generation_id


def test_complete_checkpoint_pointer_publication_tail_is_repairable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module, source = _single_stream_module(
        tmp_path,
        texts=("Podcast",),
        corrector=FixtureCorrectorAdapter(),
    )
    original = store_module._replace_fsynced
    pointer_publications = 0

    def crash_on_complete_pointer(source_path: Path, destination: Path) -> None:
        nonlocal pointer_publications
        if destination.name == "active-create-checkpoint.json":
            pointer_publications += 1
            if pointer_publications == 5:
                raise RuntimeError("injected complete pointer publication tail")
        original(source_path, destination)

    monkeypatch.setattr(store_module, "_replace_fsynced", crash_on_complete_pointer)
    with pytest.raises(RuntimeError, match="pointer publication tail"):
        module.create(
            CreateRequest(
                episode_id="episode-complete-pointer-tail",
                source_audio=source,
            )
        )
    partial = module.store.load_latest_create_checkpoint()
    assert partial is not None and partial.checkpoint.stage == "audio_audit_ready"
    complete_directories = tuple(
        path
        for path in module.store.create_checkpoints_dir.iterdir()
        if path.is_dir()
        and json.loads((path / "checkpoint.json").read_text(encoding="utf-8"))["stage"]
        == "complete"
    )
    assert len(complete_directories) == 1
    monkeypatch.undo()

    fresh_corrector = _CountingCorrector(FixtureCorrectorAdapter())
    fresh_auditor = _ReplayCountingAudioAuditor(FixtureAudioAuditorAdapter())
    fresh, fresh_source = _single_stream_module(
        tmp_path,
        texts=("Podcast",),
        corrector=fresh_corrector,
        audio_auditor=fresh_auditor,
    )
    recovered = fresh.create(
        CreateRequest(
            episode_id="episode-complete-pointer-tail",
            source_audio=fresh_source,
        )
    )
    assert not isinstance(recovered, Interrupted)
    assert fresh_corrector.calls == 0
    assert fresh_auditor.audit_calls == 0
    assert fresh.store.active_generation_id() == recovered.generation_id


def test_corrector_replay_failure_after_work_cannot_publish_generation(
    tmp_path: Path,
) -> None:
    corrector = _RejectingReplayCorrector(FixtureCorrectorAdapter())
    module, source = _module(tmp_path, corrector=corrector)

    with pytest.raises(GenerationIsolationError, match="not reproducible"):
        module.create(
            CreateRequest(
                episode_id="episode-corrector-replay-rejected",
                source_audio=source,
            )
        )

    assert corrector.calls == 1
    assert corrector.replay_calls == 1
    assert module.ledger.entries() == ()
    with pytest.raises(GenerationNotFoundError, match="no active"):
        module.store.active_generation_id()
    assert not module.store.generations_dir.exists() or not tuple(
        module.store.generations_dir.iterdir()
    )


@pytest.mark.parametrize("artifact_field", ("request", "response"))
def test_fresh_load_rejects_coordinated_correction_execution_byte_rewrite(
    tmp_path: Path,
    artifact_field: str,
) -> None:
    module, source = _module(tmp_path)
    created = module.create(
        CreateRequest(
            episode_id=f"episode-correction-coordinated-{artifact_field}",
            source_audio=source,
        )
    )
    _coordinated_correction_byte_tamper(
        module,
        created.generation_id,
        artifact_field=artifact_field,
    )

    with pytest.raises(GenerationIsolationError, match="not reproducible"):
        _module(tmp_path)


@pytest.mark.parametrize(
    ("field", "forged"),
    (
        ("preaudit_generation_id", "generation-" + "0" * 64),
        ("preaudit_content_hash", "0" * 64),
    ),
)
def test_fresh_load_rejects_coordinated_correction_receipt_lineage_rewrite(
    tmp_path: Path,
    field: str,
    forged: str,
) -> None:
    module, source = _module(tmp_path)
    created = module.create(
        CreateRequest(
            episode_id=f"episode-correction-lineage-{field}",
            source_audio=source,
        )
    )
    directory = module.store.generation_dir(created.generation_id)
    payloads = json.loads(
        (directory / "correction_audit_receipts.json").read_text(encoding="utf-8")
    )
    payloads[0][field] = forged
    typed = tuple(CorrectionAuditReceipt.model_validate(item) for item in payloads)
    audits_bytes = canonical_json_bytes(typed)
    _rewrite_generation_consistently(
        module,
        created.generation_id,
        artifacts={"correction_audit_receipts.json": audits_bytes},
        stage_hashes={"correction_audit_receipts": hash_object(typed)},
    )

    with pytest.raises(
        GenerationIsolationError,
        match="immutable pre-audit transcript|complete checkpoint Generation binding",
    ):
        _module(tmp_path)


def test_audio_mutation_during_normalization_or_recognition_fails_closed(
    tmp_path: Path,
) -> None:
    normalization_module, source = _module(tmp_path / "normalization")
    normalization_module._normalizer = _MutatingNormalizer(normalization_module._normalizer)
    with pytest.raises(GenerationIsolationError, match="during normalization"):
        normalization_module.create(
            CreateRequest(episode_id="episode-mutated-source", source_audio=source)
        )
    assert source.read_bytes() == b"source-audio"

    recognition_module, source = _module(tmp_path / "recognition")
    normalized = tmp_path / "recognition" / "normalized.wav"
    recognition_module._recognizers = (
        _MutatingRecognizer(recognition_module._recognizers[0], normalized),
    )
    with pytest.raises(GenerationIsolationError, match="during recognition"):
        recognition_module.create(
            CreateRequest(episode_id="episode-mutated-normalized", source_audio=source)
        )
    assert not tuple(recognition_module.store.audio_staging_dir.iterdir())


def test_speech_coverage_exception_leaves_no_audio_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module, source = _module(tmp_path)

    def fail_coverage(_request):
        raise RuntimeError("fixture speech coverage crash")

    monkeypatch.setattr(module._speech_coverage_analyzer, "analyze", fail_coverage)

    with pytest.raises(RuntimeError, match="speech coverage crash"):
        module.create(CreateRequest(episode_id="episode-coverage-crash", source_audio=source))
    assert not tuple(module.store.audio_staging_dir.iterdir())


def test_generation_reloads_from_owned_audio_after_external_files_disappear(
    tmp_path: Path,
) -> None:
    module, source = _module(tmp_path)
    source_payload = source.read_bytes()
    normalized = tmp_path / "normalized.wav"
    normalized_payload = normalized.read_bytes()
    created = module.create(CreateRequest(episode_id="episode-owned-audio", source_audio=source))

    source.unlink()
    normalized.unlink()
    loaded = module._load_generation(created.generation_id, require_active=True)
    receipt = loaded.normalization.receipt
    stored = module.store.load(created.generation_id)
    audio_artifacts = json.loads(
        module.store.read_artifact(created.generation_id, "audio_artifacts.json")
    )

    assert (
        module.store.audio_path(
            receipt.source.sha256, size_bytes=receipt.source.size_bytes
        ).read_bytes()
        == source_payload
    )
    assert (
        module.store.audio_path(
            receipt.normalized.sha256, size_bytes=receipt.normalized.size_bytes
        ).read_bytes()
        == normalized_payload
    )
    assert audio_artifacts == {
        "source": {
            "sha256": receipt.source.sha256,
            "size_bytes": receipt.source.size_bytes,
        },
        "normalized": {
            "sha256": receipt.normalized.sha256,
            "size_bytes": receipt.normalized.size_bytes,
        },
    }
    assert stored.manifest["stage_artifact_hashes"]["audio_artifacts"] == hash_object(
        audio_artifacts
    )


def test_generation_load_fails_when_owned_audio_is_tampered(tmp_path: Path) -> None:
    module, source = _module(tmp_path)
    created = module.create(CreateRequest(episode_id="episode-audio-tamper", source_audio=source))
    receipt = module._load_generation(
        created.generation_id, require_active=True
    ).normalization.receipt
    normalized_cas = module.store.audio_path(
        receipt.normalized.sha256, size_bytes=receipt.normalized.size_bytes
    )
    normalized_cas.write_bytes(b"tampered-normalized-audio")

    with pytest.raises(ArtifactHashMismatchError, match="identity"):
        module._load_generation(created.generation_id, require_active=True)


def test_recognition_request_has_no_reference_vocabulary_prompt_surface(
    tmp_path: Path,
) -> None:
    with pytest.raises(TypeError, match="vocabulary"):
        RecognitionRequest(
            episode_id="episode",
            invocation_id="invocation",
            normalized_audio=tmp_path / "audio.wav",
            vocabulary=("reference-only-term",),
        )


def test_generation_persists_exact_recognition_request_context(
    tmp_path: Path,
) -> None:
    module, source = _module(tmp_path)
    created = module.create(
        CreateRequest(
            episode_id="episode-recognition-request",
            source_audio=source,
            language_hint="zh-Hant-TW",
        )
    )
    assert not isinstance(created, Interrupted)

    payload = json.loads(
        module.store.read_artifact(
            created.generation_id,
            "recognition_request.json",
        )
    )
    record = module.store.load(created.generation_id)

    assert payload["language_hint"] == "zh-Hant-TW"
    assert payload["context_policy_id"] == "nakama-verbatim-no-lexical-context-v1"
    assert payload["context_sha256"] == _sha(b"")
    assert payload["context_size_bytes"] == 0
    assert (
        record.manifest["input_artifact_hashes"]["recognition_request"] == payload["content_hash"]
    )
    assert (
        record.manifest["stage_artifact_hashes"]["recognition_request"] == payload["content_hash"]
    )
    assert (
        json.loads(
            module.store.read_artifact(
                created.generation_id,
                "recognition_independence_receipt.json",
            )
        )
        is None
    )


def test_speaker_attribution_persists_base_without_false_corroboration(
    tmp_path: Path,
) -> None:
    attributor = MicEnergySpeakerAttributor(
        envelope_loader=lambda _paths, _reference: object(),
        speaker_assigner=lambda _words, _envelopes: [0, 1],
        algorithm_config={"algorithm": "fixture", "version": 1},
    )
    module, source = _module(tmp_path, speaker_attributor=attributor)
    host = tmp_path / "host.wav"
    guest = tmp_path / "guest.wav"
    host.write_bytes(b"isolated-host-track")
    guest.write_bytes(b"isolated-guest-track")

    created = module.create(
        CreateRequest(
            episode_id="episode-anji",
            source_audio=source,
            speaker_tracks=(
                SpeakerTrackInput(host, "HOST"),
                SpeakerTrackInput(guest, "GUEST"),
            ),
        )
    )
    assert not isinstance(created, Interrupted)
    assert tuple(recognizer.calls for recognizer in module._recognizers) == (1, 1)
    loaded = module._load_generation(created.generation_id, require_active=True)
    canonical_evidence = loaded.recognition.evidence
    base_evidence = loaded.recognition.speaker_base_evidence
    provenance = loaded.recognition.speaker_provenance
    assert len(base_evidence) == 1
    assert base_evidence[0].adapter == "primary"
    assert canonical_evidence[0].adapter == "mic-energy-speaker-attribution"
    assert all(item.adapter != "primary" for item in canonical_evidence)
    assert tuple(token.speaker for token in canonical_evidence[0].tokens) == (
        "HOST",
        "GUEST",
    )
    assert provenance is not None
    assert tuple(track.speaker_label for track in provenance.tracks) == ("HOST", "GUEST")
    assert tuple(track.sha256 for track in provenance.tracks) == (
        hash_file(host),
        hash_file(guest),
    )
    generation_dir = module.store.generation_dir(created.generation_id)
    for item in (*canonical_evidence, *base_evidence):
        assert (generation_dir / "raw_evidence" / f"{item.raw_output.sha256}.json").is_file()

    reopened, _ = _module(tmp_path, speaker_attributor=attributor)
    reloaded = reopened._load_generation(created.generation_id, require_active=True)
    assert reloaded.recognition.evidence == canonical_evidence
    assert reloaded.recognition.speaker_base_evidence == base_evidence
    assert reloaded.recognition.speaker_provenance == provenance


def test_configured_attributor_is_idle_when_evidence_already_has_speakers(
    tmp_path: Path,
) -> None:
    def must_not_run(_words, _envelopes):
        raise AssertionError("attribution must be request-scoped, not constructor-scoped")

    attributor = MicEnergySpeakerAttributor(
        envelope_loader=lambda _paths, _reference: object(),
        speaker_assigner=must_not_run,
        algorithm_config={"algorithm": "fixture", "version": 1},
    )
    module, source = _module(tmp_path, speaker_attributor=attributor)

    created = module.create(CreateRequest(episode_id="episode-speaker-ready", source_audio=source))

    assert not isinstance(created, Interrupted)
    loaded = module._load_generation(created.generation_id, require_active=True)
    assert loaded.recognition.speaker_base_evidence == ()
    assert loaded.recognition.speaker_provenance is None
    assert all(token.speaker == "guest" for token in loaded.recognition.evidence[0].tokens)


def test_missing_track_fails_before_normalization_provider_runs(tmp_path: Path) -> None:
    attributor = MicEnergySpeakerAttributor(
        envelope_loader=lambda _paths, _reference: object(),
        speaker_assigner=lambda _words, _envelopes: [0, 1],
        algorithm_config={"algorithm": "fixture", "version": 1},
    )
    module, source = _module(tmp_path, speaker_attributor=attributor)
    counting = _CountingNormalizer(module._normalizer)
    module._normalizer = counting
    existing = tmp_path / "host.wav"
    existing.write_bytes(b"host")

    with pytest.raises(ModuleInvariantError, match="not a file"):
        module.create(
            CreateRequest(
                episode_id="episode-missing-track",
                source_audio=source,
                speaker_tracks=(
                    SpeakerTrackInput(existing, "HOST"),
                    SpeakerTrackInput(tmp_path / "missing.wav", "GUEST"),
                ),
            )
        )

    assert counting.calls == 0


def test_track_changed_after_binding_is_rejected(tmp_path: Path) -> None:
    attributor = MicEnergySpeakerAttributor(
        envelope_loader=lambda _paths, _reference: object(),
        speaker_assigner=lambda _words, _envelopes: [0, 1],
        algorithm_config={"algorithm": "fixture", "version": 1},
    )
    module, source = _module(tmp_path, speaker_attributor=attributor)
    host = tmp_path / "host.wav"
    guest = tmp_path / "guest.wav"
    host.write_bytes(b"host")
    guest.write_bytes(b"guest")
    module._normalizer = _CountingNormalizer(module._normalizer, mutate=host)

    with pytest.raises(AdapterIntegrityError, match="Module binding"):
        module.create(
            CreateRequest(
                episode_id="episode-track-tamper",
                source_audio=source,
                speaker_tracks=(
                    SpeakerTrackInput(host, "HOST"),
                    SpeakerTrackInput(guest, "GUEST"),
                ),
            )
        )


@pytest.mark.parametrize(
    ("mode", "message"),
    [
        ("cross_invocation", "lineage or token count"),
        ("cross_lineage", "lineage or token count"),
        ("unknown_speaker", "unknown speaker"),
        ("changed_text", "truth or timing"),
        ("wrong_provenance", "bound microphone tracks"),
    ],
)
def test_module_rejects_malicious_attribution_before_adapter_verify(
    tmp_path: Path,
    mode: str,
    message: str,
) -> None:
    delegate = MicEnergySpeakerAttributor(
        envelope_loader=lambda _paths, _reference: object(),
        speaker_assigner=lambda _words, _envelopes: [0, 1],
        algorithm_config={"algorithm": "fixture", "version": 1},
    )
    module, source = _module(
        tmp_path,
        speaker_attributor=_TamperingSpeakerAttributor(delegate, mode),
    )
    host = tmp_path / "host.wav"
    guest = tmp_path / "guest.wav"
    host.write_bytes(b"host")
    guest.write_bytes(b"guest")

    with pytest.raises(GenerationIsolationError, match=message):
        module.create(
            CreateRequest(
                episode_id=f"episode-malicious-{mode}",
                source_audio=source,
                speaker_tracks=(
                    SpeakerTrackInput(host, "HOST"),
                    SpeakerTrackInput(guest, "GUEST"),
                ),
            )
        )
    assert not tuple(module.store.audio_staging_dir.iterdir())


def test_persisted_speaker_label_swap_is_rejected_on_restart(tmp_path: Path) -> None:
    attributor = MicEnergySpeakerAttributor(
        envelope_loader=lambda _paths, _reference: object(),
        speaker_assigner=lambda _words, _envelopes: [0, 1],
        algorithm_config={"algorithm": "fixture", "version": 1},
    )
    module, source = _module(tmp_path, speaker_attributor=attributor)
    host = tmp_path / "host.wav"
    guest = tmp_path / "guest.wav"
    host.write_bytes(b"host")
    guest.write_bytes(b"guest")
    created = module.create(
        CreateRequest(
            episode_id="episode-label-swap",
            source_audio=source,
            speaker_tracks=(
                SpeakerTrackInput(host, "HOST"),
                SpeakerTrackInput(guest, "GUEST"),
            ),
        )
    )
    assert not isinstance(created, Interrupted)
    provenance_path = (
        module.store.generation_dir(created.generation_id) / "speaker_attribution_provenance.json"
    )
    payload = json.loads(provenance_path.read_text(encoding="utf-8"))
    payload["tracks"][0]["speaker_label"], payload["tracks"][1]["speaker_label"] = (
        payload["tracks"][1]["speaker_label"],
        payload["tracks"][0]["speaker_label"],
    )
    provenance_path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )

    with pytest.raises(ArtifactHashMismatchError, match="artifact hash mismatch"):
        module._load_generation(created.generation_id, require_active=True)


def test_constructor_rejects_declared_speaker_adapter_identity_mismatch(
    tmp_path: Path,
) -> None:
    attributor = MicEnergySpeakerAttributor(
        envelope_loader=lambda _paths, _reference: object(),
        speaker_assigner=lambda _words, _envelopes: [0, 1],
        algorithm_config={"algorithm": "fixture", "version": 1},
    )
    module, _ = _module(tmp_path / "base")

    with pytest.raises(ValueError, match="identity differs"):
        PodcastSubtitleV2(
            tmp_path / "mismatch",
            normalizer=module._normalizer,
            recognizers=module._recognizers,
            semantic_analyzer=module._semantic_analyzer,
            corrector=module._corrector,
            audio_auditor=module._audio_auditor,
            corrector_identity=module._corrector_identity,
            semantic_analyzer_identity=module._semantic_analyzer_identity,
            speaker_attributor=attributor,
            speaker_attributor_identity=_adapter_identity("declared-lie"),
        )


def test_projection_rejects_coordinated_artifact_and_record_rewrite(tmp_path: Path) -> None:
    module, source = _module(tmp_path)
    created = module.create(CreateRequest(episode_id="episode-anji", source_audio=source))
    assert isinstance(created, NeedsReview)
    accepted = module.resolve(
        ResolveRequest(
            created.generation_id,
            (_decision_for_created(created, event_id="accept-for-projection"),),
        )
    )
    assert isinstance(accepted, AcceptedGeneration)
    verified = module.project(ProjectRequest(accepted.generation_id, HORIZONTAL_16X9))
    assert not isinstance(verified, Interrupted)

    directory = module.store.root / "projections" / verified.projection_id
    srt_path = directory / "transcript.srt"
    malicious = srt_path.read_bytes() + b"\nforged subtitle\n"
    srt_path.write_bytes(malicious)
    record_path = directory / "projection_record.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["artifact_hashes"]["transcript.srt"] = _sha(malicious)
    record_without_hash = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = hash_object(record_without_hash)
    record_path.write_bytes(canonical_json_bytes(record))

    with pytest.raises(GenerationIsolationError, match="not reproducible"):
        module._verify_projection_directory(
            verified.projection_id,
            expected_generation_id=accepted.generation_id,
        )


def test_projection_rejects_semantic_execution_byte_tamper_and_identity_drift(
    tmp_path: Path,
) -> None:
    module, source = _module(tmp_path)
    created = module.create(CreateRequest(episode_id="episode-semantic-proof", source_audio=source))
    assert isinstance(created, NeedsReview)
    accepted = module.resolve(
        ResolveRequest(
            created.generation_id,
            (_decision_for_created(created, event_id="accept-semantic-proof"),),
        )
    )
    assert isinstance(accepted, AcceptedGeneration)
    verified = module.project(ProjectRequest(accepted.generation_id, HORIZONTAL_16X9))
    assert not isinstance(verified, Interrupted)

    class _DriftedSemanticAnalyzer(_DynamicSemanticAnalyzer):
        @property
        def identity(self):
            return replace(
                _INLINE_SEMANTIC_IDENTITY,
                config_hash=hash_object({"fixture": "semantic-config-drift"}),
            )

    reopened, _ = _module(tmp_path, semantic_analyzer=_DriftedSemanticAnalyzer())
    with pytest.raises(GenerationIsolationError, match="executable identity drifted"):
        reopened._verify_projection_directory(
            verified.projection_id,
            expected_generation_id=accepted.generation_id,
        )

    directory = module.store.root / "projections" / verified.projection_id
    record = json.loads((directory / "projection_record.json").read_text(encoding="utf-8"))
    request_name = next(
        name for name in record["artifact_hashes"] if name.startswith("semantic/requests/")
    )
    request_path = directory / request_name
    request_path.write_bytes(request_path.read_bytes() + b" ")
    with pytest.raises(GenerationIsolationError, match="artifact hash mismatch"):
        module._verify_projection_directory(
            verified.projection_id,
            expected_generation_id=accepted.generation_id,
        )


@pytest.mark.parametrize(
    "tamper_target",
    (
        "policy_hash",
        "protected_range_set_hash",
        "reference_provenance_hash",
        "rule_set_hash",
        "display_unicode_version",
    ),
)
def test_projection_rejects_coordinated_boundary_receipt_rewrite(
    tmp_path: Path,
    tamper_target: str,
) -> None:
    module, source = _module(tmp_path)
    created = module.create(
        CreateRequest(
            episode_id=f"episode-boundary-receipt-{tamper_target}",
            source_audio=source,
        )
    )
    assert isinstance(created, NeedsReview)
    accepted = module.resolve(
        ResolveRequest(
            created.generation_id,
            (_decision_for_created(created, event_id=f"accept-{tamper_target}"),),
        )
    )
    assert isinstance(accepted, AcceptedGeneration)
    verified = module.project(ProjectRequest(accepted.generation_id, HORIZONTAL_16X9))
    assert not isinstance(verified, Interrupted)

    directory = module.store.root / "projections" / verified.projection_id
    boundary_path = directory / "boundary_constraints.json"
    payload = json.loads(boundary_path.read_text(encoding="utf-8"))
    if tamper_target == "display_unicode_version":
        payload["display_metrics"]["unicode_version"] = "hostile-unicode-version"
    else:
        payload[tamper_target] = "0" * 64
    payload["content_hash"] = hash_object(
        {key: value for key, value in payload.items() if key != "content_hash"}
    )
    forged = BoundaryConstraintReceipt.model_validate(payload)
    boundary_bytes = canonical_json_bytes(forged)
    boundary_path.write_bytes(boundary_bytes)

    record_path = directory / "projection_record.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["artifact_hashes"]["boundary_constraints.json"] = sha256_bytes(boundary_bytes)
    record["boundary_constraints_hash"] = hash_object(forged)
    record["record_hash"] = hash_object(
        {key: value for key, value in record.items() if key != "record_hash"}
    )
    record_path.write_bytes(canonical_json_bytes(record))

    with pytest.raises(
        GenerationIsolationError,
        match="Boundary Constraint Receipt is not reproducible",
    ):
        module._verify_projection_directory(
            verified.projection_id,
            expected_generation_id=accepted.generation_id,
        )


def test_module_rejects_duplicate_semantic_token_ranges(tmp_path: Path) -> None:
    module, source = _module(tmp_path)
    created = module.create(
        CreateRequest(episode_id="episode-semantic-duplicate-range", source_audio=source)
    )
    assert isinstance(created, NeedsReview)
    token_ids = tuple(token.id for token in created.transcript.tokens)
    units = tuple(
        SemanticUnit(
            id=f"token-{index}",
            token_ids=(token_id,),
            kind="token",
            strength=1.0,
        )
        for index, token_id in enumerate(token_ids)
    ) + (
        SemanticUnit(
            id="duplicate-first-token",
            token_ids=(token_ids[0],),
            kind="token",
            strength=0.5,
        ),
    )

    with pytest.raises(ModuleInvariantError, match="duplicate Semantic Unit token range"):
        _validate_semantic_partition(created.transcript, units)


@pytest.mark.parametrize(
    ("unit_ranges", "strength", "message"),
    (
        (((0, 1), (1, 2), (2, 3), (3, 4), (4, 5), (5, 6)), 0.5, "singleton-only"),
        (((0, 2), (2, 4), (4, 6)), 0.0, "all-neutral"),
        (((0, 6),), 0.9, "whole-transcript"),
    ),
)
def test_module_rejects_semantically_degenerate_long_runs(
    tmp_path: Path,
    unit_ranges: tuple[tuple[int, int], ...],
    strength: float,
    message: str,
) -> None:
    module, source = _single_stream_module(
        tmp_path,
        texts=("今天", "我們", "一起", "討論", "字幕", "品質"),
        corrector=FixtureCorrectorAdapter(),
    )
    created = module.create(
        CreateRequest(episode_id=f"episode-degenerate-{message}", source_audio=source)
    )
    assert not isinstance(created, Interrupted)
    token_ids = tuple(token.id for token in created.transcript.tokens)
    units = tuple(
        SemanticUnit(
            id=f"unit-{index}",
            token_ids=token_ids[start:end],
            kind="phrase" if end - start > 1 else "token",
            strength=strength,
        )
        for index, (start, end) in enumerate(unit_ranges)
    )

    with pytest.raises(ModuleInvariantError, match=message):
        _validate_semantic_partition(created.transcript, units)


def test_module_requires_names_and_terms_to_remain_in_one_cue(tmp_path: Path) -> None:
    module, source = _module(tmp_path)
    created = module.create(
        CreateRequest(episode_id="episode-semantic-name-cue", source_audio=source)
    )
    assert isinstance(created, NeedsReview)
    token_ids = tuple(token.id for token in created.transcript.tokens)
    units = (
        SemanticUnit(
            id="unsafe-name",
            token_ids=(token_ids[0],),
            kind="name",
            strength=1.0,
            forbid_cue_breaks=False,
        ),
    ) + tuple(
        SemanticUnit(
            id=f"token-{index}",
            token_ids=(token_id,),
            kind="token",
            strength=1.0,
        )
        for index, token_id in enumerate(token_ids[1:], start=1)
    )

    with pytest.raises(ModuleInvariantError, match="must keep a name in one cue"):
        _validate_semantic_partition(created.transcript, units)


def test_module_rejects_unbounded_semantic_overlap(tmp_path: Path) -> None:
    module, source = _single_stream_module(
        tmp_path,
        texts=tuple("abcdefghij"),
        corrector=FixtureCorrectorAdapter(),
    )
    created = module.create(
        CreateRequest(episode_id="episode-semantic-overlap-budget", source_audio=source)
    )
    assert isinstance(created, (AcceptedGeneration, NeedsReview))
    token_ids = tuple(token.id for token in created.transcript.tokens)
    assert len(token_ids) == 10
    ranges = (
        (0, 6),
        (0, 7),
        (0, 8),
        (0, 9),
        (0, 10),
        (1, 6),
        (1, 7),
        (1, 8),
        (1, 9),
    )
    units = tuple(
        SemanticUnit(
            id=f"overlap-{index}",
            token_ids=token_ids[start:end],
            kind="phrase",
            strength=1.0,
        )
        for index, (start, end) in enumerate(ranges)
    )

    with pytest.raises(ModuleInvariantError, match="bounded overlap budget"):
        _validate_semantic_partition(created.transcript, units)


def test_machine_local_paths_are_removed_from_generation_identity(tmp_path: Path) -> None:
    first, first_source = _module(tmp_path / "machine-a")
    second, second_source = _module(tmp_path / "machine-b")

    first_result = first.create(CreateRequest(episode_id="episode-anji", source_audio=first_source))
    second_result = second.create(
        CreateRequest(episode_id="episode-anji", source_audio=second_source)
    )

    assert first_result.generation_id == second_result.generation_id
    first_record = first.store.load(first_result.generation_id)
    second_record = second.store.load(second_result.generation_id)
    assert first_record.artifact_set_hash == second_record.artifact_set_hash
    assert first_record.artifact_hashes == second_record.artifact_hashes
    stored_receipt = first.store.read_artifact(
        first_result.generation_id,
        "normalization_receipt.json",
    )
    stored_evidence = first.store.read_artifact(
        first_result.generation_id,
        "recognition_evidence.json",
    )
    assert b"file:" not in stored_receipt
    assert b"file:" not in stored_evidence
    assert b"generation-artifact://raw_evidence/" in stored_evidence


def test_caller_cannot_override_corrector_executable_identity(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="differs from the Adapter executable identity"):
        _module(
            tmp_path,
            corrector_identity=_adapter_identity("caller-invented-corrector"),
        )


def test_full_audit_windows_cover_every_no_risk_span_once(tmp_path: Path) -> None:
    corrector = _RecordingCorrector()
    module, source = _single_stream_module(
        tmp_path,
        texts=("甲", "乙", "丙", "丁", "戊", "己", "庚", "辛"),
        corrector=corrector,
    )
    policy = SubtitlePolicy(
        full_audit_max_spans_per_request=3,
        full_audit_max_tokens_per_request=3,
    )

    result = module.create(
        CreateRequest(
            episode_id="episode-windowed",
            source_audio=source,
            policy=policy,
        )
    )

    assert not isinstance(result, Interrupted)
    full_audit = [request for request in corrector.requests if request.mode == "full_audit"]
    flattened = tuple(span_id for request in full_audit for span_id in request.target_span_ids)
    assert flattened == tuple(span.id for span in result.transcript.spans)
    assert len(flattened) == len(set(flattened))
    assert all(len(request.target_span_ids) <= 3 for request in full_audit)
    assert flattened[0] == result.transcript.spans[0].id
    assert flattened[-1] == result.transcript.spans[-1].id
    audio_requests = module._audio_auditor.requests
    audio_flattened = tuple(
        span_id for request in audio_requests for span_id in request.target_span_ids
    )
    assert audio_flattened == tuple(span.id for span in result.transcript.spans)
    assert all(
        request.normalized_audio.name == request.expected_normalized_audio_hash
        for request in audio_requests
    )


def test_empty_text_corrector_cannot_accept_without_successful_audio_audit(
    tmp_path: Path,
) -> None:
    auditor = FixtureAudioAuditorAdapter(status="unresolved", confidence=None)
    module, source = _single_stream_module(
        tmp_path,
        texts=("\u9ad8\u4fe1\u5fc3\u4f46\u4ecd\u9700\u807d\u97f3",),
        corrector=FixtureCorrectorAdapter(),
        audio_auditor=auditor,
    )

    created = module.create(CreateRequest(episode_id="episode-audio-required", source_audio=source))

    assert isinstance(created, NeedsReview)
    assert "audio_audit_unresolved" in {issue.code for issue in created.issues}
    assert len(auditor.requests) == 1


def test_overlapping_corrector_proposals_fail_loud(tmp_path: Path) -> None:
    module, source = _single_stream_module(
        tmp_path,
        texts=("完整", "句子"),
        corrector=_ConflictingCorrector(),
    )

    with pytest.raises(ModuleInvariantError, match="overlapping Correction Proposals"):
        module.create(CreateRequest(episode_id="episode-conflict", source_audio=source))


def test_pending_correction_has_no_active_generation_and_resumes(tmp_path: Path) -> None:
    corrector = _PendingCorrector(tmp_path / "work")
    module, source = _single_stream_module(
        tmp_path,
        texts=("完整", "句子"),
        corrector=corrector,
    )

    pending = module.create(CreateRequest(episode_id="episode-pending", source_audio=source))
    assert isinstance(pending, Interrupted)
    assert pending.operation == "create"
    assert pending.packet_paths and pending.response_paths
    with pytest.raises(GenerationNotFoundError, match="no active"):
        module.store.active_generation_id()

    corrector.ready = True
    completed = module.create(CreateRequest(episode_id="episode-pending", source_audio=source))
    assert not isinstance(completed, Interrupted)
    assert module.store.active_generation_id() == completed.generation_id


def test_pending_correction_resume_does_not_repeat_paid_prefix_stages(
    tmp_path: Path,
) -> None:
    corrector = _PendingCorrector(tmp_path / "work")
    module, source = _single_stream_module(
        tmp_path,
        texts=("完整", "句子"),
        corrector=corrector,
    )
    normalizer = _CountingNormalizer(module._normalizer)
    recognizer = _CountingRecognizer(module._recognizers[0])
    coverage = module._speech_coverage_analyzer
    assert coverage is not None
    coverage_calls = 0
    original_analyze = coverage.analyze

    def counted_analyze(request):
        nonlocal coverage_calls
        coverage_calls += 1
        return original_analyze(request)

    coverage.analyze = counted_analyze
    module._normalizer = normalizer
    module._recognizers = (recognizer,)

    first = module.create(
        CreateRequest(episode_id="episode-prefix-checkpoint", source_audio=source)
    )
    assert isinstance(first, Interrupted)
    assert (normalizer.calls, recognizer.calls, coverage_calls) == (1, 1, 1)

    corrector.ready = True
    resumed = module.create(
        CreateRequest(episode_id="episode-prefix-checkpoint", source_audio=source)
    )

    assert not isinstance(resumed, Interrupted)
    assert (normalizer.calls, recognizer.calls, coverage_calls) == (1, 1, 1)


def test_pending_correction_resumes_in_fresh_process_without_paid_prefix(
    tmp_path: Path,
) -> None:
    first_corrector = _PendingCorrector(tmp_path / "work")
    first, source = _single_stream_module(
        tmp_path,
        texts=("完整", "句子"),
        corrector=first_corrector,
    )
    first_normalizer = _CountingNormalizer(first._normalizer)
    first_recognizer = _CountingRecognizer(first._recognizers[0])
    first._normalizer = first_normalizer
    first._recognizers = (first_recognizer,)
    assert isinstance(
        first.create(CreateRequest(episode_id="episode-fresh-checkpoint", source_audio=source)),
        Interrupted,
    )
    assert (first_normalizer.calls, first_recognizer.calls) == (1, 1)

    resumed_corrector = _PendingCorrector(tmp_path / "work")
    resumed_corrector.ready = True
    fresh, fresh_source = _single_stream_module(
        tmp_path,
        texts=("完整", "句子"),
        corrector=resumed_corrector,
    )
    normalizer = _CountingNormalizer(fresh._normalizer)
    recognizer = _CountingRecognizer(fresh._recognizers[0])
    fresh._normalizer = normalizer
    fresh._recognizers = (recognizer,)

    completed = fresh.create(
        CreateRequest(
            episode_id="episode-fresh-checkpoint",
            source_audio=fresh_source,
        )
    )

    assert not isinstance(completed, Interrupted)
    assert (normalizer.calls, recognizer.calls) == (0, 0)


def test_pending_audio_audit_resume_does_not_repeat_paid_prefix_stages(
    tmp_path: Path,
) -> None:
    auditor = _PendingAudioAuditor(FixtureAudioAuditorAdapter(), tmp_path / "audio-work")
    module, source = _single_stream_module(
        tmp_path,
        texts=("完整", "句子"),
        corrector=FixtureCorrectorAdapter(),
        audio_auditor=auditor,
    )
    corrector = _CountingCorrector(module._corrector)
    normalizer = _CountingNormalizer(module._normalizer)
    recognizer = _CountingRecognizer(module._recognizers[0])
    coverage = module._speech_coverage_analyzer
    assert coverage is not None
    coverage_calls = 0
    original_analyze = coverage.analyze

    def counted_analyze(request):
        nonlocal coverage_calls
        coverage_calls += 1
        return original_analyze(request)

    coverage.analyze = counted_analyze
    module._normalizer = normalizer
    module._recognizers = (recognizer,)
    module._corrector = corrector

    first = module.create(CreateRequest(episode_id="episode-audio-checkpoint", source_audio=source))
    assert isinstance(first, Interrupted)
    assert (normalizer.calls, recognizer.calls, coverage_calls, corrector.calls) == (
        1,
        1,
        1,
        1,
    )

    auditor.ready = True
    resumed = module.create(
        CreateRequest(episode_id="episode-audio-checkpoint", source_audio=source)
    )

    assert not isinstance(resumed, Interrupted)
    assert (normalizer.calls, recognizer.calls, coverage_calls, corrector.calls) == (
        1,
        1,
        1,
        1,
    )


def test_pending_arbiter_resume_does_not_repeat_corrector_or_audio_audit(
    tmp_path: Path,
) -> None:
    proposal_corrector = _LastSpanProposalCorrector()
    arbiter = _PendingArbiter(
        FixtureArbiterAdapter(
            {
                "proposal-last-span": ArbitrationVerdict(
                    proposal_id="proposal-last-span",
                    status="accepted",
                    selected_text="正確候選",
                    confidence=0.99,
                    rationale="fixture heard audio",
                )
            }
        ),
        tmp_path / "arbiter-work",
    )
    auditor = FixtureAudioAuditorAdapter()
    module, source = _single_stream_module(
        tmp_path,
        texts=("原始", "文字"),
        corrector=proposal_corrector,
        audio_auditor=auditor,
        arbiter=arbiter,
    )
    corrector = _CountingCorrector(module._corrector)
    module._corrector = corrector

    first = module.create(
        CreateRequest(episode_id="episode-arbiter-checkpoint", source_audio=source)
    )
    assert isinstance(first, Interrupted)
    assert (corrector.calls, len(auditor.requests)) == (1, 1)

    arbiter.ready = True
    resumed = module.create(
        CreateRequest(episode_id="episode-arbiter-checkpoint", source_audio=source)
    )

    assert not isinstance(resumed, Interrupted)
    assert (corrector.calls, len(auditor.requests)) == (1, 1)


def test_create_checkpoint_rejects_source_and_runtime_identity_drift(
    tmp_path: Path,
) -> None:
    corrector = _PendingCorrector(tmp_path / "work")
    module, source = _single_stream_module(
        tmp_path,
        texts=("完整", "句子"),
        corrector=corrector,
    )
    pending = module.create(
        CreateRequest(episode_id="episode-checkpoint-drift", source_audio=source)
    )
    assert isinstance(pending, Interrupted)

    source.write_bytes(b"changed-source-audio")
    with pytest.raises(GenerationIsolationError, match="different source"):
        module.create(CreateRequest(episode_id="episode-checkpoint-drift", source_audio=source))

    source.write_bytes(b"source-audio")
    module._corrector_identity = replace(
        module._corrector_identity,
        config_hash=hash_object({"fixture": "drifted"}),
    )
    with pytest.raises(GenerationIsolationError, match="different source"):
        module.create(CreateRequest(episode_id="episode-checkpoint-drift", source_audio=source))


def test_create_checkpoint_rejects_microphone_track_drift_before_normalization(
    tmp_path: Path,
) -> None:
    attributor = MicEnergySpeakerAttributor(
        envelope_loader=lambda _paths, _reference: object(),
        speaker_assigner=lambda _words, _envelopes: [0, 1],
        algorithm_config={"algorithm": "fixture", "version": 1},
    )
    corrector = _PendingCorrector(tmp_path / "work")
    module, source = _module(
        tmp_path,
        corrector=corrector,
        speaker_attributor=attributor,
    )
    normalizer = _CountingNormalizer(module._normalizer)
    module._normalizer = normalizer
    host = tmp_path / "host.wav"
    guest = tmp_path / "guest.wav"
    host.write_bytes(b"host-track")
    guest.write_bytes(b"guest-track")
    request = CreateRequest(
        episode_id="episode-mic-checkpoint",
        source_audio=source,
        speaker_tracks=(
            SpeakerTrackInput(host, "HOST"),
            SpeakerTrackInput(guest, "GUEST"),
        ),
    )

    assert isinstance(module.create(request), Interrupted)
    assert normalizer.calls == 1
    guest.write_bytes(b"changed-guest-track")

    with pytest.raises(GenerationIsolationError, match="different source"):
        module.create(request)
    assert normalizer.calls == 1


def test_create_checkpoint_rejects_module_code_identity_drift(tmp_path: Path) -> None:
    corrector = _PendingCorrector(tmp_path / "work")
    module, source = _single_stream_module(
        tmp_path,
        texts=("完整", "句子"),
        corrector=corrector,
    )
    assert isinstance(
        module.create(CreateRequest(episode_id="episode-code-checkpoint", source_audio=source)),
        Interrupted,
    )
    module._code_hash = hash_object({"implementation": "changed"})

    with pytest.raises(GenerationIsolationError, match="different source"):
        module.create(CreateRequest(episode_id="episode-code-checkpoint", source_audio=source))


def test_create_checkpoint_artifact_tamper_blocks_resume_before_paid_prefix(
    tmp_path: Path,
) -> None:
    corrector = _PendingCorrector(tmp_path / "work")
    module, source = _single_stream_module(
        tmp_path,
        texts=("完整", "句子"),
        corrector=corrector,
    )
    normalizer = _CountingNormalizer(module._normalizer)
    module._normalizer = normalizer
    pending = module.create(
        CreateRequest(episode_id="episode-checkpoint-tamper", source_audio=source)
    )
    assert isinstance(pending, Interrupted)
    stored = module.store.load_create_checkpoint(
        expected_operation_key=json.loads(
            module.store.create_checkpoint_pointer_path.read_text(encoding="utf-8")
        )["operation_key"]
    )
    assert stored is not None
    (stored.directory / "recognition_evidence.json").write_bytes(b"[]")

    corrector.ready = True
    with pytest.raises(ArtifactHashMismatchError, match="artifact hash mismatch"):
        module.create(CreateRequest(episode_id="episode-checkpoint-tamper", source_audio=source))
    assert normalizer.calls == 1


def test_unknown_speaker_and_pending_semantics_cannot_publish(tmp_path: Path) -> None:
    no_speaker, source = _single_stream_module(
        tmp_path / "no-speaker",
        texts=("沒有", "說話者"),
        corrector=_RecordingCorrector(),
        speaker=None,
    )
    unresolved = no_speaker.create(
        CreateRequest(episode_id="episode-no-speaker", source_audio=source)
    )
    assert isinstance(unresolved, NeedsReview)
    with pytest.raises(ModuleInvariantError, match="accepted active"):
        no_speaker.project(ProjectRequest(unresolved.generation_id, HORIZONTAL_16X9))

    semantic = _PendingSemanticAnalyzer(tmp_path / "semantic-work")
    module, source = _module(tmp_path / "semantic", semantic_analyzer=semantic)
    created = module.create(CreateRequest(episode_id="episode-anji", source_audio=source))
    assert isinstance(created, NeedsReview)
    decision = _decision_for_created(created, event_id="semantic-ready")
    accepted = module.resolve(ResolveRequest(created.generation_id, (decision,)))
    assert isinstance(accepted, AcceptedGeneration)

    pending = module.project(ProjectRequest(accepted.generation_id, HORIZONTAL_16X9))
    assert isinstance(pending, Interrupted)
    projections_root = module.store.root / "projections"
    assert not projections_root.exists() or not tuple(projections_root.iterdir())
    semantic.ready = True
    assert not isinstance(
        module.project(ProjectRequest(accepted.generation_id, HORIZONTAL_16X9)),
        Interrupted,
    )


def test_decision_must_bind_exact_stored_proposal_and_span(tmp_path: Path) -> None:
    corrector = _LastSpanProposalCorrector()
    module, source = _single_stream_module(
        tmp_path,
        texts=("原始", "文字"),
        corrector=corrector,
    )
    created = module.create(CreateRequest(episode_id="episode-proposal", source_audio=source))
    assert isinstance(created, NeedsReview)
    assert corrector.proposal is not None
    proposal = corrector.proposal
    all_spans = tuple(span.id for span in created.transcript.spans)
    all_tokens = created.transcript.tokens
    issue_ids = tuple(
        issue.id for issue in created.transcript.review_issues if issue.status == "unresolved"
    )

    fake = CorrectionDecision(
        event_id="fake-proposal",
        episode_id=created.transcript.episode_id,
        generation_id=created.generation_id,
        target_span_ids=all_spans,
        target_start_ms=all_tokens[0].start_ms,
        target_end_ms=all_tokens[-1].end_ms,
        evidence_fingerprint=review_target_fingerprint(created.transcript, all_spans),
        issue_ids=issue_ids,
        proposal_ids=("proposal-does-not-exist",),
        audio_evidence_ids=tuple(
            sorted({item for token in all_tokens for item in token.evidence_ids})
        ),
        action="replace",
        replacement_text="任意改字",
        replacement_lexemes=("任意", "改字"),
        actor_kind="human",
        actor="reviewer",
        rationale="hostile fixture",
        timestamp=datetime(2026, 8, 12, tzinfo=timezone.utc),
    )
    with pytest.raises(GenerationIsolationError, match="unknown Proposals"):
        module.resolve(ResolveRequest(created.generation_id, (fake,)))

    wrong_candidate = fake.model_copy(
        update={
            "event_id": "wrong-candidate",
            "proposal_ids": (proposal.id,),
            "action": "accept_candidate",
            "replacement_text": None,
            "selected_candidate": "錯誤候選",
            "replacement_lexemes": ("錯誤候選",),
        }
    )
    with pytest.raises(
        (GenerationIsolationError, ModuleInvariantError),
        match="audio verdict|different Audio Spans|exactly select",
    ):
        module.resolve(ResolveRequest(created.generation_id, (wrong_candidate,)))


def test_audio_audit_replay_runs_before_commit_and_in_a_fresh_adapter_process(
    tmp_path: Path,
) -> None:
    auditor = _ReplayCountingAudioAuditor(FixtureAudioAuditorAdapter())
    module, source = _single_stream_module(
        tmp_path,
        texts=("完整", "字幕"),
        corrector=FixtureCorrectorAdapter(),
        audio_auditor=auditor,
    )
    created = module.create(
        CreateRequest(episode_id="episode-audio-replay-boundary", source_audio=source)
    )
    assert not isinstance(created, Interrupted)
    assert auditor.audit_calls > 0
    # The audio checkpoint and final Generation commit independently replay
    # each proof before publishing durable state.
    assert auditor.replay_calls >= auditor.audit_calls * 2

    fresh_auditor = _ReplayCountingAudioAuditor(FixtureAudioAuditorAdapter())
    fresh, _ = _single_stream_module(
        tmp_path,
        texts=("完整", "字幕"),
        corrector=FixtureCorrectorAdapter(),
        audio_auditor=fresh_auditor,
    )
    assert fresh.store.active_generation_id() == created.generation_id
    assert fresh_auditor.audit_calls == 0
    assert fresh_auditor.replay_calls == 0


def test_fresh_checkpoint_recovery_replays_audio_proof_without_reauditing(
    tmp_path: Path,
) -> None:
    verdict = ArbitrationVerdict(
        proposal_id="proposal-last-span",
        status="accepted",
        selected_text="正確候選",
        confidence=0.99,
        rationale="fixture heard exact audio",
    )
    first_arbiter = _PendingArbiter(
        FixtureArbiterAdapter({"proposal-last-span": verdict}),
        tmp_path / "arbiter-work",
    )
    first_auditor = _ReplayCountingAudioAuditor(FixtureAudioAuditorAdapter())
    first, source = _single_stream_module(
        tmp_path,
        texts=("原始", "文字"),
        corrector=_LastSpanProposalCorrector(),
        audio_auditor=first_auditor,
        arbiter=first_arbiter,
    )
    pending = first.create(
        CreateRequest(episode_id="episode-audio-replay-recovery", source_audio=source)
    )
    assert isinstance(pending, Interrupted)
    assert (first_auditor.audit_calls, first_auditor.replay_calls) == (1, 1)

    fresh_arbiter = _PendingArbiter(
        FixtureArbiterAdapter({"proposal-last-span": verdict}),
        tmp_path / "arbiter-work",
    )
    fresh_arbiter.ready = True
    fresh_auditor = _ReplayCountingAudioAuditor(FixtureAudioAuditorAdapter())
    fresh, fresh_source = _single_stream_module(
        tmp_path,
        texts=("原始", "文字"),
        corrector=_LastSpanProposalCorrector(),
        audio_auditor=fresh_auditor,
        arbiter=fresh_arbiter,
    )
    completed = fresh.create(
        CreateRequest(
            episode_id="episode-audio-replay-recovery",
            source_audio=fresh_source,
        )
    )
    assert not isinstance(completed, Interrupted)
    assert fresh_auditor.audit_calls == 0
    assert fresh_auditor.replay_calls >= 2


@pytest.mark.parametrize("mode", ("clip", "request", "response"))
def test_fresh_load_rejects_coordinated_audio_audit_proof_rewrite(
    tmp_path: Path,
    mode: str,
) -> None:
    module, source = _single_stream_module(
        tmp_path,
        texts=("不可竄改",),
        corrector=FixtureCorrectorAdapter(),
    )
    created = module.create(
        CreateRequest(episode_id=f"episode-audio-proof-{mode}", source_audio=source)
    )
    assert not isinstance(created, Interrupted)
    _coordinated_audio_audit_tamper(module, created.generation_id, mode=mode)

    with pytest.raises(
        GenerationIsolationError,
        match="Audio Audit proof is not reproducible|complete checkpoint Generation binding",
    ):
        _single_stream_module(
            tmp_path,
            texts=("不可竄改",),
            corrector=FixtureCorrectorAdapter(),
        )


def test_arbitration_replay_runs_before_commit_and_rejects_semantic_rewrite(
    tmp_path: Path,
) -> None:
    verdict = ArbitrationVerdict(
        proposal_id="proposal-last-span",
        status="accepted",
        selected_text="正確候選",
        confidence=0.99,
        rationale="fixture heard exact audio",
    )
    arbiter = _ReplayCountingArbiter(FixtureArbiterAdapter({"proposal-last-span": verdict}))
    module, source = _single_stream_module(
        tmp_path,
        texts=("原始", "文字"),
        corrector=_LastSpanProposalCorrector(),
        arbiter=arbiter,
    )
    created = module.create(
        CreateRequest(episode_id="episode-arbitration-replay", source_audio=source)
    )
    assert not isinstance(created, Interrupted)
    assert arbiter.decide_calls == 1
    assert arbiter.replay_calls == 1

    _coordinated_arbitration_response_tamper(module, created.generation_id)
    fresh_arbiter = _ReplayCountingArbiter(FixtureArbiterAdapter({"proposal-last-span": verdict}))
    with pytest.raises(
        GenerationIsolationError,
        match="Arbitration proof is not reproducible|complete checkpoint Generation binding",
    ):
        _single_stream_module(
            tmp_path,
            texts=("原始", "文字"),
            corrector=_LastSpanProposalCorrector(),
            arbiter=fresh_arbiter,
        )
    assert fresh_arbiter.decide_calls == 0
    assert fresh_arbiter.replay_calls == 0


def test_unresolved_low_risk_is_accepted_when_policy_permits_it(
    tmp_path: Path,
) -> None:
    module, source = _single_stream_module(
        tmp_path,
        texts=("Podcast",),
        corrector=FixtureCorrectorAdapter(),
    )

    created = module.create(
        CreateRequest(
            episode_id="episode-low-risk-permitted",
            source_audio=source,
            policy=SubtitlePolicy(permit_unresolved_low_risk=True),
        )
    )

    assert isinstance(created, AcceptedGeneration)
    assert created.transcript.acceptance_policy.permit_unresolved_low_risk is True
    low_issues = tuple(
        issue
        for issue in created.transcript.review_issues
        if issue.status == "unresolved" and issue.severity == "low"
    )
    assert {issue.code for issue in low_issues} == {"code_switch"}


def test_low_risk_blocking_policy_survives_reopen_and_resolves_explicitly(
    tmp_path: Path,
) -> None:
    module, source = _single_stream_module(
        tmp_path,
        texts=("Podcast",),
        corrector=FixtureCorrectorAdapter(),
    )
    policy = SubtitlePolicy(permit_unresolved_low_risk=False)
    created = module.create(
        CreateRequest(
            episode_id="episode-low-risk-blocked",
            source_audio=source,
            policy=policy,
        )
    )
    assert isinstance(created, NeedsReview)
    assert created.transcript.status == "draft"
    assert created.transcript.acceptance_policy.permit_unresolved_low_risk is False
    assert {issue.code for issue in created.issues} == {"code_switch"}
    with pytest.raises(ModuleInvariantError, match="accepted active Canonical Transcript"):
        module.project(ProjectRequest(created.generation_id, HORIZONTAL_16X9))

    reopened, _ = _single_stream_module(
        tmp_path,
        texts=("Podcast",),
        corrector=FixtureCorrectorAdapter(),
    )
    loaded = reopened._load_generation(created.generation_id, require_active=True).result
    assert loaded.outcome == "needs_review"
    issue = loaded.transcript.review_issues[0]
    token = loaded.transcript.tokens[0]
    span = loaded.transcript.spans[0]
    decision = CorrectionDecision(
        event_id="confirm-low-risk",
        episode_id=loaded.transcript.episode_id,
        generation_id=loaded.transcript.generation_id,
        target_span_ids=(span.id,),
        target_start_ms=span.start_ms,
        target_end_ms=span.end_ms,
        evidence_fingerprint=review_target_fingerprint(
            loaded.transcript,
            (span.id,),
        ),
        issue_ids=(issue.id,),
        audio_evidence_ids=token.evidence_ids,
        evidence_basis="audio",
        action="confirm_original",
        actor_kind="human",
        actor="reviewer",
        rationale="confirmed exact normalized audio",
        timestamp=datetime(2026, 8, 12, tzinfo=timezone.utc),
    )
    resolved = reopened.resolve(ResolveRequest(created.generation_id, (decision,)))
    assert isinstance(resolved, AcceptedGeneration)
    assert resolved.transcript.acceptance_policy.permit_unresolved_low_risk is False

    fresh, _ = _single_stream_module(
        tmp_path,
        texts=("Podcast",),
        corrector=FixtureCorrectorAdapter(),
    )
    fresh_result = fresh._load_generation(
        resolved.generation_id,
        require_active=True,
    ).result
    assert fresh_result.outcome == "accepted"


def test_fresh_load_rejects_coordinated_acceptance_policy_and_status_tamper(
    tmp_path: Path,
) -> None:
    module, source = _single_stream_module(
        tmp_path,
        texts=("Podcast",),
        corrector=FixtureCorrectorAdapter(),
    )
    created = module.create(
        CreateRequest(
            episode_id="episode-policy-tamper",
            source_audio=source,
            policy=SubtitlePolicy(permit_unresolved_low_risk=False),
        )
    )
    assert isinstance(created, NeedsReview)
    directory = module.store.generation_dir(created.generation_id)
    payload = json.loads((directory / "canonical_transcript.json").read_text(encoding="utf-8"))
    payload["acceptance_policy"]["permit_unresolved_low_risk"] = True
    payload["status"] = "accepted"
    transcript = CanonicalTranscript.model_validate(payload)
    transcript_bytes = canonical_json_bytes(transcript)
    _rewrite_generation_consistently(
        module,
        created.generation_id,
        artifacts={"canonical_transcript.json": transcript_bytes},
        stage_hashes={"canonical_transcript": hash_object(transcript)},
    )

    with pytest.raises(
        GenerationIsolationError,
        match=(
            "Generation identity does not match its sealed policy/content|"
            "complete checkpoint Generation binding"
        ),
    ):
        fresh, _ = _single_stream_module(
            tmp_path,
            texts=("Podcast",),
            corrector=FixtureCorrectorAdapter(),
        )
        fresh._load_generation(created.generation_id, require_active=True)
