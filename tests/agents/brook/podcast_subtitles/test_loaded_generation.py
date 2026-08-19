"""In-memory integrity contracts for loaded Podcast Subtitle V2 generations."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, is_dataclass, replace
from typing import Any, get_args, get_origin, get_type_hints

import pytest

from agents.brook.podcast_subtitles.audio_audit_execution import AudioAuditRunResult
from agents.brook.podcast_subtitles.canonical import CanonicalBuildResult
from agents.brook.podcast_subtitles.correction_acceptance import (
    CorrectionAcceptancePolicyV2,
    CorrectionAcceptanceVerdictV2,
)
from agents.brook.podcast_subtitles.full_audit_attestation import (
    FullAuditAggregateAttestationV2,
)
from agents.brook.podcast_subtitles.hashing import hash_object, sha256_bytes
from agents.brook.podcast_subtitles.loaded_generation import (
    LoadedAdapterIdentity,
    LoadedCommonRuntimeState,
    LoadedGenerationState,
    LoadedLedgerEntrySnapshot,
    LoadedLegacyAuditState,
    LoadedLegacyCollectionArtifacts,
    LoadedLegacyRuntimeState,
    LoadedNativeAudioRunArtifacts,
    LoadedNativeAuditArtifacts,
    LoadedNativeAuditState,
    LoadedNativeResolutionArtifacts,
    LoadedNativeResolutionState,
    LoadedNativeRuntimeState,
    LoadedNativeSourceSet,
    LoadedNativeTextRunArtifacts,
    LoadedNormalizationState,
    LoadedRecognitionState,
    LoadedReferenceState,
    LoadedSpeechCoverageState,
    LoadedStorageSnapshot,
    VerifiedArtifactBytes,
    VerifiedArtifactSet,
    VerifiedNativeSource,
)
from agents.brook.podcast_subtitles.native_resolution import NativeCorrectionDecisionV2
from agents.brook.podcast_subtitles.ports import (
    AudioModelIdentity,
    CorrectionModelIdentity,
    RecognitionModelIdentity,
    RecognitionRequest,
)
from agents.brook.podcast_subtitles.recognition_request import RecognitionRequestArtifactV1
from agents.brook.podcast_subtitles.text_audit_execution import TextAuditRunResult
from shared.schemas.podcast_subtitles_v2 import (
    AuditPlan,
    CandidateGroupSet,
    CandidateSignalSet,
    CanonicalTranscript,
    CorrectionAuditPolicySnapshot,
    GenerationManifest,
    ReferenceRetrievalPolicySnapshot,
    normalization_receipt_content_hash,
    recognition_evidence_set_hash,
    reference_evidence_set_hash,
)
from shared.schemas.podcast_subtitles_v2_audio_audit import (
    AudioAuditAdapterIdentityV2,
    AudioAuditExecutionPolicyV2,
    AudioAuditExecutionRecordV2,
)
from shared.schemas.podcast_subtitles_v2_correction import (
    CorrectionAuditExecutionPlanV2,
    CorrectionAuditExecutionPolicyV2,
)
from shared.schemas.podcast_subtitles_v2_text_audit import (
    TextAuditAdapterIdentityV2,
    TextAuditExecutionPolicyV2,
    TextAuditExecutionRecordV2,
    TextDiscoveredCandidateV2,
)
from tests.agents.brook.podcast_subtitles.test_module import _accepted_receipt, _evidence


def _artifact(name: str, payload: bytes) -> VerifiedArtifactBytes:
    return VerifiedArtifactBytes(name=name, sha256=sha256_bytes(payload), payload=payload)


def _legacy_runtime() -> LoadedLegacyRuntimeState:
    digest = "a" * 64
    runtime_components = (("python", "3.11"),)
    return LoadedLegacyRuntimeState(
        common=LoadedCommonRuntimeState(
            recognizers=(
                RecognitionModelIdentity(
                    adapter_name="recognizer",
                    adapter_version="1",
                    model="model",
                    model_version="1",
                    aligner="aligner",
                    aligner_version="1",
                    runtime_components=runtime_components,
                    runtime_hash=hash_object({"runtime_components": runtime_components}),
                    adapter_code_hash=digest,
                    config_hash=digest,
                    execution_mode="fixture",
                ),
            ),
            speech_coverage_analyzer=None,
            speaker_attributor=None,
            reference_retriever=None,
        ),
        corrector=LoadedAdapterIdentity(
            name="corrector",
            version="1",
            config_hash=digest,
            execution_mode="fixture",
        ),
        corrector_execution=CorrectionModelIdentity(
            adapter_name="corrector",
            adapter_version="1",
            model="model",
            model_version="1",
            runtime_hash=digest,
            adapter_code_hash=digest,
            config_hash=digest,
            execution_mode="fixture",
        ),
        audio_auditor=AudioModelIdentity(
            adapter_name="audio",
            adapter_version="1",
            model="model",
            model_version="1",
            runtime_hash=digest,
            adapter_code_hash=digest,
            config_hash=digest,
            execution_mode="fixture",
        ),
        arbiter=None,
        identities_artifact=_artifact("adapter_identities.json", b"{}"),
    )


def _common_runtime() -> LoadedCommonRuntimeState:
    return _legacy_runtime().common


def _native_audit(generation_id: str) -> LoadedNativeAuditState:
    plan_hash = "1" * 64
    signals_hash = "2" * 64
    groups_hash = "3" * 64
    execution_hash = "4" * 64
    transcript = CanonicalTranscript.model_construct(generation_id=generation_id)
    audit_basis = CanonicalBuildResult(
        outcome="needs_review",
        transcript=transcript,
        candidates=(),
        risks=(),
        reference_evidence_hash="5" * 64,
    )
    audit_plan = AuditPlan.model_construct(
        generation_id=generation_id,
        content_hash=plan_hash,
    )
    candidate_signals = CandidateSignalSet.model_construct(
        audit_plan_content_hash=plan_hash,
        content_hash=signals_hash,
    )
    candidate_groups = CandidateGroupSet.model_construct(
        audit_plan_content_hash=plan_hash,
        signal_set_content_hash=signals_hash,
        content_hash=groups_hash,
    )
    execution_plan = CorrectionAuditExecutionPlanV2.model_construct(
        generation_id=generation_id,
        audit_plan_content_hash=plan_hash,
        candidate_signal_set_content_hash=signals_hash,
        candidate_group_set_content_hash=groups_hash,
        content_hash=execution_hash,
    )
    text_run = TextAuditRunResult(
        record=TextAuditExecutionRecordV2.model_construct(
            execution_plan_content_hash=execution_hash
        ),
        request_bytes=(b"text-request",),
        response_bytes=(b"text-response",),
    )
    audio_run = AudioAuditRunResult(
        record=AudioAuditExecutionRecordV2.model_construct(
            execution_plan_content_hash=execution_hash
        ),
        request_bytes=(b"audio-request",),
        response_bytes=(b"audio-response",),
        clip_bytes=(b"audio-clip",),
    )

    def artifact_set(name: str, payload: bytes) -> VerifiedArtifactSet:
        return VerifiedArtifactSet((_artifact(name, payload),))

    text_sources = LoadedNativeSourceSet(
        modality="text",
        index_artifact=_artifact(
            "native_full_audit/text_source_artifact_index.json", b"text-index"
        ),
        sources=(
            VerifiedNativeSource(
                uri="urn:audit:text",
                artifact=_artifact("native_full_audit/sources/text/0000.bin", b"source"),
            ),
        ),
    )
    audio_sources = LoadedNativeSourceSet(
        modality="audio",
        index_artifact=_artifact(
            "native_full_audit/audio_source_artifact_index.json", b"audio-index"
        ),
        sources=(
            VerifiedNativeSource(
                uri="urn:audit:audio",
                artifact=_artifact("native_full_audit/sources/audio/0000.bin", b"source"),
            ),
        ),
    )
    text_artifacts = LoadedNativeTextRunArtifacts(
        index_artifact=_artifact("native_full_audit/text_run_index.json", b"text-runs"),
        requests=artifact_set(
            "native_full_audit/text/requests/0000.bin", text_run.request_bytes[0]
        ),
        responses=artifact_set(
            "native_full_audit/text/responses/0000.bin", text_run.response_bytes[0]
        ),
    )
    audio_artifacts = LoadedNativeAudioRunArtifacts(
        index_artifact=_artifact("native_full_audit/audio_run_index.json", b"audio-runs"),
        requests=artifact_set(
            "native_full_audit/audio/requests/0000.bin", audio_run.request_bytes[0]
        ),
        responses=artifact_set(
            "native_full_audit/audio/responses/0000.bin", audio_run.response_bytes[0]
        ),
        clips=artifact_set("native_full_audit/audio/clips/0000.bin", audio_run.clip_bytes[0]),
        journals=VerifiedArtifactSet(),
    )
    audit_artifacts = LoadedNativeAuditArtifacts(
        audit_plan=_artifact("native_full_audit/audit_plan.json", b"plan"),
        candidate_signals=_artifact("native_full_audit/candidate_signal_set.json", b"signals"),
        candidate_groups=_artifact("native_full_audit/candidate_group_set.json", b"groups"),
        execution_plan=_artifact(
            "native_full_audit/correction_audit_execution_plan.json", b"execution"
        ),
        text_record=_artifact("native_full_audit/text_audit_execution_record.json", b"text-record"),
        audio_record=_artifact(
            "native_full_audit/audio_audit_execution_record.json", b"audio-record"
        ),
        aggregate=_artifact("native_full_audit/full_audit_aggregate.json", b"aggregate"),
    )
    runtime = LoadedNativeRuntimeState(
        common=_common_runtime(),
        text_identity=TextAuditAdapterIdentityV2.model_construct(),
        text_policy=TextAuditExecutionPolicyV2.model_construct(),
        audio_identity=AudioAuditAdapterIdentityV2.model_construct(),
        audio_policy=AudioAuditExecutionPolicyV2.model_construct(),
        audit_policy=CorrectionAuditPolicySnapshot.model_construct(),
        execution_policy=CorrectionAuditExecutionPolicyV2.model_construct(),
        identities_artifact=_artifact("adapter_identities.json", b"{}"),
    )
    return LoadedNativeAuditState(
        audit_basis=audit_basis,
        audit_plan=audit_plan,
        candidate_signals=candidate_signals,
        candidate_groups=candidate_groups,
        execution_plan=execution_plan,
        text_sources=text_sources,
        audio_sources=audio_sources,
        text_run=text_run,
        audio_run=audio_run,
        aggregate=FullAuditAggregateAttestationV2.model_construct(generation_id=generation_id),
        audit_artifacts=audit_artifacts,
        text_run_artifacts=text_artifacts,
        audio_run_artifacts=audio_artifacts,
        runtime=runtime,
        resolution=None,
    )


def _native_resolution() -> LoadedNativeResolutionState:
    candidate_id = "1" * 64
    candidate_hash = "2" * 64
    authorization_id = "3" * 64
    authorization_hash = "4" * 64
    policy_hash = "5" * 64
    candidate = TextDiscoveredCandidateV2.model_construct(
        id=candidate_id,
        content_hash=candidate_hash,
    )
    policy = CorrectionAcceptancePolicyV2.model_construct(content_hash=policy_hash)
    authorization = CorrectionAcceptanceVerdictV2.model_construct(
        id=authorization_id,
        content_hash=authorization_hash,
        candidate_discovery_id=candidate_id,
        candidate_discovery_hash=candidate_hash,
        policy_hash=policy_hash,
    )
    decision = NativeCorrectionDecisionV2.model_construct(
        authorization_kind="correction_acceptance",
        authorization_id=authorization_id,
        authorization_hash=authorization_hash,
        candidate_discovery_id=candidate_id,
        candidate_discovery_hash=candidate_hash,
    )
    return LoadedNativeResolutionState(
        authorization_kind="correction_acceptance",
        candidate=candidate,
        authorization=authorization,
        policy=policy,
        decision=decision,
        ledger_entry=LoadedLedgerEntrySnapshot(
            sequence=1,
            previous_hash="6" * 64,
            decision_hash=hash_object(decision.model_dump(mode="json")),
            entry_hash="7" * 64,
        ),
        human_audio_receipts=(),
        human_original_confirmation_receipts=(),
        reference_proof=None,
        reference_adjudication=None,
        artifacts=LoadedNativeResolutionArtifacts(
            decision=_artifact("native_resolution/decision.json", b"decision"),
            candidate=_artifact("native_resolution/candidate_discovery.json", b"candidate"),
            authorization=_artifact("native_resolution/authorization.json", b"authorization"),
            policy=_artifact("native_resolution/acceptance_policy.json", b"policy"),
            human_receipt_index=_artifact("native_resolution/human_audio_index.json", b"receipts"),
            ledger_entry=_artifact("native_resolution/prepared_ledger_entry.json", b"ledger"),
            human_receipts=VerifiedArtifactSet(),
            reference_proof=None,
            reference_adjudication=None,
        ),
    )


def _native_loaded_generation(tmp_path) -> LoadedGenerationState:
    generation_id = "7" * 64
    episode_id = "episode-loaded-state"
    invocation_id = "invocation-loaded-state"
    source = tmp_path / "source.wav"
    normalized = tmp_path / "normalized.wav"
    raw = tmp_path / "recognition.json"
    source.write_bytes(b"source")
    normalized.write_bytes(b"normalized")
    raw.write_bytes(b"recognition")
    receipt = _accepted_receipt(source, normalized)
    evidence = _evidence(
        episode_id=episode_id,
        invocation_id=invocation_id,
        normalized=normalized,
        raw=raw,
        adapter="recognizer",
        texts=("測試",),
    )
    transcript = CanonicalTranscript.model_construct(
        episode_id=episode_id,
        generation_id=generation_id,
        source_audio_hash=receipt.source.sha256,
        normalized_audio_hash=receipt.normalized.sha256,
        normalization_receipt_hash=normalization_receipt_content_hash(receipt),
        evidence_hash=recognition_evidence_set_hash((evidence,)),
        orthographic_projection_evidence_hash=None,
        verified_speech_coverage_receipt_hash=None,
        reference_evidence_hash=reference_evidence_set_hash(()),
    )
    result = CanonicalBuildResult(
        outcome="needs_review",
        transcript=transcript,
        candidates=(),
        risks=(),
        reference_evidence_hash=transcript.reference_evidence_hash,
    )
    digest = "a" * 64
    manifest = GenerationManifest(
        id="manifest-loaded-state",
        episode_id=episode_id,
        generation_id=generation_id,
        parent_manifest_hash="b" * 64,
        input_artifact_hashes={"input": digest},
        code_hash=digest,
        policy_hash=digest,
        adapter_hash=digest,
        stage_artifact_hashes={"stage": digest},
        status="complete",
    )
    storage = LoadedStorageSnapshot.capture(
        generation_id=generation_id,
        artifact_set_hash=digest,
        manifest=manifest,
        artifact_hashes={"canonical_transcript.json": digest},
        directory=tmp_path,
    )
    normalization = LoadedNormalizationState(
        receipt=receipt,
        source_audio_path=source,
        normalized_audio_path=normalized,
        receipt_artifact=_artifact("normalization_receipt.json", b"receipt"),
        audio_bindings_artifact=_artifact("audio_artifacts.json", b"audio"),
    )
    request_artifact = RecognitionRequestArtifactV1.model_construct(
        episode_id=episode_id,
        invocation_id=invocation_id,
        normalized_audio_sha256=receipt.normalized.sha256,
        normalized_audio_size_bytes=receipt.normalized.size_bytes,
    )
    recognition = LoadedRecognitionState(
        request_artifact=request_artifact,
        request=RecognitionRequest(
            episode_id=episode_id,
            invocation_id=invocation_id,
            normalized_audio=normalized,
            expected_normalized_audio_hash=receipt.normalized.sha256,
        ),
        independence=None,
        evidence=(evidence,),
        speaker_base_evidence=(),
        speaker_provenance=None,
        orthographic_projections=(),
        request_bytes=_artifact("recognition_request.json", b"request"),
        independence_bytes=_artifact("recognition_independence_receipt.json", b"null"),
        evidence_bytes=_artifact("recognition_evidence.json", b"evidence"),
        speaker_base_evidence_bytes=_artifact("base_primary_recognition_evidence.json", b"[]"),
        speaker_provenance_bytes=_artifact("speaker_attribution_provenance.json", b"null"),
        orthographic_projection_bytes=None,
        raw_outputs=VerifiedArtifactSet(
            (
                _artifact(
                    f"raw_evidence/{evidence.raw_output.sha256}.json",
                    raw.read_bytes(),
                ),
            )
        ),
        orthographic_raw_outputs=VerifiedArtifactSet(),
    )
    references = LoadedReferenceState(
        enrollments=(),
        evidence=(),
        retrievals=(),
        retrieval_policy=ReferenceRetrievalPolicySnapshot.model_construct(),
        enrollments_bytes=_artifact("reference_enrollments.json", b"[]"),
        evidence_bytes=_artifact("reference_evidence.json", b"[]"),
        retrievals_bytes=_artifact("reference_retrieval_receipts.json", b"[]"),
        retrieval_policy_bytes=_artifact("reference_retrieval_policy.json", b"{}"),
        source_snapshots=VerifiedArtifactSet(),
        extraction_snapshots=VerifiedArtifactSet(),
    )
    coverage = LoadedSpeechCoverageState(
        receipt=None,
        receipt_bytes=_artifact("speech_coverage_receipt.json", b"null"),
        raw_outputs=VerifiedArtifactSet(),
    )
    return LoadedGenerationState(
        result=result,
        storage=storage,
        normalization=normalization,
        recognition=recognition,
        references=references,
        speech_coverage=coverage,
        audit=_native_audit(generation_id),
    )


def test_verified_artifact_bytes_is_an_immutable_exact_byte_binding() -> None:
    payload = b'{"schema_version":1}'
    artifact = VerifiedArtifactBytes(
        name="recognition/request.json",
        sha256=sha256_bytes(payload),
        payload=payload,
    )

    assert artifact.payload is payload
    assert artifact.size_bytes == len(payload)
    assert not hasattr(artifact, "__dict__")
    with pytest.raises(FrozenInstanceError):
        artifact.name = "changed.json"  # type: ignore[misc]


def test_verified_artifact_set_rejects_duplicate_or_noncanonical_order() -> None:
    first = _artifact("audit/0001.json", b"first")
    second = _artifact("audit/0002.json", b"second")

    verified = VerifiedArtifactSet(artifacts=(first, second))

    assert verified.require(first.name) is first
    assert verified.names == (first.name, second.name)
    with pytest.raises(ValueError, match="canonical name order"):
        VerifiedArtifactSet(artifacts=(second, first))
    with pytest.raises(ValueError, match="duplicate"):
        VerifiedArtifactSet(artifacts=(first, first))


def test_verified_artifact_bytes_rejects_digest_tampering() -> None:
    with pytest.raises(ValueError, match="digest mismatch"):
        VerifiedArtifactBytes(
            name="recognition/request.json",
            sha256=sha256_bytes(b"original"),
            payload=b"tampered",
        )


def test_loaded_storage_snapshot_breaks_mutable_manifest_and_record_aliases(
    tmp_path,
) -> None:
    digest = "a" * 64
    manifest = GenerationManifest(
        id="manifest-1",
        episode_id="episode-1",
        generation_id=digest,
        input_artifact_hashes={"input": digest},
        code_hash=digest,
        policy_hash=digest,
        adapter_hash=digest,
        stage_artifact_hashes={"stage": digest},
    )
    artifact_hashes = {"canonical.json": digest}

    snapshot = LoadedStorageSnapshot.capture(
        generation_id=digest,
        artifact_set_hash=digest,
        manifest=manifest,
        artifact_hashes=artifact_hashes,
        directory=tmp_path,
    )
    manifest.input_artifact_hashes["later"] = "b" * 64
    artifact_hashes["later.json"] = "b" * 64

    assert tuple(item.name for item in snapshot.manifest.input_artifact_hashes) == ("input",)
    assert snapshot.artifact_hashes == (snapshot.artifact_hashes[0],)
    assert snapshot.artifact_hash("canonical.json") == digest
    assert snapshot.manifest.materialize() == GenerationManifest(
        id="manifest-1",
        episode_id="episode-1",
        generation_id=digest,
        input_artifact_hashes={"input": digest},
        code_hash=digest,
        policy_hash=digest,
        adapter_hash=digest,
        stage_artifact_hashes={"stage": digest},
    )


def test_legacy_audit_state_has_a_closed_discriminant_and_no_positional_api() -> None:
    empty = VerifiedArtifactSet()
    collection_artifacts = LoadedLegacyCollectionArtifacts(
        proposals=_artifact("correction_proposals.json", b"[]"),
        correction_audits=_artifact("correction_audit_receipts.json", b"[]"),
        targeted_correction_audits=_artifact("targeted_correction_audit_receipts.json", b"[]"),
        audio_audits=_artifact("audio_audit_receipts.json", b"[]"),
        arbitrations=_artifact("arbitration_receipts.json", b"[]"),
    )
    state = LoadedLegacyAuditState(
        proposals=(),
        correction_audits=(),
        targeted_correction_audits=(),
        audio_audits=(),
        arbitrations=(),
        collection_artifacts=collection_artifacts,
        correction_execution_artifacts=empty,
        audio_execution_artifacts=empty,
        runtime=_legacy_runtime(),
    )

    assert state.profile == "legacy_v1"
    assert not hasattr(state, "__getitem__")
    assert not hasattr(state, "__iter__")
    with pytest.raises(TypeError):
        LoadedLegacyAuditState(  # type: ignore[arg-type]
            proposals=[],
            correction_audits=(),
            targeted_correction_audits=(),
            audio_audits=(),
            arbitrations=(),
            collection_artifacts=collection_artifacts,
            correction_execution_artifacts=empty,
            audio_execution_artifacts=empty,
            runtime=_legacy_runtime(),
        )


def test_native_role_collections_reject_reordering_and_missing_run_bytes() -> None:
    index = _artifact("native_full_audit/text_source_artifact_index.json", b"index")
    source_a = _artifact("native_full_audit/sources/text/a.json", b"a")
    source_b = _artifact("native_full_audit/sources/text/b.json", b"b")
    from agents.brook.podcast_subtitles.loaded_generation import VerifiedNativeSource

    with pytest.raises(ValueError, match="canonical URI order"):
        LoadedNativeSourceSet(
            modality="text",
            index_artifact=index,
            sources=(
                VerifiedNativeSource(uri="urn:b", artifact=source_b),
                VerifiedNativeSource(uri="urn:a", artifact=source_a),
            ),
        )
    with pytest.raises(ValueError, match="at least one exact source"):
        LoadedNativeSourceSet(modality="text", index_artifact=index, sources=())

    text_artifacts = LoadedNativeTextRunArtifacts(
        index_artifact=_artifact("native_full_audit/text_run_index.json", b"index"),
        requests=VerifiedArtifactSet(),
        responses=VerifiedArtifactSet(),
    )
    audio_artifacts = LoadedNativeAudioRunArtifacts(
        index_artifact=_artifact("native_full_audit/audio_run_index.json", b"index"),
        requests=VerifiedArtifactSet(),
        responses=VerifiedArtifactSet(),
        clips=VerifiedArtifactSet(),
        journals=VerifiedArtifactSet(),
    )

    class _TextRun:
        request_bytes = (b"request",)
        response_bytes = (b"response",)

    class _AudioRun:
        request_bytes = (b"request",)
        response_bytes = (b"response",)
        clip_bytes = (b"clip",)

    with pytest.raises(ValueError, match="text request bytes"):
        text_artifacts.verify_run(_TextRun())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="audio request bytes"):
        audio_artifacts.verify_run(_AudioRun())  # type: ignore[arg-type]


def test_native_audit_state_carries_exact_typed_parents_under_closed_profile() -> None:
    generation_id = "9" * 64

    state = _native_audit(generation_id)

    assert state.profile == "native_v2"
    assert state.audit_basis.transcript.generation_id == generation_id
    assert state.candidate_signals.audit_plan_content_hash == state.audit_plan.content_hash
    assert state.candidate_groups.signal_set_content_hash == state.candidate_signals.content_hash
    assert state.execution_plan.candidate_group_set_content_hash == (
        state.candidate_groups.content_hash
    )
    assert (
        state.text_run_artifacts.requests.artifacts[0].payload is (state.text_run.request_bytes[0])
    )
    assert state.audio_run_artifacts.clips.artifacts[0].payload is (state.audio_run.clip_bytes[0])
    assert state.resolution is None
    assert not hasattr(state, "__getitem__")
    assert not hasattr(state, "__iter__")


def test_native_audit_state_rejects_crossed_full_audit_parent_lineage() -> None:
    state = _native_audit("8" * 64)
    crossed_groups = state.candidate_groups.model_copy(update={"signal_set_content_hash": "f" * 64})

    with pytest.raises(ValueError, match="parents crossed content lineage"):
        LoadedNativeAuditState(
            audit_basis=state.audit_basis,
            audit_plan=state.audit_plan,
            candidate_signals=state.candidate_signals,
            candidate_groups=crossed_groups,
            execution_plan=state.execution_plan,
            text_sources=state.text_sources,
            audio_sources=state.audio_sources,
            text_run=state.text_run,
            audio_run=state.audio_run,
            aggregate=state.aggregate,
            audit_artifacts=state.audit_artifacts,
            text_run_artifacts=state.text_run_artifacts,
            audio_run_artifacts=state.audio_run_artifacts,
            runtime=state.runtime,
            resolution=None,
        )


def test_native_resolution_state_carries_one_closed_authorization_branch() -> None:
    state = _native_resolution()

    assert state.authorization_kind == "correction_acceptance"
    assert state.decision.authorization_id == state.authorization.id
    assert state.artifacts.authorization.payload == b"authorization"
    with pytest.raises(ValueError, match="branch differs"):
        replace(state, authorization_kind="original_confirmation")


def test_ledger_snapshot_rejects_zero_based_sequence() -> None:
    with pytest.raises(ValueError, match="positive"):
        LoadedLedgerEntrySnapshot(
            sequence=0,
            previous_hash="1" * 64,
            decision_hash="2" * 64,
            entry_hash="3" * 64,
        )


def test_native_parent_manifest_without_resolution_is_a_valid_loaded_derivative(
    tmp_path,
) -> None:
    state = _native_loaded_generation(tmp_path)

    assert state.profile == "native_v2"
    assert state.storage.manifest.parent_manifest_hash is not None
    assert state.audit.resolution is None
    assert not hasattr(state, "__getitem__")
    assert not hasattr(state, "__iter__")


def test_top_state_rejects_crossed_canonical_recognition_lineage(tmp_path) -> None:
    state = _native_loaded_generation(tmp_path)
    crossed_transcript = state.result.transcript.model_copy(update={"evidence_hash": "f" * 64})

    with pytest.raises(ValueError, match="Recognition Evidence crossed Canonical lineage"):
        LoadedGenerationState(
            result=CanonicalBuildResult(
                outcome=state.result.outcome,
                transcript=crossed_transcript,
                candidates=state.result.candidates,
                risks=state.result.risks,
                reference_evidence_hash=state.result.reference_evidence_hash,
            ),
            storage=state.storage,
            normalization=state.normalization,
            recognition=state.recognition,
            references=state.references,
            speech_coverage=state.speech_coverage,
            audit=state.audit,
        )


def test_recognition_state_rejects_a_missing_exact_raw_output(tmp_path) -> None:
    state = _native_loaded_generation(tmp_path)

    with pytest.raises(ValueError, match="raw output exact artifact set"):
        replace(state.recognition, raw_outputs=VerifiedArtifactSet())


def test_all_loaded_state_dataclasses_are_frozen_slotted_and_non_positional() -> None:
    import agents.brook.podcast_subtitles.loaded_generation as loaded

    state_classes = tuple(
        value
        for name in loaded.__all__
        if name.startswith(("Loaded", "Verified", "ArtifactHashBinding"))
        and isinstance((value := getattr(loaded, name)), type)
        and is_dataclass(value)
    )
    assert state_classes
    for state_class in state_classes:
        assert state_class.__dataclass_params__.frozen is True
        assert "__slots__" in state_class.__dict__
        assert "__getitem__" not in state_class.__dict__
        assert "__iter__" not in state_class.__dict__


def test_core_state_field_types_have_no_mutable_or_untyped_escape_hatch() -> None:
    import agents.brook.podcast_subtitles.loaded_generation as loaded

    def contains_escape_hatch(annotation) -> bool:
        origin = get_origin(annotation)
        if annotation in {Any, dict, tuple} or origin is dict:
            return True
        return any(contains_escape_hatch(item) for item in get_args(annotation))

    core_classes = (
        loaded.LoadedGenerationState,
        loaded.LoadedStorageSnapshot,
        loaded.LoadedNormalizationState,
        loaded.LoadedRecognitionState,
        loaded.LoadedReferenceState,
        loaded.LoadedSpeechCoverageState,
        loaded.LoadedLegacyAuditState,
        loaded.LoadedNativeAuditState,
        loaded.LoadedNativeResolutionState,
    )
    for state_class in core_classes:
        for field_name, annotation in get_type_hints(state_class).items():
            assert not contains_escape_hatch(annotation), (
                f"{state_class.__name__}.{field_name} has an untyped/mutable core field"
            )


def test_top_audit_field_is_exactly_the_legacy_native_discriminated_union() -> None:
    audit_types = set(get_args(get_type_hints(LoadedGenerationState)["audit"]))

    assert audit_types == {LoadedLegacyAuditState, LoadedNativeAuditState}
