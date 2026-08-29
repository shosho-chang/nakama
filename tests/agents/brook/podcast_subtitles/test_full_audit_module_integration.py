"""Tracer tests for the native Podcast Subtitle V2 full-audit create path."""

from __future__ import annotations

import json
import wave
from dataclasses import replace
from pathlib import Path

import pytest

from agents.brook.podcast_subtitles.adapters.fixtures import (
    FixtureAudioAuditorAdapter,
    FixtureCorrectorAdapter,
    FixtureNormalizerAdapter,
)
from agents.brook.podcast_subtitles.adapters.speech_coverage import (
    FixtureSpeechCoverageAnalyzer,
)
from agents.brook.podcast_subtitles.audio_audit_execution import (
    AudioFullAuditExecutor,
    build_audio_audit_adapter_identity,
)
from agents.brook.podcast_subtitles.audio_audit_selection import (
    default_audio_audit_selection_policy,
)
from agents.brook.podcast_subtitles.checkpoint import build_create_checkpoint
from agents.brook.podcast_subtitles.errors import (
    ArtifactHashMismatchError,
    GenerationIsolationError,
    NativeAcceptanceRequiredError,
)
from agents.brook.podcast_subtitles.facade import PodcastSubtitleFacade
from agents.brook.podcast_subtitles.full_audit_attestation import (
    FullAuditAggregateAttestationV2,
)
from agents.brook.podcast_subtitles.hashing import (
    canonical_json_bytes,
    hash_file,
    hash_object,
    sha256_bytes,
)
from agents.brook.podcast_subtitles.loaded_generation import LoadedNativeAuditState
from agents.brook.podcast_subtitles.module import (
    AdapterIdentity,
    CreateRequest,
    Interrupted,
    NativeFullAuditBundle,
    NeedsReview,
    PodcastSubtitleV2,
    ProjectRequest,
    ResolveRequest,
)
from agents.brook.podcast_subtitles.profiles import HORIZONTAL_16X9
from agents.brook.podcast_subtitles.text_audit_execution import (
    TextFullAuditExecutor,
    build_text_audit_adapter_identity,
)
from shared.schemas.podcast_subtitles_v2_audio_audit import (
    AudioAuditProviderRequestV2,
)
from tests.agents.brook.podcast_subtitles.test_audio_audit_execution import (
    _response_bytes as audio_response_bytes,
)
from tests.agents.brook.podcast_subtitles.test_audio_audit_execution import (
    _response_payload as audio_response_payload,
)
from tests.agents.brook.podcast_subtitles.test_audio_audit_execution import (
    _typed_source as typed_audio_source,
)
from tests.agents.brook.podcast_subtitles.test_module import (
    _accepted_receipt,
    _adapter_identity,
    _BoundFixtureRecognizer,
    _CountingNormalizer,
    _CountingRecognizer,
    _decision_for_created,
    _DynamicSemanticAnalyzer,
)
from tests.agents.brook.podcast_subtitles.test_module_reference_enrollment import (
    _reference_setup,
)
from tests.agents.brook.podcast_subtitles.test_text_audit_execution import (
    _response_bytes as text_response_bytes,
)


def _pcm_wav(path: Path, *, duration_seconds: int = 4) -> None:
    with wave.open(str(path), "wb") as sink:
        sink.setnchannels(1)
        sink.setsampwidth(2)
        sink.setframerate(8_000)
        sink.writeframes(b"\x00\x00" * 8_000 * duration_seconds)


def _material_audio_response_bytes(request_bytes: bytes, _clip_bytes: bytes) -> bytes:
    request = AudioAuditProviderRequestV2.model_validate_json(request_bytes)
    canonical = typed_audio_source(request, "canonical_transcript")
    token_by_id = {item.id: item for item in canonical.tokens}
    payload = audio_response_payload(request_bytes)
    assessment = payload["assessments"][0]
    assert isinstance(assessment, dict)
    affected_token_ids = assessment["cited_token_ids"]
    assert isinstance(affected_token_ids, tuple)
    observed_text = "".join(token_by_id[item].text for item in affected_token_ids)
    assessment.update(
        {
            "status": "finding",
            "affected_token_ids": affected_token_ids,
            "observed_text": observed_text,
            "candidate_text": f"{observed_text}修正",
            "evidence_basis": "exact_audio_clip",
        }
    )
    return canonical_json_bytes(payload)


def _native_module(
    episode_root: Path,
    *,
    workspace: Path,
    text_adapter: str = "fixture-native-text-auditor",
    audio_adapter: str = "fixture-native-audio-auditor",
    reference_retriever=None,
    reference_retriever_identity: AdapterIdentity | None = None,
    selective_audio: bool = True,
    code_version: str = "podcast-subtitle-v2",
) -> tuple[PodcastSubtitleV2, Path, _CountingNormalizer, tuple[_CountingRecognizer, ...]]:
    episode_root.mkdir(parents=True, exist_ok=True)
    source = episode_root / "source.wav"
    normalized = episode_root / "normalized.wav"
    raw_one = episode_root / "primary.json"
    raw_two = episode_root / "secondary.json"
    if not source.exists():
        _pcm_wav(source)
    if not normalized.exists():
        _pcm_wav(normalized)
    if not raw_one.exists():
        raw_one.write_text('{"adapter":"primary"}', encoding="utf-8")
    if not raw_two.exists():
        raw_two.write_text('{"adapter":"secondary"}', encoding="utf-8")

    normalizer = _CountingNormalizer(
        FixtureNormalizerAdapter(normalized, _accepted_receipt(source, normalized))
    )
    recognizers = (
        _CountingRecognizer(
            _BoundFixtureRecognizer(normalized, raw_one, "primary", ("哥大", "BA典禮"))
        ),
        _CountingRecognizer(
            _BoundFixtureRecognizer(normalized, raw_two, "secondary", ("哥大", "畢業典禮"))
        ),
    )
    ordinary_words = (
        "哥大畢業典禮",
        "訪談片段乙",
        "訪談片段丙",
        "訪談片段丁",
        "訪談片段戊",
        "訪談片段己",
        "訪談片段庚",
        "訪談片段辛",
        "訪談片段壬",
        "訪談片段癸",
    )
    recognizers = (
        _CountingRecognizer(
            _BoundFixtureRecognizer(normalized, raw_one, "primary", ordinary_words)
        ),
        _CountingRecognizer(
            _BoundFixtureRecognizer(normalized, raw_two, "secondary", ordinary_words)
        ),
    )
    semantic = _DynamicSemanticAnalyzer()
    native = NativeFullAuditBundle(
        text_executor=TextFullAuditExecutor(
            identity=build_text_audit_adapter_identity(
                adapter=text_adapter,
                adapter_version="1",
                model="fixture-text-model",
                model_version="1",
                execution_mode="subscription",
            ),
            workspace_root=workspace,
        ),
        audio_executor=AudioFullAuditExecutor(
            identity=build_audio_audit_adapter_identity(
                adapter=audio_adapter,
                adapter_version="1",
                model="fixture-audio-model",
                model_version="1",
                execution_mode="subscription",
            ),
            workspace_root=workspace,
        ),
        audio_selection_policy=(
            default_audio_audit_selection_policy() if selective_audio else None
        ),
    )
    module = PodcastSubtitleV2(
        episode_root,
        normalizer=normalizer,
        recognizers=recognizers,
        semantic_analyzer=semantic,
        semantic_analyzer_identity=AdapterIdentity(
            name="fixture-semantic",
            version="1",
            config_hash=semantic.identity.config_hash,
            execution_mode="fixture",
        ),
        speech_coverage_analyzer=FixtureSpeechCoverageAnalyzer(),
        native_full_audit=native,
        reference_retriever=reference_retriever,
        reference_retriever_identity=reference_retriever_identity,
        code_version=code_version,
    )
    module._corrector = _PoisonLegacyAudit()
    module._audio_auditor = _PoisonLegacyAudit()
    return module, source, normalizer, recognizers


class _PoisonLegacyAudit:
    def propose(self, *_args, **_kwargs):
        raise AssertionError("legacy Corrector must not run in native Full Audit")

    def propose_with_receipt(self, *_args, **_kwargs):
        raise AssertionError("legacy Corrector must not run in native Full Audit")

    def replay(self, *_args, **_kwargs):
        raise AssertionError("legacy Corrector replay must not run in native Full Audit")

    def audit(self, *_args, **_kwargs):
        raise AssertionError("legacy AudioAuditor must not run in native Full Audit")


def test_native_create_resumes_text_then_audio_and_finishes_needs_review(
    tmp_path: Path,
) -> None:
    episode_root = tmp_path / "episode"
    workspace = tmp_path / "native-workspace"
    request = CreateRequest(
        episode_id="episode-native-full-audit",
        source_audio=episode_root / "source.wav",
    )

    first, source, first_normalizer, first_recognizers = _native_module(
        episode_root,
        workspace=workspace,
    )
    request = replace(request, source_audio=source)
    text_pending = first.create(request)

    assert isinstance(text_pending, Interrupted)
    assert text_pending.audit_basis_generation_id == text_pending.generation_id
    assert text_pending.packet_paths
    assert not text_pending.clip_paths
    assert first_normalizer.calls == 1
    assert tuple(item.calls for item in first_recognizers) == (1, 1)
    checkpoint = first.store.load_latest_create_checkpoint()
    assert checkpoint is not None
    assert checkpoint.checkpoint.stage == "native_audit_basis_ready"
    assert "native_full_audit/audio_source_artifact_index.json" not in (
        checkpoint.checkpoint.artifact_hashes
    )
    assert "native_full_audit/text_execution_plan_v3.json" in (
        checkpoint.checkpoint.artifact_hashes
    )
    checkpoint_names = {
        item.role: item.adapter_name for item in checkpoint.checkpoint.adapter_identities
    }
    assert checkpoint_names["native_text_full_audit"] == "fixture-native-text-auditor"
    assert checkpoint_names["native_audio_full_audit"] == "fixture-native-audio-auditor"
    assert not first.store.generations_dir.exists()
    for request_path, response_path in zip(
        text_pending.packet_paths,
        text_pending.response_paths,
        strict=True,
    ):
        response_path.parent.mkdir(parents=True, exist_ok=True)
        response_path.write_bytes(text_response_bytes(request_path.read_bytes()))

    second, _, second_normalizer, second_recognizers = _native_module(
        episode_root,
        workspace=workspace,
    )
    audio_pending = second.create(request)

    assert isinstance(audio_pending, Interrupted)
    assert audio_pending.audit_basis_generation_id == text_pending.audit_basis_generation_id
    assert audio_pending.packet_paths
    assert audio_pending.clip_paths
    assert second_normalizer.calls == 0
    assert tuple(item.calls for item in second_recognizers) == (0, 0)
    assert not second.store.generations_dir.exists()
    text_ready = second.store.load_latest_create_checkpoint()
    assert text_ready is not None
    assert text_ready.checkpoint.stage == "native_text_audit_ready"
    assert "native_full_audit/initial_audio_selection_v3.json" in (
        text_ready.checkpoint.artifact_hashes
    )
    for request_path, clip_path, response_path in zip(
        audio_pending.packet_paths,
        audio_pending.clip_paths,
        audio_pending.response_paths,
        strict=True,
    ):
        response_path.parent.mkdir(parents=True, exist_ok=True)
        response_path.write_bytes(
            audio_response_bytes(request_path.read_bytes(), clip_path.read_bytes())
        )

    third, _, third_normalizer, third_recognizers = _native_module(
        episode_root,
        workspace=workspace,
    )
    completed = third.create(request)

    assert isinstance(completed, NeedsReview)
    assert completed.generation_id == text_pending.audit_basis_generation_id
    assert third_normalizer.calls == 0
    assert tuple(item.calls for item in third_recognizers) == (0, 0)
    assert completed.transcript.full_audit_receipt_set_hash is None
    assert any(item.code == "native_full_audit_requires_resolution" for item in completed.issues)
    assert third.store.read_artifact(
        completed.generation_id,
        "native_full_audit/selective_audit_aggregate_v3.json",
    )

    workspace.rename(tmp_path / "detached-native-workspace")
    fourth, _, fourth_normalizer, fourth_recognizers = _native_module(
        episode_root,
        workspace=workspace,
    )
    reloaded = fourth.create(request)

    assert isinstance(reloaded, NeedsReview)
    assert reloaded == completed
    assert fourth_normalizer.calls == 0
    assert tuple(item.calls for item in fourth_recognizers) == (0, 0)


def test_selective_native_create_resumes_10_to_30_to_full_without_prefix_rerun(
    tmp_path: Path,
) -> None:
    episode_root = tmp_path / "episode"
    workspace = tmp_path / "workspace"
    first, source, _, _ = _native_module(episode_root, workspace=workspace)
    request = CreateRequest(episode_id="episode-selective-escalation", source_audio=source)
    text_pending = first.create(request)
    assert isinstance(text_pending, Interrupted)
    for request_path, response_path in zip(
        text_pending.packet_paths, text_pending.response_paths, strict=True
    ):
        response_path.parent.mkdir(parents=True, exist_ok=True)
        response_path.write_bytes(text_response_bytes(request_path.read_bytes()))

    second, _, _, _ = _native_module(episode_root, workspace=workspace)
    ten_pending = second.create(request)
    assert isinstance(ten_pending, Interrupted)
    assert len(ten_pending.packet_paths) == 1
    for request_path, clip_path, response_path in zip(
        ten_pending.packet_paths,
        ten_pending.clip_paths,
        ten_pending.response_paths,
        strict=True,
    ):
        response_path.parent.mkdir(parents=True, exist_ok=True)
        response_path.write_bytes(
            _material_audio_response_bytes(request_path.read_bytes(), clip_path.read_bytes())
        )

    third, _, third_normalizer, third_recognizers = _native_module(
        episode_root, workspace=workspace
    )
    thirty_pending = third.create(request)
    assert isinstance(thirty_pending, Interrupted)
    assert len(thirty_pending.packet_paths) == 2
    assert set(thirty_pending.packet_paths).isdisjoint(ten_pending.packet_paths)
    assert third_normalizer.calls == 0
    assert tuple(item.calls for item in third_recognizers) == (0, 0)
    for request_path, clip_path, response_path in zip(
        thirty_pending.packet_paths,
        thirty_pending.clip_paths,
        thirty_pending.response_paths,
        strict=True,
    ):
        response_path.parent.mkdir(parents=True, exist_ok=True)
        response_path.write_bytes(
            audio_response_bytes(request_path.read_bytes(), clip_path.read_bytes())
        )

    fourth, _, _, _ = _native_module(episode_root, workspace=workspace)
    full_pending = fourth.create(request)
    assert isinstance(full_pending, Interrupted)
    assert len(full_pending.packet_paths) == 7
    assert set(full_pending.packet_paths).isdisjoint(
        {*ten_pending.packet_paths, *thirty_pending.packet_paths}
    )
    for request_path, clip_path, response_path in zip(
        full_pending.packet_paths,
        full_pending.clip_paths,
        full_pending.response_paths,
        strict=True,
    ):
        response_path.parent.mkdir(parents=True, exist_ok=True)
        response_path.write_bytes(
            audio_response_bytes(request_path.read_bytes(), clip_path.read_bytes())
        )

    final, _, final_normalizer, final_recognizers = _native_module(
        episode_root, workspace=workspace
    )
    completed = final.create(request)
    assert isinstance(completed, NeedsReview)
    assert final_normalizer.calls == 0
    assert tuple(item.calls for item in final_recognizers) == (0, 0)
    aggregate = json.loads(
        final.store.read_artifact(
            completed.generation_id,
            "native_full_audit/selective_audit_aggregate_v3.json",
        )
    )
    assert aggregate["terminal_tier"] == "full"
    assert aggregate["terminal_decision"] == "complete"


def test_selective_runtime_reopens_completed_legacy_v2_generation(tmp_path: Path) -> None:
    episode_root = tmp_path / "episode"
    workspace = tmp_path / "workspace"
    first, source, _, _ = _native_module(
        episode_root,
        workspace=workspace,
        selective_audio=False,
    )
    request = CreateRequest(episode_id="episode-legacy-readable", source_audio=source)
    text_pending = first.create(request)
    assert isinstance(text_pending, Interrupted)
    for request_path, response_path in zip(
        text_pending.packet_paths, text_pending.response_paths, strict=True
    ):
        response_path.parent.mkdir(parents=True, exist_ok=True)
        response_path.write_bytes(text_response_bytes(request_path.read_bytes()))

    second, _, _, _ = _native_module(
        episode_root,
        workspace=workspace,
        selective_audio=False,
    )
    audio_pending = second.create(request)
    assert isinstance(audio_pending, Interrupted)
    for request_path, clip_path, response_path in zip(
        audio_pending.packet_paths,
        audio_pending.clip_paths,
        audio_pending.response_paths,
        strict=True,
    ):
        response_path.parent.mkdir(parents=True, exist_ok=True)
        response_path.write_bytes(
            audio_response_bytes(request_path.read_bytes(), clip_path.read_bytes())
        )

    legacy, _, _, _ = _native_module(
        episode_root,
        workspace=workspace,
        selective_audio=False,
    )
    completed = legacy.create(request)
    assert isinstance(completed, NeedsReview)
    selective, _, _, _ = _native_module(episode_root, workspace=workspace)
    loaded = selective._load_generation(completed.generation_id, require_active=True)
    assert isinstance(loaded.audit, LoadedNativeAuditState)
    assert isinstance(loaded.audit.aggregate, FullAuditAggregateAttestationV2)


def test_selective_runtime_rejects_incomplete_legacy_v2_checkpoint(tmp_path: Path) -> None:
    episode_root = tmp_path / "episode"
    workspace = tmp_path / "workspace"
    legacy, source, _, _ = _native_module(
        episode_root,
        workspace=workspace,
        selective_audio=False,
    )
    request = CreateRequest(episode_id="episode-legacy-incomplete", source_audio=source)
    pending = legacy.create(request)
    assert isinstance(pending, Interrupted)
    checkpoint = legacy.store.load_latest_create_checkpoint()
    assert checkpoint is not None
    assert checkpoint.checkpoint.stage == "native_audit_basis_ready"

    selective, _, _, _ = _native_module(episode_root, workspace=workspace)
    with pytest.raises(GenerationIsolationError):
        selective.create(request)


def test_native_and_legacy_audit_composition_is_rejected(tmp_path: Path) -> None:
    module, _, _, _ = _native_module(
        tmp_path / "seed",
        workspace=tmp_path / "workspace",
    )

    with pytest.raises(ValueError, match="mutually exclusive"):
        PodcastSubtitleV2(
            tmp_path / "ambiguous",
            normalizer=module._normalizer,
            recognizers=module._recognizers,
            semantic_analyzer=module._semantic_analyzer,
            semantic_analyzer_identity=module._semantic_analyzer_identity,
            corrector=FixtureCorrectorAdapter(),
            audio_auditor=FixtureAudioAuditorAdapter(),
            corrector_identity=_adapter_identity("fixture-corrector"),
            native_full_audit=module._native_full_audit,
        )


def test_native_identity_drift_stops_before_prefix_provider_calls(tmp_path: Path) -> None:
    episode_root = tmp_path / "episode"
    workspace = tmp_path / "workspace"
    first, source, _, _ = _native_module(episode_root, workspace=workspace)
    request = CreateRequest(episode_id="episode-native-drift", source_audio=source)
    assert isinstance(first.create(request), Interrupted)

    changed, _, normalizer, recognizers = _native_module(
        episode_root,
        workspace=workspace,
        text_adapter="changed-native-text-auditor",
    )
    with pytest.raises(GenerationIsolationError, match="incomplete create operation"):
        changed.create(request)
    assert normalizer.calls == 0
    assert tuple(item.calls for item in recognizers) == (0, 0)
    assert not changed.store.generations_dir.exists()
    assert not tuple(changed.store.audio_staging_dir.iterdir())


def test_module_code_only_drift_stops_before_prefix_provider_calls(tmp_path: Path) -> None:
    episode_root = tmp_path / "episode"
    workspace = tmp_path / "workspace"
    first, source, _, _ = _native_module(
        episode_root,
        workspace=workspace,
        code_version="source-inventory-before",
    )
    request = CreateRequest(episode_id="episode-module-code-drift", source_audio=source)
    assert isinstance(first.create(request), Interrupted)

    changed, _, normalizer, recognizers = _native_module(
        episode_root,
        workspace=workspace,
        code_version="source-inventory-after",
    )
    with pytest.raises(GenerationIsolationError, match="incomplete create operation"):
        changed.create(request)
    assert normalizer.calls == 0
    assert tuple(item.calls for item in recognizers) == (0, 0)
    assert not tuple(changed.store.audio_staging_dir.iterdir())


def test_native_basis_tamper_stops_before_prefix_provider_calls(tmp_path: Path) -> None:
    episode_root = tmp_path / "episode"
    workspace = tmp_path / "workspace"
    first, source, _, _ = _native_module(episode_root, workspace=workspace)
    request = CreateRequest(episode_id="episode-native-tamper", source_audio=source)
    assert isinstance(first.create(request), Interrupted)
    checkpoint = first.store.load_latest_create_checkpoint()
    assert checkpoint is not None
    (checkpoint.directory / "native_full_audit" / "audit_plan.json").write_bytes(
        b'{"tampered":true}'
    )

    fresh, _, normalizer, recognizers = _native_module(
        episode_root,
        workspace=workspace,
    )
    with pytest.raises(ArtifactHashMismatchError, match="artifact hash mismatch"):
        fresh.create(request)
    assert normalizer.calls == 0
    assert tuple(item.calls for item in recognizers) == (0, 0)
    assert not fresh.store.generations_dir.exists()


def _complete_native_generation(tmp_path: Path) -> tuple[PodcastSubtitleV2, NeedsReview]:
    episode_root = tmp_path / "episode"
    workspace = tmp_path / "workspace"
    first, source, _, _ = _native_module(episode_root, workspace=workspace)
    request = CreateRequest(episode_id="episode-native-reader", source_audio=source)
    text_pending = first.create(request)
    assert isinstance(text_pending, Interrupted)
    for request_path, response_path in zip(
        text_pending.packet_paths, text_pending.response_paths, strict=True
    ):
        response_path.parent.mkdir(parents=True, exist_ok=True)
        response_path.write_bytes(text_response_bytes(request_path.read_bytes()))
    second, _, _, _ = _native_module(episode_root, workspace=workspace)
    audio_pending = second.create(request)
    assert isinstance(audio_pending, Interrupted)
    for request_path, clip_path, response_path in zip(
        audio_pending.packet_paths,
        audio_pending.clip_paths,
        audio_pending.response_paths,
        strict=True,
    ):
        response_path.parent.mkdir(parents=True, exist_ok=True)
        response_path.write_bytes(
            audio_response_bytes(request_path.read_bytes(), clip_path.read_bytes())
        )
    final, _, _, _ = _native_module(episode_root, workspace=workspace)
    completed = final.create(request)
    assert isinstance(completed, NeedsReview)
    return final, completed


def test_native_general_load_and_facade_status_review_are_fully_verified(tmp_path: Path) -> None:
    module, completed = _complete_native_generation(tmp_path)

    loaded = module._load_generation(completed.generation_id, require_active=True)
    status = PodcastSubtitleFacade(module).status()
    review = PodcastSubtitleFacade(module).review()

    assert loaded.result.transcript == completed.transcript
    assert loaded.profile == "native_v2"
    assert status.state == "complete"
    assert status.transcript_status == "draft"
    assert review.generation_id == completed.generation_id
    assert any(item["code"] == "native_full_audit_requires_resolution" for item in review.issues)


def test_native_resolve_boundary_is_typed_and_side_effect_free(tmp_path: Path) -> None:
    module, completed = _complete_native_generation(tmp_path)
    decision = _decision_for_created(completed, event_id="native-must-not-resolve")
    active_before = module.store.active_generation_id()
    ledger_before = module.ledger.entries()
    generation_before = module.store.load(completed.generation_id)

    for action in (
        "confirm_original",
        "replace",
        "accept_candidate",
        "reject_candidate",
        "defer",
    ):
        action_decision = decision.model_copy(update={"action": action})
        with pytest.raises(NativeAcceptanceRequiredError, match="native acceptance"):
            module.resolve(ResolveRequest(completed.generation_id, (action_decision,)))

        assert module.store.active_generation_id() == active_before
        assert module.ledger.entries() == ledger_before
        assert module.store.load(completed.generation_id) == generation_before


def test_native_generation_cannot_project_before_acceptance_integration(tmp_path: Path) -> None:
    module, completed = _complete_native_generation(tmp_path)

    with pytest.raises(Exception, match="accepted active Canonical Transcript"):
        module.project(ProjectRequest(completed.generation_id, HORIZONTAL_16X9))


def _forge_native_terminal_chain(module: PodcastSubtitleV2, generation_id: str) -> None:
    """Bind a valid Generation to a hash-valid but semantically empty native chain."""

    generation_dir = module.store.generation_dir(generation_id)
    generation_record_path = generation_dir / "generation.json"
    generation_record = json.loads(generation_record_path.read_text(encoding="utf-8"))
    terminal = module.store.load_latest_create_checkpoint()
    assert terminal is not None and terminal.checkpoint.stage == "complete"
    original = terminal.checkpoint
    assert original.previous_checkpoint_id is not None
    operation_key = hash_object({"attack": "native-terminal-reseal"})
    common = {
        "operation_key": operation_key,
        "episode_id": original.episode_id,
        "source": original.source,
        "input_binding_hash": original.input_binding_hash,
        "policy_hash": original.policy_hash,
        "code_hash": original.code_hash,
        "reference_enrollment_hash": original.reference_enrollment_hash,
        "speaker_track_binding_hash": original.speaker_track_binding_hash,
        "adapter_identities": original.adapter_identities,
        "expected_active_generation_id": generation_id,
    }
    started_payload = canonical_json_bytes({"attacker_stage": "started"})
    previous = build_create_checkpoint(
        **common,
        stage="started",
        invocation_id=None,
        normalized=None,
        normalization_receipt_hash=None,
        artifact_hashes={"attacker.json": sha256_bytes(started_payload)},
    )
    module.store.commit_create_checkpoint(previous, artifacts={"attacker.json": started_payload})
    for stage in (
        "evidence_ready",
        "native_audit_basis_ready",
        "native_text_audit_ready",
        "native_audio_audit_ready",
    ):
        stage_payload = canonical_json_bytes({"attacker_stage": stage})
        checkpoint = build_create_checkpoint(
            **common,
            stage=stage,
            invocation_id=original.invocation_id,
            normalized=original.normalized,
            normalization_receipt_hash=original.normalization_receipt_hash,
            artifact_hashes={"attacker.json": sha256_bytes(stage_payload)},
            previous_checkpoint_id=previous.id,
        )
        module.store.commit_create_checkpoint(
            checkpoint, artifacts={"attacker.json": stage_payload}
        )
        previous = checkpoint

    binding = {
        "schema_version": 1,
        "generation_id": generation_id,
        "generation_artifact_set_hash": generation_record["artifact_set_hash"],
        "generation_record_hash": hash_file(generation_record_path),
        "generation_manifest_hash": original.generation_manifest_hash,
        "generation_transcript_hash": original.generation_transcript_hash,
        "generation_outcome": original.generation_outcome,
        "previous_checkpoint_id": previous.id,
    }
    binding_bytes = canonical_json_bytes(binding)
    forged = build_create_checkpoint(
        **common,
        stage="complete",
        invocation_id=original.invocation_id,
        normalized=original.normalized,
        normalization_receipt_hash=original.normalization_receipt_hash,
        artifact_hashes={"checkpoint/complete_binding.json": sha256_bytes(binding_bytes)},
        previous_checkpoint_id=previous.id,
        generation_id=generation_id,
        generation_artifact_set_hash=generation_record["artifact_set_hash"],
        generation_record_hash=hash_file(generation_record_path),
        generation_manifest_hash=original.generation_manifest_hash,
        generation_transcript_hash=original.generation_transcript_hash,
        generation_outcome=original.generation_outcome,
    )
    module.store.commit_create_checkpoint(
        forged,
        artifacts={"checkpoint/complete_binding.json": binding_bytes},
    )


def _forge_hash_consistent_native_terminal_tamper(
    module: PodcastSubtitleV2, generation_id: str
) -> None:
    """Model an attacker who can reseal hashes but cannot forge semantic replay."""

    generation_dir = module.store.generation_dir(generation_id)
    text_index = json.loads(
        (generation_dir / "native_full_audit" / "text_run_index.json").read_text(encoding="utf-8")
    )
    response_name = text_index["responses"][0]
    response_path = generation_dir / Path(response_name)
    response_path.write_bytes(response_path.read_bytes() + b" ")
    generation_record_path = generation_dir / "generation.json"
    generation_record = json.loads(generation_record_path.read_text(encoding="utf-8"))
    generation_record["artifact_hashes"][response_name] = hash_file(response_path)
    generation_record["artifact_set_hash"] = hash_object(
        {
            "store_version": generation_record["store_version"],
            "manifest": generation_record["manifest"],
            "artifact_hashes": generation_record["artifact_hashes"],
        }
    )
    generation_record_path.write_bytes(canonical_json_bytes(generation_record))
    module.store.set_active(generation_id)
    _forge_native_terminal_chain(module, generation_id)


def _reseal_generation_record(module: PodcastSubtitleV2, generation_id: str) -> None:
    directory = module.store.generation_dir(generation_id)
    record_path = directory / "generation.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["artifact_set_hash"] = hash_object(
        {
            "store_version": record["store_version"],
            "manifest": record["manifest"],
            "artifact_hashes": record["artifact_hashes"],
        }
    )
    record_path.write_bytes(canonical_json_bytes(record))
    module.store.set_active(generation_id)


def test_native_general_load_rejects_reordered_source_index(tmp_path: Path) -> None:
    module, completed = _complete_native_generation(tmp_path)
    directory = module.store.generation_dir(completed.generation_id)
    index_path = directory / "native_full_audit" / "text_source_artifact_index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    assert len(index["entries"]) > 1
    index["entries"].reverse()
    index_bytes = canonical_json_bytes(index)
    index_path.write_bytes(index_bytes)
    record_path = directory / "generation.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["artifact_hashes"]["native_full_audit/text_source_artifact_index.json"] = sha256_bytes(
        index_bytes
    )
    record_path.write_bytes(canonical_json_bytes(record))
    _reseal_generation_record(module, completed.generation_id)

    with pytest.raises(GenerationIsolationError, match="source index is not canonical"):
        module._load_generation(completed.generation_id, require_active=True)


def test_native_general_load_rejects_non_addressed_source_name(tmp_path: Path) -> None:
    module, completed = _complete_native_generation(tmp_path)
    directory = module.store.generation_dir(completed.generation_id)
    index_name = "native_full_audit/text_source_artifact_index.json"
    index_path = directory / Path(index_name)
    index = json.loads(index_path.read_text(encoding="utf-8"))
    entry = index["entries"][0]
    original_name = entry["artifact_name"]
    forged_name = "native_full_audit/not-content-addressed.bin"
    forged_path = directory / Path(forged_name)
    forged_path.write_bytes((directory / Path(original_name)).read_bytes())
    entry["artifact_name"] = forged_name
    index_bytes = canonical_json_bytes(index)
    index_path.write_bytes(index_bytes)
    record_path = directory / "generation.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["artifact_hashes"].pop(original_name)
    record["artifact_hashes"][forged_name] = sha256_bytes(forged_path.read_bytes())
    record["artifact_hashes"][index_name] = sha256_bytes(index_bytes)
    record_path.write_bytes(canonical_json_bytes(record))
    _reseal_generation_record(module, completed.generation_id)

    with pytest.raises(GenerationIsolationError, match="source index is not canonical"):
        module._load_generation(completed.generation_id, require_active=True)


def test_native_general_load_rejects_non_addressed_run_name(tmp_path: Path) -> None:
    module, completed = _complete_native_generation(tmp_path)
    directory = module.store.generation_dir(completed.generation_id)
    index_name = "native_full_audit/text_run_index.json"
    index_path = directory / Path(index_name)
    index = json.loads(index_path.read_text(encoding="utf-8"))
    original_name = index["requests"][0]
    forged_name = "native_full_audit/text/requests/not-content-addressed.bin"
    forged_path = directory / Path(forged_name)
    forged_path.parent.mkdir(parents=True, exist_ok=True)
    forged_path.write_bytes((directory / Path(original_name)).read_bytes())
    index["requests"][0] = forged_name
    index_bytes = canonical_json_bytes(index)
    index_path.write_bytes(index_bytes)
    record_path = directory / "generation.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["artifact_hashes"].pop(original_name)
    record["artifact_hashes"][forged_name] = sha256_bytes(forged_path.read_bytes())
    record["artifact_hashes"][index_name] = sha256_bytes(index_bytes)
    record_path.write_bytes(canonical_json_bytes(record))
    _reseal_generation_record(module, completed.generation_id)

    with pytest.raises(GenerationIsolationError, match="run index is not canonical"):
        module._load_generation(completed.generation_id, require_active=True)


def test_fresh_constructor_replays_hash_consistent_native_terminal_proofs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module, completed = _complete_native_generation(tmp_path)
    _forge_hash_consistent_native_terminal_tamper(module, completed.generation_id)

    def provider_call_forbidden(*_args, **_kwargs):
        raise AssertionError("terminal verification must not execute a provider or runner")

    monkeypatch.setattr(TextFullAuditExecutor, "execute", provider_call_forbidden)
    monkeypatch.setattr(AudioFullAuditExecutor, "execute", provider_call_forbidden)

    with pytest.raises(
        GenerationIsolationError,
        match="native Generation run index is not canonical|native text audit is not replayable",
    ):
        _native_module(
            tmp_path / "episode",
            workspace=tmp_path / "workspace",
        )


def test_fresh_constructor_rejects_valid_generation_with_fake_native_checkpoint_chain(
    tmp_path: Path,
) -> None:
    module, completed = _complete_native_generation(tmp_path)
    _forge_native_terminal_chain(module, completed.generation_id)

    with pytest.raises(GenerationIsolationError, match="native terminal checkpoint artifacts"):
        _native_module(
            tmp_path / "episode",
            workspace=tmp_path / "workspace",
        )


def test_reference_backed_native_generation_reopens_from_immutable_snapshots(
    tmp_path: Path,
) -> None:
    episode_root = tmp_path / "episode"
    workspace = tmp_path / "workspace"
    retriever, enrollment = _reference_setup(tmp_path / "references")
    reference_identity = AdapterIdentity(
        name="local-reference",
        version=retriever.RETRIEVER_VERSION,
        config_hash=hash_object({"index_hash": retriever.index.index_hash}),
        execution_mode="local",
    )

    def fresh_module() -> tuple[PodcastSubtitleV2, Path]:
        module, source, _, _ = _native_module(
            episode_root,
            workspace=workspace,
            reference_retriever=retriever,
            reference_retriever_identity=reference_identity,
        )
        return module, source

    first, source = fresh_module()
    request = CreateRequest(
        episode_id="episode-native-reference",
        source_audio=source,
        reference_enrollments=(enrollment,),
        vocabulary=("哥大畢業典禮",),
    )
    text_pending = first.create(request)
    assert isinstance(text_pending, Interrupted)
    for request_path, response_path in zip(
        text_pending.packet_paths, text_pending.response_paths, strict=True
    ):
        response_path.parent.mkdir(parents=True, exist_ok=True)
        response_path.write_bytes(text_response_bytes(request_path.read_bytes()))
    second, _ = fresh_module()
    audio_pending = second.create(request)
    assert isinstance(audio_pending, Interrupted)
    for request_path, clip_path, response_path in zip(
        audio_pending.packet_paths,
        audio_pending.clip_paths,
        audio_pending.response_paths,
        strict=True,
    ):
        response_path.parent.mkdir(parents=True, exist_ok=True)
        response_path.write_bytes(
            audio_response_bytes(request_path.read_bytes(), clip_path.read_bytes())
        )
    third, _ = fresh_module()
    completed = third.create(request)
    assert isinstance(completed, NeedsReview)
    loaded = third._load_generation(completed.generation_id, require_active=True)
    assert tuple(item.source_id for item in loaded.references.enrollments) == (
        enrollment.artifact.source_id,
    )
    assert loaded.references.enrollments[0].digest.sha256 == enrollment.artifact.digest.sha256
    assert loaded.references.evidence

    workspace.rename(tmp_path / "detached-workspace")
    reopened, _ = fresh_module()
    replayed = reopened._load_generation(completed.generation_id, require_active=True)
    assert replayed.result.transcript == completed.transcript
    assert replayed.references.enrollments == loaded.references.enrollments
    assert replayed.references.evidence == loaded.references.evidence
