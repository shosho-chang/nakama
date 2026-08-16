"""Deterministic fixture Adapters for Interface-level tests and gold corpora."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from shared.schemas.podcast_subtitles_v2 import (
    ArbitrationReceipt,
    ArtifactDigest,
    AudioAuditReceipt,
    AudioClockMap,
    NormalizationReceipt,
    RecognitionEvidence,
    SemanticUnit,
    reference_evidence_set_hash,
)

from ..hashing import canonical_json_bytes, hash_file, hash_object, sha256_bytes
from ..ports import (
    AdapterInputError,
    AdapterIntegrityError,
    ArbitrationRequest,
    ArbitrationRunResult,
    ArbitrationVerdict,
    AudioAuditRequest,
    AudioAuditRunResult,
    AudioModelIdentity,
    CorrectionExecutionReceipt,
    CorrectionModelIdentity,
    CorrectionProposal,
    CorrectionRequest,
    CorrectionRunResult,
    NormalizationResult,
    NormalizeRequest,
    RecognitionModelIdentity,
    RecognitionRequest,
    ReferenceRetrievalRequest,
    ReferenceRetrievalResult,
    SemanticAnalysisRequest,
    SemanticExecutionReceipt,
    SemanticModelIdentity,
    SemanticRunResult,
)


def _fixture_artifact(prefix: str, kind: str, payload: bytes) -> ArtifactDigest:
    digest = sha256_bytes(payload)
    return ArtifactDigest(
        uri=f"generation-artifact://{prefix}/{kind}s/{digest}",
        sha256=digest,
        size_bytes=len(payload),
    )


def _strict_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key is forbidden: {key!r}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number is forbidden: {value}")


def _strict_json_bytes(payload: bytes, *, label: str) -> dict[str, object]:
    if not isinstance(payload, bytes):
        raise AdapterIntegrityError(f"stored fixture {label} is not immutable bytes")
    try:
        decoded = payload.decode("utf-8")
        parsed = json.loads(
            decoded,
            object_pairs_hook=_strict_json_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise AdapterIntegrityError(f"stored fixture {label} is malformed JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise AdapterIntegrityError(f"stored fixture {label} must be a JSON object")
    return parsed


class FixtureNormalizerAdapter:
    """Return a prebuilt normalized artifact only when its receipt still matches bytes."""

    def __init__(self, normalized_audio: str | Path, receipt: NormalizationReceipt) -> None:
        self._normalized_audio = Path(normalized_audio)
        self._receipt = receipt

    def normalize(self, request: NormalizeRequest) -> NormalizationResult:
        source = Path(request.source_audio)
        if not source.is_file() or not self._normalized_audio.is_file():
            raise AdapterInputError("fixture normalization artifacts must exist")
        source_hash = hash_file(source)
        normalized_hash = hash_file(self._normalized_audio)
        if request.expected_source_hash and request.expected_source_hash != source_hash:
            raise AdapterIntegrityError("fixture source hash differs from NormalizeRequest")
        if source_hash != self._receipt.source.sha256:
            raise AdapterIntegrityError("fixture source bytes differ from NormalizationReceipt")
        if normalized_hash != self._receipt.normalized.sha256:
            raise AdapterIntegrityError("fixture normalized bytes differ from NormalizationReceipt")
        return NormalizationResult(
            normalized_audio=self._normalized_audio.resolve(),
            receipt=self._receipt,
        )


class FixtureRecognizerAdapter:
    """Return immutable Evidence only for the exact requested generation and audio."""

    def __init__(self, evidence: RecognitionEvidence) -> None:
        self._evidence = evidence
        runtime_components = (("fixture_runtime", "podcast-subtitle-v2"),)
        self._identity = RecognitionModelIdentity(
            adapter_name=evidence.adapter,
            adapter_version="1",
            model=evidence.model,
            model_version="fixture-pinned",
            aligner="fixture-alignment",
            aligner_version="1",
            runtime_components=runtime_components,
            runtime_hash=hash_object({"runtime_components": runtime_components}),
            adapter_code_hash=hash_file(Path(__file__)),
            config_hash=evidence.config_hash,
            execution_mode="fixture",
        )

    @property
    def identity(self) -> RecognitionModelIdentity:
        return self._identity

    def recognize(self, request: RecognitionRequest) -> RecognitionEvidence:
        if request.episode_id != self._evidence.episode_id:
            raise AdapterIntegrityError("fixture Evidence belongs to another episode")
        if request.invocation_id != self._evidence.invocation_id:
            raise AdapterIntegrityError("fixture Evidence belongs to another invocation")
        audio = Path(request.normalized_audio)
        if not audio.is_file():
            raise AdapterInputError(f"normalized audio is not a file: {audio}")
        actual_hash = hash_file(audio)
        if request.expected_normalized_audio_hash and (
            request.expected_normalized_audio_hash != actual_hash
        ):
            raise AdapterIntegrityError("fixture audio differs from RecognitionRequest")
        if actual_hash != self._evidence.normalized_audio_hash:
            raise AdapterIntegrityError("fixture audio differs from RecognitionEvidence")
        return self._evidence

    def verify(
        self,
        evidence: RecognitionEvidence,
        *,
        request: RecognitionRequest,
        raw_output: bytes,
    ) -> RecognitionEvidence:
        expected = self.recognize(request)
        expected = expected.model_copy(
            update={
                "raw_output": expected.raw_output.model_copy(
                    update={"uri": evidence.raw_output.uri}
                )
            }
        )
        if evidence != expected:
            raise AdapterIntegrityError("fixture Recognition Evidence does not replay")
        if (
            sha256_bytes(raw_output) != evidence.raw_output.sha256
            or len(raw_output) != evidence.raw_output.size_bytes
        ):
            raise AdapterIntegrityError("fixture Recognition raw output digest mismatch")
        return evidence


class FixtureReferenceRetrieverAdapter:
    """Return pre-indexed source passages for a stable audio-span query."""

    def __init__(self, results: Mapping[str, ReferenceRetrievalResult]) -> None:
        self._results = dict(results)

    def retrieve(self, request: ReferenceRetrievalRequest) -> ReferenceRetrievalResult:
        try:
            result = self._results[request.audio_span_id]
        except KeyError as exc:
            raise AdapterInputError(
                f"no fixture Reference Evidence for audio span {request.audio_span_id!r}"
            ) from exc
        if result.episode_id != request.episode_id:
            raise AdapterIntegrityError("fixture retrieval belongs to another episode")
        if result.invocation_id != request.invocation_id:
            raise AdapterIntegrityError("fixture retrieval belongs to another invocation")
        if result.audio_span_id != request.audio_span_id:
            raise AdapterIntegrityError("fixture retrieval belongs to another audio span")
        if result.query != request.observed_text:
            raise AdapterIntegrityError("fixture retrieval query differs from request")
        if result.context != request.context or result.policy != request.policy:
            raise AdapterIntegrityError("fixture retrieval context/policy differs from request")
        if result.candidate_terms != request.candidate_terms:
            raise AdapterIntegrityError("fixture retrieval candidates differ from request")
        if result.allowed_source_ids != tuple(sorted(request.allowed_artifact_ids)):
            raise AdapterIntegrityError("fixture retrieval source scope differs from request")
        if result.max_results != request.max_results:
            raise AdapterIntegrityError("fixture retrieval result limit differs from request")
        if len(result.evidence) > request.max_results:
            raise AdapterIntegrityError("fixture retriever returned more than max_results")
        if request.allowed_artifact_ids:
            disallowed = {
                item.artifact.source_id
                for item in result.evidence
                if item.artifact.source_id not in request.allowed_artifact_ids
            }
            if disallowed:
                raise AdapterIntegrityError(
                    f"fixture retriever returned disallowed artifacts: {sorted(disallowed)!r}"
                )
        return result

    def replay(
        self,
        request: ReferenceRetrievalRequest,
        stored: ReferenceRetrievalResult,
    ) -> ReferenceRetrievalResult:
        current = self.retrieve(request)
        if current != stored:
            raise AdapterIntegrityError("fixture Reference retrieval does not replay")
        return stored


class FixtureCorrectorAdapter:
    """Return reviewed gold proposals after checking their Evidence references."""

    def __init__(self, proposals: tuple[CorrectionProposal, ...] = ()) -> None:
        self._proposals = tuple(proposals)
        self._identity = CorrectionModelIdentity(
            adapter_name="fixture-corrector",
            adapter_version="1",
            model="gold-fixture",
            model_version="1",
            runtime_hash=hash_object({"fixture": "correction-runtime"}),
            adapter_code_hash=hash_file(Path(__file__)),
            config_hash=hash_object({"fixture": "fixture-corrector"}),
            execution_mode="fixture",
        )

    @property
    def identity(self) -> CorrectionModelIdentity:
        return self._identity

    def propose(self, request: CorrectionRequest) -> tuple[CorrectionProposal, ...]:
        if not request.evidence:
            raise AdapterInputError("correction fixture requires recognition Evidence")
        available = {token.id for evidence in request.evidence for token in evidence.tokens}
        available_references = {item.id for item in request.reference_evidence}
        available_spans = set(request.target_span_ids)
        for proposal in self._proposals:
            if proposal.evidence_basis not in {
                "recognition",
                "recognition_and_reference",
                "reference_only",
            }:
                raise AdapterIntegrityError(
                    "fixture text Corrector cannot claim that it heard audio"
                )
            unknown_spans = set(proposal.audio_span_ids) - available_spans
            if unknown_spans:
                raise AdapterIntegrityError(
                    "fixture correction proposal references untargeted Audio Spans: "
                    f"{sorted(unknown_spans)!r}"
                )
            unknown = set(proposal.evidence_token_ids) - available
            if unknown:
                raise AdapterIntegrityError(
                    f"fixture correction proposal references unknown Evidence: {sorted(unknown)!r}"
                )
            unknown_references = set(proposal.reference_evidence_ids) - available_references
            if unknown_references:
                raise AdapterIntegrityError(
                    "fixture correction proposal references unknown Reference Evidence: "
                    f"{sorted(unknown_references)!r}"
                )
        return self._proposals

    def propose_with_receipt(self, request: CorrectionRequest) -> CorrectionRunResult:
        return self._build_run(request)

    def _build_run(self, request: CorrectionRequest) -> CorrectionRunResult:
        """Deterministically rebuild fixture proof without entering an action port."""

        proposals = self.propose(request)
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
            "adapter_identity_hash": self.identity.content_hash,
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
                for proposal in proposals
            ],
        }
        request_bytes = canonical_json_bytes(packet)
        response_bytes = canonical_json_bytes(response)
        return CorrectionRunResult(
            proposals=proposals,
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
                    request=_fixture_artifact("correction", "request", request_bytes),
                    response=_fixture_artifact("correction", "response", response_bytes),
                    adapter_identity=self.identity,
                    proposal_ids=tuple(proposal.id for proposal in proposals),
                ),
            ),
            request_bytes=(request_bytes,),
            response_bytes=(response_bytes,),
        )

    def replay(
        self,
        request: CorrectionRequest,
        *,
        proposals: tuple[CorrectionProposal, ...],
        execution_receipts: tuple[CorrectionExecutionReceipt, ...],
        request_bytes: tuple[bytes, ...],
        response_bytes: tuple[bytes, ...],
    ) -> CorrectionRunResult:
        """Rebuild fixture proof from current inputs without executing new work."""

        try:
            stored = CorrectionRunResult(
                proposals=proposals,
                execution_receipts=execution_receipts,
                request_bytes=request_bytes,
                response_bytes=response_bytes,
            )
        except (TypeError, ValueError) as exc:
            raise AdapterIntegrityError(
                f"stored fixture Corrector proof is invalid: {exc}"
            ) from exc
        if any(receipt.adapter_identity != self.identity for receipt in execution_receipts):
            raise AdapterIntegrityError("fixture Corrector executable identity drifted")
        expected = self._build_run(request)
        if stored != expected:
            raise AdapterIntegrityError("fixture Corrector execution proof does not replay")
        return expected


class FixtureAudioAuditorAdapter:
    """Gold audio review with replayable bytes for Module tests and corpora."""

    def __init__(
        self,
        *,
        status: str = "confirmed",
        confidence: float | None = 0.99,
        proposals: tuple[CorrectionProposal, ...] = (),
        runtime_hash: str | None = None,
    ) -> None:
        self._status = status
        self._confidence = confidence
        self._proposals = tuple(proposals)
        self.requests: list[AudioAuditRequest] = []
        self._identity = AudioModelIdentity(
            adapter_name="fixture-audio-auditor",
            adapter_version="1",
            model="gold-human-listening",
            model_version="1",
            runtime_hash=runtime_hash or hash_object({"fixture": "audio-runtime"}),
            adapter_code_hash=hash_file(Path(__file__)),
            config_hash=hash_object(
                {"fixture": "audio-auditor", "status": status, "confidence": confidence}
            ),
            execution_mode="fixture",
        )

    @property
    def identity(self) -> AudioModelIdentity:
        return self._identity

    def _run(
        self,
        request: AudioAuditRequest,
        *,
        record_request: bool,
    ) -> AudioAuditRunResult:
        if record_request:
            self.requests.append(request)
        audio = request.normalized_audio.resolve()
        if not audio.is_file() or hash_file(audio) != request.expected_normalized_audio_hash:
            raise AdapterIntegrityError("fixture audio audit did not receive bound CAS bytes")
        proposals = tuple(
            proposal
            for proposal in self._proposals
            if set(proposal.audio_span_ids).issubset(request.target_span_ids)
        )
        if self._status != "finding":
            proposals = ()
        if self._status == "finding" and not proposals:
            raise AdapterInputError("fixture finding requires a proposal in this window")
        if any(
            proposal.evidence_basis not in {"audio", "audio_and_reference"}
            for proposal in proposals
        ):
            raise AdapterIntegrityError("fixture Audio Auditor proposals must be audio-backed")
        work_packet = {
            "schema_version": 1,
            "episode_id": request.transcript.episode_id,
            "generation_id": request.transcript.generation_id,
            "canonical_content_hash": request.transcript.content_hash,
            "recognition_evidence_hash": request.transcript.evidence_hash,
            "reference_evidence_hash": request.transcript.reference_evidence_hash,
            "policy_hash": request.transcript.policy_hash,
            "normalized_audio_hash": request.expected_normalized_audio_hash,
            "target_span_ids": list(request.target_span_ids),
            "window": [request.window_start_ms, request.window_end_ms],
            "adapter_identity_hash": self.identity.content_hash,
        }
        work_packet_id = f"fixture-audio-audit-{hash_object(work_packet)}"
        request_bytes = canonical_json_bytes({**work_packet, "work_packet_id": work_packet_id})
        response_bytes = canonical_json_bytes(
            {
                "schema_version": 1,
                "work_packet_id": work_packet_id,
                "verdict": self._status,
                "confidence": self._confidence,
                "rationale": "fixture gold audio review",
                "proposals": [
                    {
                        "id": proposal.id,
                        "audio_span_ids": list(proposal.audio_span_ids),
                        "candidate_text": proposal.candidate_text,
                    }
                    for proposal in proposals
                ],
            }
        )
        clip_bytes = audio.read_bytes()
        request_artifact = _fixture_artifact("audio_audit", "request", request_bytes)
        response_artifact = _fixture_artifact("audio_audit", "response", response_bytes)
        clip_artifact = _fixture_artifact("audio_audit", "clip", clip_bytes)
        status = self._status
        if status not in {"confirmed", "finding", "unresolved"}:
            raise AdapterInputError(f"invalid fixture audio audit status: {status!r}")
        receipt = AudioAuditReceipt(
            id=f"audio-audit-{hash_object((work_packet_id, response_artifact.sha256))}",
            episode_id=request.transcript.episode_id,
            preaudit_generation_id=request.transcript.generation_id,
            preaudit_content_hash=request.transcript.content_hash,
            evidence_hash=request.transcript.evidence_hash,
            reference_evidence_hash=request.transcript.reference_evidence_hash,
            policy_hash=request.transcript.policy_hash,
            normalized_audio_hash=request.expected_normalized_audio_hash,
            target_span_ids=request.target_span_ids,
            window_start_ms=request.window_start_ms,
            window_end_ms=request.window_end_ms,
            clip_start_ms=request.window_start_ms,
            clip_end_ms=request.window_end_ms,
            work_packet_id=work_packet_id,
            request=request_artifact,
            response=response_artifact,
            clip=clip_artifact,
            adapter_name=self.identity.adapter_name,
            adapter_version=self.identity.adapter_version,
            model=self.identity.model,
            model_version=self.identity.model_version,
            runtime_hash=self.identity.runtime_hash,
            adapter_code_hash=self.identity.adapter_code_hash,
            config_hash=self.identity.config_hash,
            adapter_identity_hash=self.identity.content_hash,
            status=status,
            confidence=self._confidence,
            rationale="fixture gold audio review",
            proposal_ids=tuple(proposal.id for proposal in proposals),
            proposal_set_hash=hash_object(proposals),
        )
        return AudioAuditRunResult(
            proposals=proposals,
            receipt=receipt,
            request_bytes=request_bytes,
            response_bytes=response_bytes,
            clip_bytes=clip_bytes,
        )

    def audit(self, request: AudioAuditRequest) -> AudioAuditRunResult:
        return self._run(request, record_request=True)

    def replay(
        self,
        request: AudioAuditRequest,
        *,
        proposals: tuple[CorrectionProposal, ...],
        receipt: AudioAuditReceipt,
        request_bytes: bytes,
        response_bytes: bytes,
        clip_bytes: bytes,
    ) -> AudioAuditRunResult:
        """Recreate the fixture proof from bound inputs without mutable side effects."""

        _strict_json_bytes(response_bytes, label="audio audit response")
        try:
            stored = AudioAuditRunResult(
                proposals=proposals,
                receipt=receipt,
                request_bytes=request_bytes,
                response_bytes=response_bytes,
                clip_bytes=clip_bytes,
            )
        except (TypeError, ValueError) as exc:
            raise AdapterIntegrityError(
                f"stored fixture audio audit proof is invalid: {exc}"
            ) from exc
        expected = self._run(request, record_request=False)
        if stored != expected:
            raise AdapterIntegrityError("stored fixture audio audit result does not replay exactly")
        return expected


class FixtureArbiterAdapter:
    """Resolve configured proposal IDs and fail loud for an unreviewed dispute."""

    def __init__(self, verdicts: Mapping[str, ArbitrationVerdict]) -> None:
        self._verdicts = dict(verdicts)
        self._identity = AudioModelIdentity(
            adapter_name="fixture-audio-arbiter",
            adapter_version="1",
            model="gold-human-listening",
            model_version="1",
            runtime_hash=hash_object({"fixture": "arbiter-runtime"}),
            adapter_code_hash=hash_file(Path(__file__)),
            config_hash=hash_object({"fixture": "arbiter"}),
            execution_mode="fixture",
        )

    @property
    def identity(self) -> AudioModelIdentity:
        return self._identity

    def decide(self, request: ArbitrationRequest) -> ArbitrationVerdict:
        try:
            verdict = self._verdicts[request.proposal.id]
        except KeyError as exc:
            raise AdapterInputError(
                f"no fixture arbitration verdict for proposal {request.proposal.id!r}"
            ) from exc
        if verdict.proposal_id != request.proposal.id:
            raise AdapterIntegrityError("fixture verdict is bound to another proposal")
        available_references = {item.id for item in request.reference_evidence}
        unknown_references = set(request.proposal.reference_evidence_ids) - available_references
        if unknown_references:
            raise AdapterIntegrityError(
                f"fixture arbitration is missing Reference Evidence: {sorted(unknown_references)!r}"
            )
        return verdict

    def _run_with_receipt(self, request: ArbitrationRequest) -> ArbitrationRunResult:
        verdict = self.decide(request)
        audio = request.normalized_audio.resolve()
        if not audio.is_file() or hash_file(audio) != request.expected_normalized_audio_hash:
            raise AdapterIntegrityError("fixture arbitration did not receive bound CAS bytes")
        packet = {
            "schema_version": 1,
            "episode_id": request.episode_id,
            "generation_id": request.generation_id,
            "canonical_content_hash": request.transcript.content_hash,
            "recognition_evidence_hash": request.transcript.evidence_hash,
            "reference_evidence_hash": request.transcript.reference_evidence_hash,
            "policy_hash": request.transcript.policy_hash,
            "normalized_audio_hash": request.expected_normalized_audio_hash,
            "proposal": {
                "id": request.proposal.id,
                "hash": hash_object(request.proposal),
            },
            "adapter_identity_hash": self.identity.content_hash,
        }
        work_packet_id = f"fixture-arbitration-{hash_object(packet)}"
        request_bytes = canonical_json_bytes({**packet, "work_packet_id": work_packet_id})
        response_bytes = canonical_json_bytes(
            {
                "schema_version": 1,
                "work_packet_id": work_packet_id,
                "status": verdict.status,
                "selected_text": verdict.selected_text,
                "confidence": verdict.confidence,
                "rationale": verdict.rationale,
            }
        )
        clip_bytes = audio.read_bytes()
        request_artifact = _fixture_artifact("arbitration", "request", request_bytes)
        response_artifact = _fixture_artifact("arbitration", "response", response_bytes)
        clip_artifact = _fixture_artifact("arbitration", "clip", clip_bytes)
        receipt = ArbitrationReceipt(
            id=f"arbitration-{hash_object((work_packet_id, response_artifact.sha256))}",
            episode_id=request.episode_id,
            generation_id=request.generation_id,
            canonical_content_hash=request.transcript.content_hash,
            evidence_hash=request.transcript.evidence_hash,
            reference_evidence_hash=request.transcript.reference_evidence_hash,
            policy_hash=request.transcript.policy_hash,
            normalized_audio_hash=request.expected_normalized_audio_hash,
            proposal_id=request.proposal.id,
            proposal_hash=hash_object(request.proposal),
            start_ms=request.proposal.start_ms,
            end_ms=request.proposal.end_ms,
            work_packet_id=work_packet_id,
            request=request_artifact,
            response=response_artifact,
            clip=clip_artifact,
            adapter_name=self.identity.adapter_name,
            adapter_version=self.identity.adapter_version,
            model=self.identity.model,
            model_version=self.identity.model_version,
            runtime_hash=self.identity.runtime_hash,
            adapter_code_hash=self.identity.adapter_code_hash,
            config_hash=self.identity.config_hash,
            adapter_identity_hash=self.identity.content_hash,
            status=verdict.status,
            selected_text=verdict.selected_text,
            confidence=verdict.confidence,
            rationale=verdict.rationale,
        )
        return ArbitrationRunResult(
            verdict=verdict,
            receipt=receipt,
            request_bytes=request_bytes,
            response_bytes=response_bytes,
            clip_bytes=clip_bytes,
        )

    def decide_with_receipt(self, request: ArbitrationRequest) -> ArbitrationRunResult:
        return self._run_with_receipt(request)

    def replay(
        self,
        request: ArbitrationRequest,
        *,
        verdict: ArbitrationVerdict,
        receipt: ArbitrationReceipt,
        request_bytes: bytes,
        response_bytes: bytes,
        clip_bytes: bytes,
    ) -> ArbitrationRunResult:
        """Recreate the fixture verdict from bound inputs without external work."""

        _strict_json_bytes(response_bytes, label="arbitration response")
        try:
            stored = ArbitrationRunResult(
                verdict=verdict,
                receipt=receipt,
                request_bytes=request_bytes,
                response_bytes=response_bytes,
                clip_bytes=clip_bytes,
            )
        except (TypeError, ValueError) as exc:
            raise AdapterIntegrityError(
                f"stored fixture arbitration proof is invalid: {exc}"
            ) from exc
        expected = self._run_with_receipt(request)
        if stored != expected:
            raise AdapterIntegrityError("stored fixture arbitration result does not replay exactly")
        return expected


class FixtureSemanticAnalyzerAdapter:
    """Return gold Semantic Units after validating token order and coverage."""

    def __init__(
        self,
        units: tuple[SemanticUnit, ...],
        *,
        expected_content_hash: str | None = None,
        require_full_coverage: bool = True,
    ) -> None:
        self._units = tuple(units)
        self._expected_content_hash = expected_content_hash
        self._require_full_coverage = require_full_coverage
        self._identity = SemanticModelIdentity(
            adapter_name="fixture-semantic",
            adapter_version="1",
            model="fixture",
            model_version="1",
            runtime_hash=hash_object({"runtime": "fixture"}),
            adapter_code_hash=hash_object({"code": "fixture-semantic"}),
            config_hash=hash_object(
                {
                    "units": self._units,
                    "expected_content_hash": expected_content_hash,
                    "require_full_coverage": require_full_coverage,
                }
            ),
            execution_mode="fixture",
        )

    @property
    def identity(self) -> SemanticModelIdentity:
        return self._identity

    def _validated_units(self, request: SemanticAnalysisRequest) -> tuple[SemanticUnit, ...]:
        transcript = request.transcript
        if self._expected_content_hash and transcript.content_hash != self._expected_content_hash:
            raise AdapterIntegrityError(
                "fixture Semantic Units target another Canonical Transcript"
            )
        positions = {token.id: index for index, token in enumerate(transcript.tokens)}
        if len({unit.id for unit in self._units}) != len(self._units):
            raise AdapterInputError("fixture Semantic Unit IDs must be unique")
        covered: set[str] = set()
        for unit in self._units:
            try:
                indices = [positions[token_id] for token_id in unit.token_ids]
            except KeyError as exc:
                raise AdapterIntegrityError(
                    f"fixture Semantic Unit references unknown token {exc.args[0]!r}"
                ) from exc
            if indices != list(range(indices[0], indices[-1] + 1)):
                raise AdapterInputError(
                    f"fixture Semantic Unit {unit.id!r} must contain contiguous ordered tokens"
                )
            covered.update(unit.token_ids)
        if self._require_full_coverage:
            missing = set(positions) - covered
            if missing:
                raise AdapterInputError(
                    f"fixture Semantic Units do not cover canonical tokens: {sorted(missing)!r}"
                )
        return self._units

    def partition_with_receipts(self, request: SemanticAnalysisRequest) -> SemanticRunResult:
        return self._build_semantic_run(request)

    def _build_semantic_run(self, request: SemanticAnalysisRequest) -> SemanticRunResult:
        """Deterministically rebuild fixture proof without entering an action port."""

        units = self._validated_units(request)
        token_ids = tuple(token.id for token in request.transcript.tokens)
        packet: dict[str, object] = {
            "schema_version": 1,
            "task": "podcast_subtitle_v2_fixture_semantic_partition",
            "episode_id": request.transcript.episode_id,
            "generation_id": request.transcript.generation_id,
            "canonical_content_hash": request.transcript.content_hash,
            "policy_hash": request.policy_hash,
            "protected_range_set_hash": request.protected_range_set_hash,
            "protected_ranges": request.protected_ranges,
            "language": request.language,
            "adapter_identity_hash": self.identity.content_hash,
            "target_token_ids": token_ids,
            "owned_target_token_ids": token_ids,
            "units": units,
        }
        packet["work_packet_id"] = f"semantic-work-{hash_object(packet)}"
        request_bytes = canonical_json_bytes(packet)
        response_bytes = canonical_json_bytes(
            {
                "schema_version": 1,
                "work_packet_id": packet["work_packet_id"],
                "units": units,
            }
        )

        def artifact(kind: str, payload: bytes) -> ArtifactDigest:
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

    def replay(
        self,
        request: SemanticAnalysisRequest,
        *,
        units: tuple[SemanticUnit, ...],
        execution_receipts: tuple[SemanticExecutionReceipt, ...],
        request_bytes: tuple[bytes, ...],
        response_bytes: tuple[bytes, ...],
    ) -> SemanticRunResult:
        expected = self._build_semantic_run(request)
        try:
            stored = SemanticRunResult(
                canonical_token_ids=tuple(token.id for token in request.transcript.tokens),
                units=units,
                execution_receipts=execution_receipts,
                request_bytes=request_bytes,
                response_bytes=response_bytes,
            )
        except (TypeError, ValueError) as exc:
            raise AdapterIntegrityError(f"stored fixture Semantic proof is invalid: {exc}") from exc
        if stored != expected:
            raise AdapterIntegrityError("fixture Semantic execution proof does not replay")
        return expected

    def partition(self, request: SemanticAnalysisRequest) -> tuple[SemanticUnit, ...]:
        return self.partition_with_receipts(request).units


def unverified_identity_clock_map() -> AudioClockMap:
    """Explicit test helper; this map must never satisfy an accepted receipt."""

    return AudioClockMap(
        source_origin_ms=0,
        normalized_origin_ms=0,
        verified=False,
        drift_ms=0.0,
    )


__all__ = [
    "FixtureArbiterAdapter",
    "FixtureAudioAuditorAdapter",
    "FixtureCorrectorAdapter",
    "FixtureNormalizerAdapter",
    "FixtureReferenceRetrieverAdapter",
    "FixtureRecognizerAdapter",
    "FixtureSemanticAnalyzerAdapter",
    "unverified_identity_clock_map",
]
