"""Behavior tests for correction-quality execution topology."""

from __future__ import annotations

import io
import wave
from pathlib import Path

import pytest
from pydantic import ValidationError

from agents.brook.podcast_subtitles import correction_execution as correction_execution_module
from agents.brook.podcast_subtitles.audit_plan import (
    build_audit_plan,
    default_correction_audit_policy,
)
from agents.brook.podcast_subtitles.candidate_generation import (
    derive_candidate_group_set,
    derive_candidate_signal_set,
)
from agents.brook.podcast_subtitles.correction_execution import (
    CorrectionExecutionError,
    assert_correction_audit_execution_plan,
    build_correction_audit_execution_plan,
    default_correction_audit_execution_policy,
    derive_pcm_wav_clip_bytes,
)
from agents.brook.podcast_subtitles.hashing import (
    canonical_json_bytes,
    hash_object,
    sha256_bytes,
)
from agents.brook.podcast_subtitles.profiles import CorrectionAuditExecutionDefaults
from shared.schemas.podcast_subtitles_v2 import (
    EMPTY_REFERENCE_EVIDENCE_HASH,
    ArtifactDigest,
    CanonicalSpan,
    CanonicalToken,
    CanonicalTranscript,
    EvidenceToken,
    RecognitionEvidence,
    RecognitionSeamEvidence,
    RecognitionSeamObservation,
    canonical_content_hash,
    recognition_evidence_content_hash,
    recognition_evidence_set_hash,
    reference_evidence_set_hash,
)
from shared.schemas.podcast_subtitles_v2_correction import (
    CorrectionAuditExecutionLimitsV2,
    CorrectionAuditExecutionPlanV2,
    CorrectionAuditExecutionPolicyV2,
    CorrectionAuditPacketV2,
    CorrectionPacketReferenceLocatorPartV2,
)
from tests.agents.brook.podcast_subtitles.test_candidate_generation import (
    make_reference_receipt,
)

H2 = "2" * 64
H3 = "3" * 64
H4 = "4" * 64
H5 = "5" * 64
H6 = "6" * 64
H7 = "7" * 64
H8 = "8" * 64
H9 = "9" * 64


def test_recognition_interval_lookup_preserves_strict_overlap_edges() -> None:
    tokens = tuple(
        EvidenceToken(
            id=f"edge-{index}",
            text=text,
            start_ms=start,
            end_ms=end,
            evidence_refs=(f"segment-{index}",),
        )
        for index, (text, start, end) in enumerate(
            (("a", 0, 100), ("b", 100, 200), ("c", 250, 300))
        )
    )
    index = correction_execution_module._RecognitionIntervalIndex(
        source_hash=H2,
        tokens=tokens,
        starts=tuple(item.start_ms for item in tokens),
        ends=tuple(item.end_ms for item in tokens),
    )

    assert tuple(item.id for item in index.overlapping(((100, 200),))) == ("edge-1",)
    assert index.overlapping(((200, 250),)) == ()
    assert tuple(item.id for item in index.overlapping(((100, 250),))) == ("edge-1",)
    assert tuple(
        item.id for item in index.overlapping(((0, 100), (100, 200)))
    ) == ("edge-0", "edge-1")


def test_execution_defaults_reject_bool_as_integer() -> None:
    with pytest.raises(ValueError, match="must be positive"):
        CorrectionAuditExecutionDefaults(text_max_cells_per_packet=True)


def replace_execution_limits(
    policy: CorrectionAuditExecutionPolicyV2,
    *,
    text_updates: dict[str, int] | None = None,
    audio_updates: dict[str, int] | None = None,
) -> CorrectionAuditExecutionPolicyV2:
    payload = policy.model_dump(mode="python", exclude={"content_hash"})
    for modality, updates in (
        ("text", text_updates or {}),
        ("audio", audio_updates or {}),
    ):
        limits_payload = dict(payload[modality])
        limits_payload.update(updates)
        payload[modality] = CorrectionAuditExecutionLimitsV2(**limits_payload)
    return CorrectionAuditExecutionPolicyV2(
        **payload,
        content_hash=hash_object(payload),
    )


def make_pcm_wav(*, duration_ms: int = 60_000, sample_rate_hz: int = 1_000) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(sample_rate_hz)
        stream.writeframes(b"\x00\x00" * (duration_ms * sample_rate_hz // 1_000))
    return output.getvalue()


def make_inputs(
    *, mandatory_boundaries: bool = True
) -> tuple[
    bytes,
    CanonicalTranscript,
    tuple[RecognitionEvidence, ...],
    RecognitionSeamEvidence,
]:
    audio = make_pcm_wav()
    audio_hash = sha256_bytes(audio)
    texts = ("今天", "訪談", "作者", "研究")
    timings = ((100, 300), (400, 600), (700, 900), (1_000, 1_200))
    speakers = (
        ("guest", "guest", "host", "host")
        if mandatory_boundaries
        else ("guest", "guest", "guest", "guest")
    )
    evidence_tokens = tuple(
        EvidenceToken(
            id=f"ev-{index}",
            text=text,
            start_ms=start,
            end_ms=end,
            confidence=0.95,
            speaker=speakers[index],
            evidence_refs=(f"segment-{index}",),
        )
        for index, (text, (start, end)) in enumerate(zip(texts, timings, strict=True))
    )
    recognition = RecognitionEvidence(
        episode_id="episode-1",
        invocation_id="recognition-1",
        adapter="fixture-asr",
        model="fixture-model",
        language="zh",
        config_hash=H4,
        raw_output=ArtifactDigest(uri="file:///raw.json", sha256=H5, size_bytes=100),
        raw_output_hash=H5,
        normalized_audio_hash=audio_hash,
        tokens=evidence_tokens,
    )
    recognitions = (recognition,)
    tokens = tuple(
        CanonicalToken(
            id=f"token-{index}",
            text=text,
            start_ms=start,
            end_ms=end,
            evidence_ids=(f"ev-{index}",),
            confidence=0.95,
            speaker=speakers[index],
        )
        for index, (text, (start, end)) in enumerate(zip(texts, timings, strict=True))
    )
    transcript = CanonicalTranscript(
        episode_id="episode-1",
        generation_id="generation-1",
        revision=1,
        status="draft",
        source_audio_hash=H2,
        normalized_audio_hash=audio_hash,
        normalization_receipt_hash=H3,
        evidence_hash=recognition_evidence_set_hash(recognitions),
        reference_evidence_hash=EMPTY_REFERENCE_EVIDENCE_HASH,
        ledger_hash=H6,
        policy_hash=H7,
        acceptance_policy={"permit_unresolved_low_risk": True},
        tokens=tokens,
        spans=tuple(
            CanonicalSpan(
                id=f"span-{index}",
                token_ids=(f"token-{index}",),
                start_ms=start,
                end_ms=end,
            )
            for index, (start, end) in enumerate(timings)
        ),
        content_hash=canonical_content_hash(tokens),
    )
    recognition_hashes = (recognition_evidence_content_hash(recognition),)
    observation_payload = {
        "schema_version": 1,
        "id": "seam-1",
        "seam_ms": 650,
        "status": "matched",
        "raw_artifact_hash": H5,
        "left_chunk_id": "chunk-left",
        "right_chunk_id": "chunk-right",
        "left_chunk_receipt_hash": H8,
        "right_chunk_receipt_hash": H9,
    }
    observation = RecognitionSeamObservation(
        **observation_payload,
        observation_hash=hash_object(observation_payload),
    )
    seam_payload = {
        "schema_version": 1,
        "status": "complete" if mandatory_boundaries else "unchunked",
        "normalized_audio_hash": audio_hash,
        "recognition_evidence_hashes": recognition_hashes,
        "raw_artifact_hash": H5,
        "observations": (observation,) if mandatory_boundaries else (),
        "failure_reason": None,
    }
    seam = RecognitionSeamEvidence(
        **seam_payload,
        content_hash=hash_object(seam_payload),
    )
    return audio, transcript, recognitions, seam


def build_fixture(
    tmp_path: Path,
    *,
    mandatory_boundaries: bool = True,
    execution_policy: CorrectionAuditExecutionPolicyV2 | None = None,
    with_references: bool = False,
) -> tuple[object, object, object, object, Path, object, object, object, object]:
    audio, transcript, recognitions, seam = make_inputs(mandatory_boundaries=mandatory_boundaries)
    audio_path = tmp_path / "normalized.wav"
    audio_path.write_bytes(audio)
    reference_retrievals = ()
    if with_references:
        transcript, receipt = make_reference_receipt(transcript)
        evidence = receipt.evidence[0]
        artifact = evidence.artifact.model_copy(
            update={
                "digest": ArtifactDigest(
                    uri="file:///C:/private/reference/book.txt",
                    sha256=evidence.artifact.digest.sha256,
                    size_bytes=evidence.artifact.digest.size_bytes,
                ),
                "extracted_text": ArtifactDigest(
                    uri=r"C:\private\reference\book.extracted.json",
                    sha256=evidence.artifact.extracted_text.sha256,
                    size_bytes=evidence.artifact.extracted_text.size_bytes,
                ),
            }
        )
        evidence = evidence.model_copy(update={"artifact": artifact})
        receipt = receipt.model_copy(update={"evidence": (evidence,)})
        transcript = CanonicalTranscript.model_validate(
            {
                **transcript.model_dump(mode="python"),
                "reference_evidence_hash": reference_evidence_set_hash((evidence,)),
                "content_hash": canonical_content_hash(transcript.tokens),
            }
        )
        reference_retrievals = (receipt,)
    audit_policy = default_correction_audit_policy()
    audit_plan = build_audit_plan(
        transcript,
        recognitions,
        audit_policy,
        references_enrolled=with_references,
        reference_retrievals=reference_retrievals,
        seam_evidence=seam,
    )
    signal_set = derive_candidate_signal_set(
        audit_plan,
        transcript,
        recognitions,
        references_enrolled=with_references,
        reference_retrievals=reference_retrievals,
        seam_evidence=seam,
    )
    group_set = derive_candidate_group_set(audit_plan, signal_set, transcript)
    execution_policy = execution_policy or default_correction_audit_execution_policy()
    execution_plan = build_correction_audit_execution_plan(
        audit_plan,
        signal_set,
        group_set,
        transcript,
        recognitions,
        execution_policy,
        reference_retrievals=reference_retrievals,
        seam_evidence=seam,
        normalized_audio_path=audio_path,
    )
    return (
        audit_plan,
        signal_set,
        group_set,
        execution_plan,
        audio_path,
        transcript,
        seam,
        recognitions,
        execution_policy,
    )


def test_execution_plan_partitions_every_cell_per_modality_and_binds_exact_clips(
    tmp_path: Path,
) -> None:
    audit_plan, _, _, execution, audio_path, _, _, _, _ = build_fixture(tmp_path)

    text_packets = tuple(item for item in execution.packets if item.modality == "text")
    audio_packets = tuple(item for item in execution.packets if item.modality == "audio")
    assert len(text_packets) == len(audio_packets) == 2
    assert {cell_id for packet in text_packets for cell_id in packet.owned_cell_ids} == {
        item.id for item in audit_plan.cells
    }
    assert {cell_id for packet in audio_packets for cell_id in packet.owned_cell_ids} == {
        item.id for item in audit_plan.cells
    }
    assert all(
        set(packet.requested_cell_ids)
        == {
            cell_id
            for cell_id in packet.owned_cell_ids
            if next(item for item in audit_plan.cells if item.id == cell_id).applicability
            == "required"
        }
        for packet in execution.packets
    )

    second_text = text_packets[1]
    assert audit_plan.boundary_targets[2].id in second_text.owned_target_ids
    assert audit_plan.boundary_targets[-1].id in second_text.owned_target_ids
    assert "known_speaker_transition" in second_text.split_before_reasons
    assert "recognition_seam" in second_text.split_before_reasons

    for packet in audio_packets:
        clip_binding = next(item for item in packet.source_bindings if item.kind == "audio_clip")
        clip_bytes, start_frame, end_frame = derive_pcm_wav_clip_bytes(
            audio_path,
            clip_start_ms=packet.clip_start_ms,  # type: ignore[arg-type]
            clip_end_ms=packet.clip_end_ms,  # type: ignore[arg-type]
        )
        assert clip_binding.content_hash == sha256_bytes(clip_bytes)
        assert packet.clip_start_frame == start_frame
        assert packet.clip_end_frame == end_frame

    source_by_kind = {item.kind: item for item in execution.source_bindings}
    assert source_by_kind["audit_plan"].content_hash == sha256_bytes(
        canonical_json_bytes(audit_plan)
    )
    assert source_by_kind["normalized_audio"].content_hash == sha256_bytes(audio_path.read_bytes())
    assert all(
        all(
            item.kind not in {"reference_retrieval_receipt_set", "normalized_audio"}
            for item in packet.source_bindings
        )
        for packet in execution.packets
    )
    full_artifact_hashes = {
        item.kind: item.content_hash
        for item in execution.source_bindings
        if item.kind
        in {
            "audit_plan",
            "candidate_signal_set",
            "candidate_group_set",
            "canonical_transcript",
            "recognition_evidence_set",
        }
    }
    for packet in execution.packets:
        packet_bindings = {
            item.kind: item for item in packet.source_bindings if item.kind in full_artifact_hashes
        }
        assert packet_bindings.keys() == full_artifact_hashes.keys()
        for kind, binding in packet_bindings.items():
            assert binding.artifact_uri.startswith("execution-artifact://packet-inputs/")
            assert binding.content_hash != full_artifact_hashes[kind]
            assert binding.internal_object_hash == binding.id
        packet_manifest_bytes = canonical_json_bytes(packet.source_bindings)
        assert b"file:///" not in packet_manifest_bytes
        assert str(audio_path).encode("utf-8") not in packet_manifest_bytes


def test_execution_plan_exact_rebuild_and_cross_lineage_fail_closed(tmp_path: Path) -> None:
    (
        audit_plan,
        signal_set,
        group_set,
        execution,
        audio_path,
        transcript,
        seam,
        recognitions,
        policy,
    ) = build_fixture(tmp_path)

    assert (
        assert_correction_audit_execution_plan(
            execution,
            audit_plan,
            signal_set,
            group_set,
            transcript,
            recognitions,
            policy,
            seam_evidence=seam,
            normalized_audio_path=audio_path,
        )
        == execution
    )

    forged = execution.model_copy(
        update={"generation_id": "generation-hostile"},
    )
    with pytest.raises(CorrectionExecutionError, match="not reproducible"):
        assert_correction_audit_execution_plan(
            forged,
            audit_plan,
            signal_set,
            group_set,
            transcript,
            recognitions,
            policy,
            seam_evidence=seam,
            normalized_audio_path=audio_path,
        )

    cross_generation = transcript.model_copy(update={"generation_id": "generation-other"})
    with pytest.raises(CorrectionExecutionError, match="AuditPlan differs"):
        build_correction_audit_execution_plan(
            audit_plan,
            signal_set,
            group_set,
            cross_generation,
            recognitions,
            policy,
            seam_evidence=seam,
            normalized_audio_path=audio_path,
        )


def test_execution_plan_rejects_reordered_missing_and_duplicate_topology(
    tmp_path: Path,
) -> None:
    _, _, _, execution, _, _, _, _, _ = build_fixture(tmp_path)

    for hostile_packets in (
        tuple(reversed(execution.packets)),
        execution.packets[:-1],
        (*execution.packets, execution.packets[-1]),
    ):
        payload = execution.model_dump(mode="python")
        payload["packets"] = hostile_packets
        payload_without_identity = {
            key: value for key, value in payload.items() if key not in {"id", "content_hash"}
        }
        payload["id"] = payload["content_hash"] = hash_object(payload_without_identity)
        with pytest.raises(ValidationError):
            CorrectionAuditExecutionPlanV2.model_validate(payload)

    bindings = execution.source_bindings
    for hostile_bindings in (
        tuple(reversed(bindings)),
        bindings[:-1],
        (*bindings, bindings[-1]),
    ):
        payload = execution.model_dump(mode="python")
        payload["source_bindings"] = hostile_bindings
        payload_without_identity = {
            key: value for key, value in payload.items() if key not in {"id", "content_hash"}
        }
        payload["id"] = payload["content_hash"] = hash_object(payload_without_identity)
        with pytest.raises(ValidationError):
            CorrectionAuditExecutionPlanV2.model_validate(payload)


def test_execution_plan_rejects_planner_drift_and_unsplittable_target(
    tmp_path: Path,
) -> None:
    (
        audit_plan,
        signal_set,
        group_set,
        _,
        audio_path,
        transcript,
        seam,
        recognitions,
        policy,
    ) = build_fixture(tmp_path)

    drifted = policy.model_copy(update={"planner_code_hash": "0" * 64})
    with pytest.raises(CorrectionExecutionError, match="planner identity drift"):
        build_correction_audit_execution_plan(
            audit_plan,
            signal_set,
            group_set,
            transcript,
            recognitions,
            drifted,
            seam_evidence=seam,
            normalized_audio_path=audio_path,
        )

    tiny = replace_execution_limits(
        policy,
        text_updates={"max_window_duration_ms": 1},
    )
    with pytest.raises(CorrectionExecutionError, match="unsplittable single audit target"):
        build_correction_audit_execution_plan(
            audit_plan,
            signal_set,
            group_set,
            transcript,
            recognitions,
            tiny,
            seam_evidence=seam,
            normalized_audio_path=audio_path,
        )


def test_truncated_or_hash_changed_audio_fails_closed(tmp_path: Path) -> None:
    (
        audit_plan,
        signal_set,
        group_set,
        _,
        audio_path,
        transcript,
        seam,
        recognitions,
        policy,
    ) = build_fixture(tmp_path)

    audio_path.write_bytes(audio_path.read_bytes()[:-4])
    with pytest.raises(CorrectionExecutionError, match="exact bytes differ"):
        build_correction_audit_execution_plan(
            audit_plan,
            signal_set,
            group_set,
            transcript,
            recognitions,
            policy,
            seam_evidence=seam,
            normalized_audio_path=audio_path,
        )


def test_packet_rejects_full_reference_receipt_artifact(tmp_path: Path) -> None:
    _, _, _, execution, _, _, _, _, _ = build_fixture(tmp_path)
    packet = next(item for item in execution.packets if item.modality == "text")
    payload = packet.model_dump(mode="python")
    payload["source_bindings"] = execution.source_bindings
    payload_without_identity = {
        key: value for key, value in payload.items() if key not in {"id", "content_hash"}
    }
    payload["id"] = payload["content_hash"] = hash_object(payload_without_identity)

    with pytest.raises(ValidationError, match="cannot expose full retrieval"):
        CorrectionAuditPacketV2.model_validate(payload)

    payload = packet.model_dump(mode="python")
    payload["source_bindings"] = tuple(
        item
        for item in execution.source_bindings
        if item.kind not in {"reference_retrieval_receipt_set", "normalized_audio"}
    )
    payload_without_identity = {
        key: value for key, value in payload.items() if key not in {"id", "content_hash"}
    }
    payload["id"] = payload["content_hash"] = hash_object(payload_without_identity)
    with pytest.raises(ValidationError, match="bounded packet slices"):
        CorrectionAuditPacketV2.model_validate(payload)


def test_packet_reference_excerpt_is_typed_least_context_and_uri_free(
    tmp_path: Path,
) -> None:
    _, _, _, execution, _, _, _, _, _ = build_fixture(
        tmp_path,
        with_references=True,
    )
    packets_with_reference = tuple(
        packet for packet in execution.packets if packet.reference_evidence_ids
    )
    assert packets_with_reference
    for packet in packets_with_reference:
        excerpt_bindings = tuple(
            item for item in packet.source_bindings if item.kind == "reference_excerpt"
        )
        assert tuple(item.id for item in excerpt_bindings) == packet.reference_evidence_ids
        packet_bytes = canonical_json_bytes(packet.source_bindings)
        assert b"file:///" not in packet_bytes
        assert b"C:\\\\private\\\\reference" not in packet_bytes
        assert b"book.txt" not in packet_bytes
        assert b"book.extracted.json" not in packet_bytes


@pytest.mark.parametrize(
    "malicious",
    (
        "file:///C:/private/book.txt",
        r"C:\private\book.txt",
        r"\\server\private\book.txt",
        "/private/book.txt",
        "https://private.example/book.txt",
        "chapter\x00secret",
    ),
)
def test_packet_reference_locator_rejects_uri_absolute_path_and_controls(
    malicious: str,
) -> None:
    value = malicious.replace("\\x00", "\x00")
    with pytest.raises(ValidationError, match="locator"):
        CorrectionPacketReferenceLocatorPartV2(kind="other", value=value)


def test_source_id_hash_swap_can_validate_but_exact_rebuild_rejects_it(
    tmp_path: Path,
) -> None:
    (
        audit_plan,
        signal_set,
        group_set,
        execution,
        audio_path,
        transcript,
        seam,
        recognitions,
        policy,
    ) = build_fixture(tmp_path)
    bindings = list(execution.source_bindings)
    left = bindings[0].model_dump(mode="python")
    right = bindings[1].model_dump(mode="python")
    left["content_hash"], right["content_hash"] = (
        right["content_hash"],
        left["content_hash"],
    )
    for index, binding_payload in ((0, left), (1, right)):
        binding_payload["binding_hash"] = hash_object(
            {key: value for key, value in binding_payload.items() if key != "binding_hash"}
        )
        bindings[index] = type(bindings[index]).model_validate(binding_payload)
    plan_payload = execution.model_dump(mode="python")
    plan_payload["source_bindings"] = tuple(bindings)
    plan_preimage = {
        key: value for key, value in plan_payload.items() if key not in {"id", "content_hash"}
    }
    plan_payload["id"] = plan_payload["content_hash"] = hash_object(plan_preimage)
    forged = CorrectionAuditExecutionPlanV2.model_validate(plan_payload)

    with pytest.raises(CorrectionExecutionError, match="not reproducible"):
        assert_correction_audit_execution_plan(
            forged,
            audit_plan,
            signal_set,
            group_set,
            transcript,
            recognitions,
            policy,
            seam_evidence=seam,
            normalized_audio_path=audio_path,
        )


def test_all_caps_apply_and_context_halo_is_deterministic(tmp_path: Path) -> None:
    policy = replace_execution_limits(
        default_correction_audit_execution_policy(),
        text_updates={"max_spans_per_packet": 3},
        audio_updates={"max_spans_per_packet": 3},
    )
    _, _, _, execution, _, _, _, _, _ = build_fixture(
        tmp_path,
        mandatory_boundaries=False,
        execution_policy=policy,
    )

    for modality, limits in (("text", policy.text), ("audio", policy.audio)):
        packets = tuple(item for item in execution.packets if item.modality == modality)
        assert len(packets) == 2
        assert packets[0].context_span_ids == ()
        assert packets[1].context_span_ids == (packets[0].owned_span_ids[-1],)
        for packet in packets:
            assert len((*packet.owned_cell_ids, *packet.context_cell_ids)) <= (
                limits.max_cells_per_packet
            )
            assert len((*packet.owned_span_ids, *packet.context_span_ids)) <= (
                limits.max_spans_per_packet
            )
            assert len((*packet.owned_token_ids, *packet.context_token_ids)) <= (
                limits.max_tokens_per_packet
            )
            assert packet.window_end_ms - packet.window_start_ms <= (limits.max_window_duration_ms)


def test_clip_derivation_uses_bounded_seek_reads_not_full_audio_per_packet(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_open = correction_execution_module.wave.open
    bytes_read: list[int] = []

    class ReadSpy:
        def __init__(self, path: str) -> None:
            self._stream = Path(path).open("rb")

        def read(self, size: int = -1) -> bytes:
            data = self._stream.read(size)
            bytes_read.append(len(data))
            return data

        def seek(self, offset: int, whence: int = 0) -> int:
            return self._stream.seek(offset, whence)

        def tell(self) -> int:
            return self._stream.tell()

        def close(self) -> None:
            self._stream.close()

    def spy_open(path: str, mode: str):  # type: ignore[no-untyped-def]
        if mode == "rb":
            return original_open(ReadSpy(path), mode)
        return original_open(path, mode)

    monkeypatch.setattr(correction_execution_module.wave, "open", spy_open)
    _, _, _, execution, audio_path, _, _, _, _ = build_fixture(tmp_path)

    audio_packet_count = sum(item.modality == "audio" for item in execution.packets)
    assert audio_packet_count == 2
    assert sum(bytes_read) < audio_path.stat().st_size
    assert max(bytes_read) < audio_path.stat().st_size // audio_packet_count


def test_audio_mutation_during_build_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audio, transcript, recognitions, seam = make_inputs()
    audio_path = tmp_path / "mutating.wav"
    audio_path.write_bytes(audio)
    audit_plan = build_audit_plan(
        transcript,
        recognitions,
        default_correction_audit_policy(),
        references_enrolled=False,
        seam_evidence=seam,
    )
    signals = derive_candidate_signal_set(
        audit_plan,
        transcript,
        recognitions,
        references_enrolled=False,
        seam_evidence=seam,
    )
    groups = derive_candidate_group_set(audit_plan, signals, transcript)
    policy = default_correction_audit_execution_policy()
    original_derive = correction_execution_module.derive_pcm_wav_clip_bytes
    mutated = False

    def mutate_after_clip(*args: object, **kwargs: object) -> tuple[bytes, int, int]:
        nonlocal mutated
        result = original_derive(*args, **kwargs)  # type: ignore[arg-type]
        if not mutated:
            changed = bytearray(audio_path.read_bytes())
            changed[-1] ^= 1
            audio_path.write_bytes(changed)
            mutated = True
        return result

    monkeypatch.setattr(
        correction_execution_module,
        "derive_pcm_wav_clip_bytes",
        mutate_after_clip,
    )
    with pytest.raises(CorrectionExecutionError, match="mutated during"):
        build_correction_audit_execution_plan(
            audit_plan,
            signals,
            groups,
            transcript,
            recognitions,
            policy,
            seam_evidence=seam,
            normalized_audio_path=audio_path,
        )
