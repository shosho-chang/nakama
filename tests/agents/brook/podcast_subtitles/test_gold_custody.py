from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import wave
from pathlib import Path
from unittest.mock import patch

import pytest

from agents.brook.podcast_subtitles.candidate_materialization import (
    build_source_bound_transcript_candidate,
)
from agents.brook.podcast_subtitles.gold_custody import (
    CustodyPhase,
    GoldCustodyWorkspace,
    load_gold_custody_event,
)
from agents.brook.podcast_subtitles.hashing import canonical_json_bytes, hash_object
from agents.brook.podcast_subtitles.recognition_request import RecognitionRequestArtifactV1
from agents.brook.podcast_subtitles.transcript_gold import (
    AdjudicationProvenance,
    AudioClipBinding,
    AudioOnlyTranscriptSubmission,
    CandidateClipOutput,
    GoldClipLabel,
    GoldToken,
    TranscriptAdjudicationRecord,
    TranscriptAnnotationPacket,
    TranscriptAnnotationProtocol,
    TranscriptCandidateArtifact,
    TranscriptEvaluationResult,
    TranscriptEvaluationStatus,
    TranscriptGoldSuite,
    load_annotation_packet,
    load_transcript_evaluation,
)
from shared.schemas.podcast_subtitles_v2 import ArtifactDigest, EvidenceToken, RecognitionEvidence


def _h(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _wav_bytes(frames: bytes, *, rate: int = 1_000) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(1)
        writer.setframerate(rate)
        writer.writeframes(frames)
    return output.getvalue()


def _audio_bytes() -> tuple[bytes, bytes]:
    frames = bytes(index % 251 for index in range(2_000))
    return _wav_bytes(frames), _wav_bytes(frames[1_000:2_000])


@pytest.fixture
def signing_keys(tmp_path: Path) -> tuple[Path, Path]:
    ssh_keygen = shutil.which("ssh-keygen")
    if ssh_keygen is None:
        pytest.fail("gold custody requires the trusted OpenSSH ssh-keygen executable")
    private_key = tmp_path / "trusted_test_ed25519"
    subprocess.run(
        [ssh_keygen, "-q", "-t", "ed25519", "-N", "", "-f", str(private_key)],
        check=True,
        capture_output=True,
    )
    return private_key, private_key.with_suffix(".pub")


def _artifacts() -> tuple[
    TranscriptAnnotationPacket,
    TranscriptCandidateArtifact,
    tuple[AudioOnlyTranscriptSubmission, AudioOnlyTranscriptSubmission],
    TranscriptAdjudicationRecord,
    TranscriptGoldSuite,
]:
    normalized_raw, clip_raw = _audio_bytes()
    clip = AudioClipBinding.build(
        clip_id="clip-1",
        start_ms=1_000,
        end_ms=2_000,
        clip_audio_hash=hashlib.sha256(clip_raw).hexdigest(),
        clip_audio_size_bytes=len(clip_raw),
        normalized_audio_hash=hashlib.sha256(normalized_raw).hexdigest(),
        normalized_audio_size_bytes=len(normalized_raw),
    )
    packet = TranscriptAnnotationPacket.build(
        packet_id="packet-1",
        episode_id="episode-1",
        normalized_audio_hash=clip.normalized_audio_hash,
        normalized_audio_size_bytes=clip.normalized_audio_size_bytes,
        instruction_profile_id="audio-only-transcript-v1",
        protocol=TranscriptAnnotationProtocol(protocol_id="two-pass-third-adjudication-v1"),
        clips=(clip,),
    )
    candidate = TranscriptCandidateArtifact.build(
        candidate_id="candidate-1",
        system_id="v2",
        generation_id="generation-1",
        annotation_packet=packet,
        generated_at_utc="2099-01-01T00:00:00Z",
        complete=True,
        clips=(
            CandidateClipOutput.build(
                clip=clip,
                outcome="accepted",
                text="安吉",
                tokens=("安吉",),
            ),
        ),
    )
    submissions = tuple(
        AudioOnlyTranscriptSubmission.build(
            submission_id=f"submission-{suffix}",
            annotation_packet_hash=packet.packet_hash,
            clip=clip,
            annotator_id=f"annotator-{suffix}",
            outcome="accepted",
            text="安吉",
            tokens=("安吉",),
        )
        for suffix in ("a", "b")
    )
    tokens = (GoldToken(token_id="token-1", text="安吉"),)
    adjudication = TranscriptAdjudicationRecord.build(
        record_id="adjudication-c",
        annotation_packet_hash=packet.packet_hash,
        clip=clip,
        first_pass_submission_hashes=tuple(  # type: ignore[arg-type]
            item.submission_hash for item in submissions
        ),
        adjudicator_id="adjudicator-c",
        spelling_authority_uses=(),
        final_expected_outcome="accepted",
        final_text="安吉",
        final_tokens=tokens,
    )
    gold = TranscriptGoldSuite.build(
        suite_id="gold-1",
        annotation_packet=packet,
        complete=True,
        labels=(
            GoldClipLabel(
                clip_id=clip.clip_id,
                expected_outcome="accepted",
                text="安吉",
                tokens=tokens,
                provenance=AdjudicationProvenance(
                    first_pass_submissions=submissions,  # type: ignore[arg-type]
                    adjudication_record=adjudication,
                ),
            ),
        ),
        # Deliberately conflicts with candidate.generated_at_utc.  The signed
        # custody chain, not these self-attested timestamps, establishes order.
        correction_targets_revealed_at_utc="2000-01-01T00:00:00Z",
    )
    return packet, candidate, submissions, adjudication, gold


def _write(path: Path, payload: bytes) -> Path:
    path.write_bytes(payload)
    return path


def test_source_bound_v3_candidate_is_replayed_on_seal_and_workspace_open(
    tmp_path: Path, signing_keys: tuple[Path, Path]
) -> None:
    packet_path, normalized, clips, *_ = _inputs(tmp_path)
    packet = load_annotation_packet(packet_path)
    clip = packet.clips[0]
    request_payload = {
        "schema_version": 1,
        "episode_id": packet.episode_id,
        "invocation_id": "clip-invocation",
        "normalized_audio_sha256": clip.clip_audio_hash,
        "normalized_audio_size_bytes": clip.clip_audio_size_bytes,
        "language_hint": "zh-TW",
        "context_policy_id": "nakama-verbatim-no-lexical-context-v1",
        "context_sha256": _h(""),
        "context_size_bytes": 0,
    }
    request = RecognitionRequestArtifactV1(
        **request_payload, content_hash=hash_object(request_payload)
    )
    evidence = RecognitionEvidence(
        episode_id=packet.episode_id,
        invocation_id=request.invocation_id,
        adapter="fixture",
        model="fixture-v1",
        language="zh-TW",
        config_hash=_h("config"),
        raw_output=ArtifactDigest(uri="digest://raw", sha256=_h("raw"), size_bytes=3),
        raw_output_hash=_h("raw"),
        normalized_audio_hash=clip.clip_audio_hash,
        tokens=(EvidenceToken(id="token", text="test", start_ms=0, end_ms=800),),
    )
    candidate = build_source_bound_transcript_candidate(
        candidate_id="candidate-v3", system_id="recognizer",
        annotation_packet=packet, requests=(request,), evidence=(evidence,),
    )
    candidate_path = _write(tmp_path / "candidate-v3.json", candidate.canonical_bytes())
    workspace = _initialize(tmp_path / "v3-custody", signing_keys)
    workspace.seal_packets(
        (packet_path,), normalized, clips, signing_key_path=signing_keys[0]
    )
    sealed = workspace.seal_candidates((candidate_path,), signing_key_path=signing_keys[0])
    reopened = GoldCustodyWorkspace.open(
        tmp_path / "v3-custody",
        public_key_path=signing_keys[1],
        expected_head_hash=sealed.current_head_hash,
    )
    assert reopened.verify().phase is CustodyPhase.CANDIDATES_SEALED

    tampered = json.loads(candidate.canonical_bytes())
    tampered["source_materializations"][0]["spans"][-1]["text"] = "fake"
    tampered_path = _write(tmp_path / "candidate-v3-tampered.json", canonical_json_bytes(tampered))
    other = _initialize(tmp_path / "v3-tamper-custody", signing_keys)
    other.seal_packets((packet_path,), normalized, clips, signing_key_path=signing_keys[0])
    with pytest.raises(ValueError):
        other.seal_candidates((tampered_path,), signing_key_path=signing_keys[0])


def _inputs(tmp_path: Path) -> tuple[
    Path,
    Path,
    tuple[Path, ...],
    Path,
    tuple[Path, Path],
    Path,
    Path,
]:
    packet, candidate, submissions, adjudication, gold = _artifacts()
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    normalized_raw, clip_raw = _audio_bytes()
    normalized_path = _write(inputs / "normalized.wav", normalized_raw)
    packet_audio_paths = tuple(
        _write(inputs / f"{clip.clip_id}.audio", clip_raw)
        for clip in packet.clips
    )
    rebound_clips = tuple(
        AudioClipBinding.build(
            clip_id=clip.clip_id,
            start_ms=clip.start_ms,
            end_ms=clip.end_ms,
            clip_audio_hash=hashlib.sha256(path.read_bytes()).hexdigest(),
            clip_audio_size_bytes=len(path.read_bytes()),
            normalized_audio_hash=clip.normalized_audio_hash,
            normalized_audio_size_bytes=clip.normalized_audio_size_bytes,
        )
        for clip, path in zip(packet.clips, packet_audio_paths, strict=True)
    )
    packet = TranscriptAnnotationPacket.build(
        packet_id=packet.packet_id,
        episode_id=packet.episode_id,
        normalized_audio_hash=packet.normalized_audio_hash,
        normalized_audio_size_bytes=packet.normalized_audio_size_bytes,
        instruction_profile_id=packet.instruction_profile_id,
        protocol=packet.protocol,
        clips=rebound_clips,
    )
    # Rebuild every dependent artifact against the exact clip bytes used by custody.
    _, original_candidate, original_submissions, _, original_gold = _artifacts()
    candidate = TranscriptCandidateArtifact.build(
        candidate_id=original_candidate.candidate_id,
        system_id=original_candidate.system_id,
        generation_id=original_candidate.generation_id,
        annotation_packet=packet,
        generated_at_utc=original_candidate.generated_at_utc,
        complete=True,
        clips=(
            CandidateClipOutput.build(
                clip=rebound_clips[0],
                outcome=original_candidate.clips[0].outcome,
                text=original_candidate.clips[0].text,
                tokens=original_candidate.clips[0].tokens,
            ),
        ),
    )
    submissions = tuple(
        AudioOnlyTranscriptSubmission.build(
            submission_id=item.submission_id,
            annotation_packet_hash=packet.packet_hash,
            clip=rebound_clips[0],
            annotator_id=item.annotator_id,
            outcome=item.outcome,
            text=item.text,
            tokens=item.tokens,
        )
        for item in original_submissions
    )
    adjudication = TranscriptAdjudicationRecord.build(
        record_id="adjudication-c",
        annotation_packet_hash=packet.packet_hash,
        clip=rebound_clips[0],
        first_pass_submission_hashes=tuple(  # type: ignore[arg-type]
            item.submission_hash for item in submissions
        ),
        adjudicator_id="adjudicator-c",
        spelling_authority_uses=(),
        final_expected_outcome="accepted",
        final_text="安吉",
        final_tokens=(GoldToken(token_id="token-1", text="安吉"),),
    )
    gold = TranscriptGoldSuite.build(
        suite_id=original_gold.suite_id,
        annotation_packet=packet,
        complete=True,
        labels=(
            GoldClipLabel(
                clip_id=rebound_clips[0].clip_id,
                expected_outcome="accepted",
                text="安吉",
                tokens=(GoldToken(token_id="token-1", text="安吉"),),
                provenance=AdjudicationProvenance(
                    first_pass_submissions=submissions,  # type: ignore[arg-type]
                    adjudication_record=adjudication,
                ),
            ),
        ),
        correction_targets_revealed_at_utc="2000-01-01T00:00:00Z",
    )
    return (
        _write(inputs / "packet.json", packet.canonical_bytes()),
        normalized_path,
        packet_audio_paths,
        _write(inputs / "candidate.json", candidate.canonical_bytes()),
        tuple(  # type: ignore[return-value]
            _write(inputs / f"submission-{index}.json", canonical_json_bytes(item))
            for index, item in enumerate(submissions)
        ),
        _write(inputs / "adjudication.json", canonical_json_bytes(adjudication)),
        _write(inputs / "gold.json", gold.canonical_bytes()),
    )


def _initialize(
    root: Path,
    signing_keys: tuple[Path, Path],
    *,
    workspace_id: str = "workspace-1",
) -> GoldCustodyWorkspace:
    private_key, public_key = signing_keys
    result = GoldCustodyWorkspace.initialize(
        root,
        workspace_id=workspace_id,
        signer_id="release-operator",
        signing_key_path=private_key,
        public_key_path=public_key,
    )
    return GoldCustodyWorkspace.open(
        root,
        public_key_path=public_key,
        expected_head_hash=result.current_head_hash,
    )


def _seal_through_gold(
    root: Path,
    signing_keys: tuple[Path, Path],
    inputs: tuple[Path, Path, tuple[Path, ...], Path, tuple[Path, Path], Path, Path],
) -> GoldCustodyWorkspace:
    private_key, _ = signing_keys
    packet, normalized, clip_audio, candidate, submissions, adjudication, gold = inputs
    workspace = _initialize(root, signing_keys)
    workspace.seal_packets(
        (packet,), normalized, clip_audio, signing_key_path=private_key
    )
    workspace.seal_candidates((candidate,), signing_key_path=private_key)
    workspace.import_submissions(
        submissions,
        (adjudication,),
        signing_key_path=private_key,
    )
    workspace.seal_gold((gold,), signing_key_path=private_key)
    return workspace


def test_signed_custody_lifecycle_exports_fresh_process_evaluation(
    tmp_path: Path, signing_keys: tuple[Path, Path]
) -> None:
    private_key, public_key = signing_keys
    root = tmp_path / "custody"
    workspace = _seal_through_gold(root, signing_keys, _inputs(tmp_path))

    # Re-open from disk before exporting: no in-memory chronology is trusted.
    expected_head = workspace.verify().events[-1].event_hash
    replay = GoldCustodyWorkspace.open(
        root,
        public_key_path=public_key,
        expected_head_hash=expected_head,
    )
    result = replay.export_evaluation(signing_key_path=private_key)

    assert result.phase.value == "evaluation_exported"
    assert result.evaluation_status == TranscriptEvaluationStatus.EVALUATED.value
    evaluation_paths = tuple(
        (root / "exports" / "evaluation").glob("*.transcript-evaluation.json")
    )
    assert len(evaluation_paths) == 1
    evaluation = load_transcript_evaluation(evaluation_paths[0])
    assert (
        evaluation.correction_metrics_status
        is TranscriptEvaluationStatus.NOT_EVALUATED
    )
    assert "source_authority_evidence_not_custodied" in (
        evaluation.correction_metrics_reason_codes
    )
    final_head = result.current_head_hash
    verified = GoldCustodyWorkspace.open(
        root,
        public_key_path=public_key,
        expected_head_hash=final_head,
    ).verify()
    assert verified.phase.value == "evaluation_exported"
    assert len(verified.events) == 6
    fresh = subprocess.run(
        [
            sys.executable,
            "scripts/podcast_subtitle_gold_custody.py",
            "verify",
            "--workspace",
            str(root),
            "--public-key",
            str(public_key),
            "--expected-head-hash",
            final_head,
        ],
        cwd=Path.cwd(),
        capture_output=True,
        check=False,
    )
    assert fresh.returncode == 0, fresh.stderr.decode()
    assert json.loads(fresh.stdout)["verification_status"] == "verified"


def test_custody_verify_fails_closed_after_lexical_evaluator_identity_drift(
    tmp_path: Path, signing_keys: tuple[Path, Path]
) -> None:
    private_key, public_key = signing_keys
    root = tmp_path / "custody"
    workspace = _seal_through_gold(root, signing_keys, _inputs(tmp_path))
    workspace.export_evaluation(signing_key_path=private_key)
    final_head = workspace.verify().events[-1].event_hash

    with patch(
        "agents.brook.podcast_subtitles.gold_custody.measure_lexical_evaluator_identity"
    ) as measured:
        evaluation_path = next(
            (root / "exports" / "evaluation").glob("*.transcript-evaluation.json")
        )
        stored = load_transcript_evaluation(evaluation_path)
        assert stored.metrics is not None
        measured.return_value = stored.metrics.lexical_evaluator_identity.model_copy(
            update={"python_version": "drifted-runtime"}
        )
        with pytest.raises(ValueError, match="lexical evaluator identity differs"):
            GoldCustodyWorkspace.open(
                root,
                public_key_path=public_key,
                expected_head_hash=final_head,
            )


def test_candidate_replacement_after_gold_and_noncanonical_bytes_are_rejected(
    tmp_path: Path, signing_keys: tuple[Path, Path]
) -> None:
    inputs = _inputs(tmp_path)
    root = tmp_path / "custody"
    workspace = _seal_through_gold(root, signing_keys, inputs)
    private_key, _ = signing_keys
    packet = _artifacts()[0]
    replacement = TranscriptCandidateArtifact.build(
        candidate_id="replacement",
        system_id="v2",
        generation_id="replacement",
        annotation_packet=packet,
        generated_at_utc="1900-01-01T00:00:00Z",
        complete=False,
        clips=(),
    )
    replacement_path = _write(
        tmp_path / "replacement.json",
        replacement.canonical_bytes(),
    )
    with pytest.raises(ValueError, match="replay conflicts"):
        workspace.seal_candidates((replacement_path,), signing_key_path=private_key)

    noncanonical = _write(
        tmp_path / "noncanonical.json",
        inputs[0].read_bytes() + b"\n",
    )
    other = _initialize(tmp_path / "other", signing_keys, workspace_id="other")
    with pytest.raises(ValueError, match="exact canonical JSON"):
        other.seal_packets(
            (noncanonical,),
            inputs[1],
            inputs[2],
            signing_key_path=private_key,
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "event",
        "signature",
        "signature_newline",
        "signature_junk",
        "signature_nul",
        "object",
        "extra",
        "delete",
        "reorder",
        "extra_event",
    ],
)
def test_event_signature_object_and_ledger_tampering_fail_closed(
    tmp_path: Path,
    signing_keys: tuple[Path, Path],
    mutation: str,
) -> None:
    root = tmp_path / "custody"
    workspace = _seal_through_gold(root, signing_keys, _inputs(tmp_path))
    expected_head = workspace.verify().events[-1].event_hash
    _, public_key = signing_keys
    event_dirs = sorted((root / "events").iterdir())
    candidate_event_dir = event_dirs[2]
    candidate_event = load_gold_custody_event(candidate_event_dir / "event.json")
    if mutation == "event":
        payload = json.loads((candidate_event_dir / "event.json").read_bytes())
        payload["signer_id"] = "attacker"
        (candidate_event_dir / "event.json").write_bytes(canonical_json_bytes(payload))
    elif mutation == "signature":
        signature = bytearray((candidate_event_dir / "signature.sshsig").read_bytes())
        signature[-2] ^= 1
        (candidate_event_dir / "signature.sshsig").write_bytes(signature)
    elif mutation.startswith("signature_"):
        suffix = {
            "signature_newline": b"\n",
            "signature_junk": b"JUNK",
            "signature_nul": b"\0",
        }[mutation]
        signature_path = candidate_event_dir / "signature.sshsig"
        signature_path.write_bytes(signature_path.read_bytes() + suffix)
    elif mutation == "object":
        reference = candidate_event.artifacts[0]
        object_path = root / reference.object_relpath
        raw = bytearray(object_path.read_bytes())
        raw[-1] ^= 1
        object_path.write_bytes(raw)
    elif mutation == "extra":
        (candidate_event_dir / "extra.json").write_text("{}", encoding="utf-8")
    elif mutation == "delete":
        candidate_event_dir.rename(tmp_path / "deleted-event")
    elif mutation == "reorder":
        candidate_event_dir.rename(
            candidate_event_dir.with_name(candidate_event_dir.name.replace("000002", "000099"))
        )
    else:
        shutil.copytree(
            event_dirs[0],
            (root / "events" / event_dirs[0].name.replace("000000", "999999")),
        )
    with pytest.raises(ValueError):
        GoldCustodyWorkspace.open(
            root,
            public_key_path=public_key,
            expected_head_hash=expected_head,
        )


def test_public_key_and_cross_workspace_event_are_rejected(
    tmp_path: Path, signing_keys: tuple[Path, Path]
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    _initialize(first, signing_keys, workspace_id="first")
    _initialize(second, signing_keys, workspace_id="second")
    event = next((first / "events").iterdir())
    target = next((second / "events").iterdir())
    target.rename(tmp_path / "old-event")
    shutil.copytree(event, second / "events" / event.name)
    with pytest.raises(ValueError, match="cross-workspace"):
        GoldCustodyWorkspace.open(
            second,
            public_key_path=signing_keys[1],
            expected_head_hash=load_gold_custody_event(event / "event.json").event_hash,
        )

    other_private, other_public = _make_keypair(tmp_path / "other-key")
    assert other_private.exists()
    with pytest.raises(ValueError, match="external trusted key"):
        GoldCustodyWorkspace.open(
            first,
            public_key_path=other_public,
            expected_head_hash=load_gold_custody_event(event / "event.json").event_hash,
        )


def test_deleting_terminal_event_does_not_roll_workspace_back_silently(
    tmp_path: Path, signing_keys: tuple[Path, Path]
) -> None:
    root = tmp_path / "custody"
    workspace = _seal_through_gold(root, signing_keys, _inputs(tmp_path))
    result = workspace.export_evaluation(signing_key_path=signing_keys[0])
    terminal = sorted((root / "events").iterdir())[-1]
    terminal.rename(tmp_path / "deleted-terminal-event")

    with pytest.raises(ValueError, match="external expected_head_hash"):
        GoldCustodyWorkspace.open(
            root,
            public_key_path=signing_keys[1],
            expected_head_hash=result.current_head_hash,
        )


def test_full_tail_object_and_export_truncation_needs_external_head(
    tmp_path: Path, signing_keys: tuple[Path, Path]
) -> None:
    root = tmp_path / "custody"
    workspace = _seal_through_gold(root, signing_keys, _inputs(tmp_path))
    result = workspace.export_evaluation(signing_key_path=signing_keys[0])
    terminal_dir = sorted((root / "events").iterdir())[-1]
    terminal = load_gold_custody_event(terminal_dir / "event.json")
    truncated = tmp_path / "truncated-tail"
    truncated.mkdir()
    terminal_dir.rename(truncated / terminal_dir.name)
    for index, reference in enumerate(terminal.artifacts):
        source = root / reference.object_relpath
        source.rename(truncated / f"object-{index}")
    (root / "exports" / "evaluation").rename(truncated / "evaluation")

    with pytest.raises(ValueError, match="external expected_head_hash"):
        GoldCustodyWorkspace.open(
            root,
            public_key_path=signing_keys[1],
            expected_head_hash=result.current_head_hash,
        )


def test_signed_but_nonrecomputed_evaluation_is_rejected(
    tmp_path: Path,
    signing_keys: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import agents.brook.podcast_subtitles.gold_custody as custody_module

    root = tmp_path / "custody"
    workspace = _seal_through_gold(root, signing_keys, _inputs(tmp_path))
    original = custody_module._evaluate_with_signed_custody

    def forge(
        suite: TranscriptGoldSuite,
        candidate: TranscriptCandidateArtifact,
        *,
        all_candidate_hashes: tuple[str, ...],
    ) -> TranscriptEvaluationResult:
        genuine = original(
            suite,
            candidate,
            all_candidate_hashes=all_candidate_hashes,
        )
        return TranscriptEvaluationResult.build(
            status=genuine.status,
            reason_codes=genuine.reason_codes,
            gold_suite_hash=genuine.gold_suite_hash,
            candidate_artifact_hash=genuine.candidate_artifact_hash,
            normalized_audio_hash=genuine.normalized_audio_hash,
            annotation_packet_hash=genuine.annotation_packet_hash,
            metrics=genuine.metrics,
            correction_metrics_status=TranscriptEvaluationStatus.NOT_EVALUATED,
            correction_metrics_reason_codes=("forged_without_recomputation",),
            clip_results=genuine.clip_results,
        )

    monkeypatch.setattr(custody_module, "_evaluate_with_signed_custody", forge)
    result = workspace.export_evaluation(signing_key_path=signing_keys[0])
    monkeypatch.setattr(custody_module, "_evaluate_with_signed_custody", original)

    with pytest.raises(ValueError, match="deterministic sealed-artifact replay"):
        GoldCustodyWorkspace.open(
            root,
            public_key_path=signing_keys[1],
            expected_head_hash=result.current_head_hash,
        )


def test_hard_link_temporary_residue_requires_explicit_safe_recovery(
    tmp_path: Path, signing_keys: tuple[Path, Path]
) -> None:
    packet, normalized, clips, *_ = _inputs(tmp_path)
    workspace = _initialize(tmp_path / "custody", signing_keys)
    result = workspace.seal_packets(
        (packet,), normalized, clips, signing_key_path=signing_keys[0]
    )
    event = workspace.verify().events[-1]
    object_path = workspace.root / event.artifacts[0].object_relpath
    residue = object_path.parent / (
        f".custody-{hashlib.sha256(object_path.read_bytes()).hexdigest()}-process-killed"
    )
    os.link(object_path, residue)

    with pytest.raises(ValueError, match="temporary artifact"):
        workspace.verify()
    recovered = GoldCustodyWorkspace.open(
        workspace.root,
        public_key_path=signing_keys[1],
        expected_head_hash=result.current_head_hash,
        recover_incomplete_commit=True,
    )
    assert not residue.exists()
    assert recovered.verify().phase is CustodyPhase.PACKETS_SEALED


def _make_keypair(path: Path) -> tuple[Path, Path]:
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(path)],
        check=True,
        capture_output=True,
    )
    return path, path.with_suffix(".pub")


def test_partial_human_work_exports_typed_not_evaluated(
    tmp_path: Path, signing_keys: tuple[Path, Path]
) -> None:
    packet, candidate, submissions, _, _ = _artifacts()
    incomplete_gold = TranscriptGoldSuite.build(
        suite_id="incomplete",
        annotation_packet=packet,
        complete=False,
        labels=(),
        correction_targets_revealed_at_utc=None,
    )
    inputs = tmp_path / "partial-inputs"
    inputs.mkdir()
    normalized_raw, clip_raw = _audio_bytes()
    paths = (
        _write(inputs / "packet.json", packet.canonical_bytes()),
        _write(inputs / "normalized.wav", normalized_raw),
        (_write(inputs / "clip-1.wav", clip_raw),),
        _write(inputs / "candidate.json", candidate.canonical_bytes()),
        tuple(  # type: ignore[return-value]
            _write(inputs / f"submission-{index}.json", canonical_json_bytes(item))
            for index, item in enumerate(submissions)
        ),
        _write(inputs / "unused-adjudication.json", canonical_json_bytes(_artifacts()[3])),
        _write(inputs / "gold.json", incomplete_gold.canonical_bytes()),
    )
    private_key, public_key = signing_keys
    workspace = _initialize(tmp_path / "partial", signing_keys)
    workspace.seal_packets(
        (paths[0],), paths[1], paths[2], signing_key_path=private_key
    )
    workspace.seal_candidates((paths[3],), signing_key_path=private_key)
    workspace.import_submissions((), (), signing_key_path=private_key)
    workspace.seal_gold((paths[6],), signing_key_path=private_key)
    result = workspace.export_evaluation(signing_key_path=private_key)

    assert result.phase is CustodyPhase.EVALUATION_EXPORTED
    assert result.evaluation_status == TranscriptEvaluationStatus.NOT_EVALUATED.value
    assert GoldCustodyWorkspace.open(
        tmp_path / "partial",
        public_key_path=public_key,
        expected_head_hash=result.current_head_hash,
    ).verify().phase is CustodyPhase.EVALUATION_EXPORTED


def test_exact_phase_replay_is_idempotent(
    tmp_path: Path, signing_keys: tuple[Path, Path]
) -> None:
    packet, normalized, clips, candidate, submissions, adjudication, gold = _inputs(
        tmp_path
    )
    private_key, _ = signing_keys
    workspace = _initialize(tmp_path / "custody", signing_keys)
    phases = (
        lambda: workspace.seal_packets(
            (packet,), normalized, clips, signing_key_path=private_key
        ),
        lambda: workspace.seal_candidates((candidate,), signing_key_path=private_key),
        lambda: workspace.import_submissions(
            submissions, (adjudication,), signing_key_path=private_key
        ),
        lambda: workspace.seal_gold((gold,), signing_key_path=private_key),
        lambda: workspace.export_evaluation(signing_key_path=private_key),
    )
    for operation in phases:
        first = operation()
        second = operation()
        assert second.phase_event_hash == first.phase_event_hash
        assert second.current_head_hash == first.current_head_hash
        assert second.replay_status == "replayed"


def test_cross_packet_cross_clip_and_nonindependent_humans_are_rejected(
    tmp_path: Path, signing_keys: tuple[Path, Path]
) -> None:
    packet, candidate, _, _, _ = _artifacts()
    private_key, _ = signing_keys
    packet_path = _write(tmp_path / "packet.json", packet.canonical_bytes())
    normalized_raw, clip_raw = _audio_bytes()
    normalized_path = _write(tmp_path / "normalized.wav", normalized_raw)
    clip_paths = (_write(tmp_path / "clip-1.wav", clip_raw),)
    candidate_path = _write(tmp_path / "candidate.json", candidate.canonical_bytes())
    workspace = _initialize(tmp_path / "custody", signing_keys)
    workspace.seal_packets(
        (packet_path,), normalized_path, clip_paths, signing_key_path=private_key
    )
    workspace.seal_candidates((candidate_path,), signing_key_path=private_key)

    other_clip = AudioClipBinding.build(
        clip_id="other-clip",
        start_ms=3_000,
        end_ms=4_000,
        clip_audio_hash=_h("other-clip"),
        clip_audio_size_bytes=1_000,
        normalized_audio_hash=packet.normalized_audio_hash,
        normalized_audio_size_bytes=packet.normalized_audio_size_bytes,
    )
    other_packet = TranscriptAnnotationPacket.build(
        packet_id="other-packet",
        episode_id=packet.episode_id,
        normalized_audio_hash=packet.normalized_audio_hash,
        normalized_audio_size_bytes=packet.normalized_audio_size_bytes,
        instruction_profile_id="audio-only-transcript-v1",
        protocol=packet.protocol,
        clips=(other_clip,),
    )
    cross_candidate = TranscriptCandidateArtifact.build(
        candidate_id="cross-candidate",
        system_id="v2",
        generation_id="cross-generation",
        annotation_packet=other_packet,
        complete=False,
        clips=(),
    )
    cross_candidate_path = _write(
        tmp_path / "cross-candidate.json",
        cross_candidate.canonical_bytes(),
    )
    candidate_workspace = _initialize(tmp_path / "cross-candidate-custody", signing_keys)
    candidate_workspace.seal_packets(
        (packet_path,), normalized_path, clip_paths, signing_key_path=private_key
    )
    with pytest.raises(ValueError, match="sealed annotation packet lineage"):
        candidate_workspace.seal_candidates(
            (cross_candidate_path,),
            signing_key_path=private_key,
        )
    cross_packet = AudioOnlyTranscriptSubmission.build(
        submission_id="cross-packet",
        annotation_packet_hash=other_packet.packet_hash,
        clip=other_clip,
        annotator_id="annotator-a",
        outcome="accepted",
        text="安吉",
        tokens=("安吉",),
    )
    cross_packet_path = _write(
        tmp_path / "cross-packet.json",
        canonical_json_bytes(cross_packet),
    )
    with pytest.raises(ValueError, match="cross-packet or cross-clip"):
        workspace.import_submissions(
            (cross_packet_path,),
            (),
            signing_key_path=private_key,
        )

    same_annotator = tuple(
        AudioOnlyTranscriptSubmission.build(
            submission_id=f"same-{index}",
            annotation_packet_hash=packet.packet_hash,
            clip=packet.clips[0],
            annotator_id="same-person",
            outcome="accepted",
            text="安吉",
            tokens=("安吉",),
        )
        for index in range(2)
    )
    same_paths = tuple(
        _write(tmp_path / f"same-{index}.json", canonical_json_bytes(value))
        for index, value in enumerate(same_annotator)
    )
    with pytest.raises(ValueError, match="distinct audio-only annotators"):
        workspace.import_submissions(same_paths, (), signing_key_path=private_key)

    distinct = _artifacts()[2]
    invalid_third = TranscriptAdjudicationRecord.build(
        record_id="invalid-third",
        annotation_packet_hash=packet.packet_hash,
        clip=packet.clips[0],
        first_pass_submission_hashes=tuple(  # type: ignore[arg-type]
            item.submission_hash for item in distinct
        ),
        adjudicator_id=distinct[0].annotator_id,
        spelling_authority_uses=(),
        final_expected_outcome="accepted",
        final_text="安吉",
        final_tokens=(GoldToken(token_id="token", text="安吉"),),
    )
    distinct_paths = tuple(
        _write(tmp_path / f"distinct-{index}.json", canonical_json_bytes(value))
        for index, value in enumerate(distinct)
    )
    third_path = _write(tmp_path / "invalid-third.json", canonical_json_bytes(invalid_third))
    with pytest.raises(ValueError, match="third person"):
        workspace.import_submissions(
            distinct_paths,
            (third_path,),
            signing_key_path=private_key,
        )


def test_annotation_export_is_exact_candidate_free_packet_only(
    tmp_path: Path, signing_keys: tuple[Path, Path]
) -> None:
    packet, _, _, _, _ = _artifacts()
    private_key, _ = signing_keys
    packet_path = _write(tmp_path / "packet.json", packet.canonical_bytes())
    normalized_raw, clip_raw = _audio_bytes()
    normalized_path = _write(tmp_path / "normalized.wav", normalized_raw)
    clip_paths = (_write(tmp_path / "clip-1.wav", clip_raw),)
    workspace = _initialize(tmp_path / "custody", signing_keys)
    workspace.seal_packets(
        (packet_path,), normalized_path, clip_paths, signing_key_path=private_key
    )

    exported = tuple((tmp_path / "custody" / "exports" / "annotation").iterdir())
    assert len(exported) == 3
    packet_export = next(
        item for item in exported if item.name.endswith(".annotation-packet.json")
    )
    assert packet_export.read_bytes() == packet.canonical_bytes()
    payload = json.loads(packet_export.read_bytes())
    assert set(payload) == set(TranscriptAnnotationPacket.model_fields)
    forbidden = {
        "candidate_artifact",
        "candidate_output",
        "qc_output",
        "reference_literal",
        "correction_target",
        "gold_target",
    }
    assert not forbidden.intersection(payload)


@pytest.mark.parametrize("unsafe_id", ("../escape", "slash/name", "CON", "trailing."))
def test_untrusted_clip_id_never_controls_export_paths(
    tmp_path: Path,
    signing_keys: tuple[Path, Path],
    unsafe_id: str,
) -> None:
    normalized_raw, clip_raw = _audio_bytes()
    clip = AudioClipBinding.build(
        clip_id=unsafe_id,
        start_ms=1_000,
        end_ms=2_000,
        clip_audio_hash=hashlib.sha256(clip_raw).hexdigest(),
        clip_audio_size_bytes=len(clip_raw),
        normalized_audio_hash=hashlib.sha256(normalized_raw).hexdigest(),
        normalized_audio_size_bytes=len(normalized_raw),
    )
    packet = TranscriptAnnotationPacket.build(
        packet_id="unsafe-id-packet",
        episode_id="episode-1",
        normalized_audio_hash=clip.normalized_audio_hash,
        normalized_audio_size_bytes=clip.normalized_audio_size_bytes,
        instruction_profile_id="audio-only-transcript-v1",
        protocol=TranscriptAnnotationProtocol(
            protocol_id="two-pass-third-adjudication-v1"
        ),
        clips=(clip,),
    )
    packet_path = _write(tmp_path / "unsafe-packet.json", packet.canonical_bytes())
    normalized_path = _write(tmp_path / "unsafe-normalized.wav", normalized_raw)
    clip_path = _write(tmp_path / "unsafe-clip.wav", clip_raw)
    workspace = _initialize(tmp_path / "unsafe-custody", signing_keys)
    workspace.seal_packets(
        (packet_path,),
        normalized_path,
        (clip_path,),
        signing_key_path=signing_keys[0],
    )
    names = {
        item.name for item in (workspace.root / "exports" / "annotation").iterdir()
    }
    assert all(unsafe_id not in name for name in names)
    assert all(
        not item.is_dir()
        for item in (workspace.root / "exports" / "annotation").rglob("*")
    )


def test_wrong_normalized_source_and_participant_aliases_fail_closed(
    tmp_path: Path, signing_keys: tuple[Path, Path]
) -> None:
    packet, normalized, clips, candidate, *_ = _inputs(tmp_path)
    wrong_normalized = _write(tmp_path / "wrong-normalized.wav", _wav_bytes(b"x" * 2_000))
    workspace = _initialize(tmp_path / "wrong-source", signing_keys)
    with pytest.raises(ValueError, match="normalized audio bytes differ"):
        workspace.seal_packets(
            (packet,),
            wrong_normalized,
            clips,
            signing_key_path=signing_keys[0],
        )

    workspace = _initialize(tmp_path / "participant-alias", signing_keys)
    workspace.seal_packets(
        (packet,), normalized, clips, signing_key_path=signing_keys[0]
    )
    workspace.seal_candidates((candidate,), signing_key_path=signing_keys[0])
    packet_value = _artifacts()[0]
    for index, alias in enumerate(("Alice", "alice ")):
        submission = AudioOnlyTranscriptSubmission.build(
            submission_id=f"alias-{index}",
            annotation_packet_hash=packet_value.packet_hash,
            clip=packet_value.clips[0],
            annotator_id=alias,
            outcome="accepted",
            text="å®‰å‰",
            tokens=("å®‰å‰",),
        )
        path = _write(tmp_path / f"alias-{index}.json", canonical_json_bytes(submission))
        with pytest.raises(ValueError, match="canonical lowercase opaque"):
            workspace.import_submissions((path,), (), signing_key_path=signing_keys[0])


def test_duplicate_clip_bytes_are_bound_by_distinct_binding_hashes(
    tmp_path: Path, signing_keys: tuple[Path, Path]
) -> None:
    frames = bytes(index % 251 for index in range(2_000))
    normalized_raw = _wav_bytes(frames + frames)
    duplicate_raw = _wav_bytes(frames[1_000:2_000])
    normalized_hash = hashlib.sha256(normalized_raw).hexdigest()
    clips = tuple(
        AudioClipBinding.build(
            clip_id=f"duplicate-{index}",
            start_ms=start,
            end_ms=end,
            clip_audio_hash=hashlib.sha256(duplicate_raw).hexdigest(),
            clip_audio_size_bytes=len(duplicate_raw),
            normalized_audio_hash=normalized_hash,
            normalized_audio_size_bytes=len(normalized_raw),
        )
        for index, (start, end) in enumerate(((1_000, 2_000), (3_000, 4_000)))
    )
    packet = TranscriptAnnotationPacket.build(
        packet_id="duplicate-bytes",
        episode_id="episode-1",
        normalized_audio_hash=normalized_hash,
        normalized_audio_size_bytes=len(normalized_raw),
        instruction_profile_id="audio-only-transcript-v1",
        protocol=TranscriptAnnotationProtocol(
            protocol_id="two-pass-third-adjudication-v1"
        ),
        clips=clips,
    )
    packet_path = _write(tmp_path / "duplicate-packet.json", packet.canonical_bytes())
    normalized_path = _write(tmp_path / "duplicate-normalized.wav", normalized_raw)
    clip_paths = tuple(
        _write(tmp_path / f"duplicate-{index}.wav", duplicate_raw) for index in range(2)
    )
    workspace = _initialize(tmp_path / "duplicate-custody", signing_keys)
    result = workspace.seal_packets(
        (packet_path,),
        normalized_path,
        clip_paths,
        signing_key_path=signing_keys[0],
    )
    event = workspace.verify().events[-1]
    audio_refs = [item for item in event.artifacts if item.role == "annotation_audio_clip"]
    assert len(audio_refs) == 2
    assert len({item.artifact_id for item in audio_refs}) == 2
    assert len({item.content_sha256 for item in audio_refs}) == 1
    assert result.current_head_hash == event.event_hash


def test_every_phase_recovers_idempotently_after_crash_before_event_commit(
    tmp_path: Path,
    signing_keys: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import agents.brook.podcast_subtitles.gold_custody as custody_module

    packet, normalized, clips, candidate, submissions, adjudication, gold = _inputs(
        tmp_path
    )
    private_key, public_key = signing_keys
    root = tmp_path / "custody"
    workspace = _initialize(root, signing_keys)
    original = custody_module._commit_event_directory

    def crash(*_args: object, **_kwargs: object) -> None:
        raise OSError("injected crash before event commit")

    operations = (
        lambda value: value.seal_packets(
            (packet,), normalized, clips, signing_key_path=private_key
        ),
        lambda value: value.seal_candidates((candidate,), signing_key_path=private_key),
        lambda value: value.import_submissions(
            submissions,
            (adjudication,),
            signing_key_path=private_key,
        ),
        lambda value: value.seal_gold((gold,), signing_key_path=private_key),
        lambda value: value.export_evaluation(signing_key_path=private_key),
    )
    for expected_phase, operation in zip(
        (
            CustodyPhase.PACKETS_SEALED,
            CustodyPhase.CANDIDATES_SEALED,
            CustodyPhase.SUBMISSIONS_IMPORTED,
            CustodyPhase.GOLD_SEALED,
            CustodyPhase.EVALUATION_EXPORTED,
        ),
        operations,
        strict=True,
    ):
        prior_head = workspace.verify().events[-1].event_hash
        monkeypatch.setattr(custody_module, "_commit_event_directory", crash)
        with pytest.raises(OSError, match="injected crash"):
            operation(workspace)
        monkeypatch.setattr(custody_module, "_commit_event_directory", original)
        with pytest.raises(ValueError, match="uncommitted or extra object"):
            GoldCustodyWorkspace.open(
                root,
                public_key_path=public_key,
                expected_head_hash=prior_head,
            )
        workspace = GoldCustodyWorkspace.open(
            root,
            public_key_path=public_key,
            expected_head_hash=prior_head,
            recover_incomplete_commit=True,
        )
        result = operation(workspace)
        assert result.phase is expected_phase
        assert result.replay_status == "committed"
    assert len(workspace.verify().events) == 6


def test_exact_successor_recovers_after_event_commit_before_head_persist(
    tmp_path: Path,
    signing_keys: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import agents.brook.podcast_subtitles.gold_custody as custody_module

    packet, normalized, clips, *_ = _inputs(tmp_path)
    root = tmp_path / "successor-custody"
    workspace = _initialize(root, signing_keys)
    prior_head = workspace.verify().events[-1].event_hash
    original = custody_module._commit_event_directory

    def commit_then_crash(*args: object, **kwargs: object) -> None:
        original(*args, **kwargs)
        raise OSError("injected crash after event commit")

    monkeypatch.setattr(custody_module, "_commit_event_directory", commit_then_crash)
    with pytest.raises(OSError, match="after event commit"):
        workspace.seal_packets(
            (packet,), normalized, clips, signing_key_path=signing_keys[0]
        )
    monkeypatch.setattr(custody_module, "_commit_event_directory", original)

    with pytest.raises(ValueError, match="external expected_head_hash"):
        GoldCustodyWorkspace.open(
            root,
            public_key_path=signing_keys[1],
            expected_head_hash=prior_head,
        )
    recovered = GoldCustodyWorkspace.open(
        root,
        public_key_path=signing_keys[1],
        expected_head_hash=prior_head,
        recover_incomplete_commit=True,
    )
    result = recovered.seal_packets(
        (packet,), normalized, clips, signing_key_path=signing_keys[0]
    )
    assert result.replay_status == "replayed"
    assert result.phase is CustodyPhase.PACKETS_SEALED
    assert result.current_head_hash == result.phase_event_hash

    other_candidate = _write(tmp_path / "candidate.json", _artifacts()[1].canonical_bytes())
    stale = GoldCustodyWorkspace.open(
        root,
        public_key_path=signing_keys[1],
        expected_head_hash=prior_head,
        recover_incomplete_commit=True,
    )
    with pytest.raises(ValueError, match="recovery successor differs"):
        stale.seal_candidates((other_candidate,), signing_key_path=signing_keys[0])


def test_cli_help_and_machine_readable_init_output(
    tmp_path: Path, signing_keys: tuple[Path, Path]
) -> None:
    script = Path("scripts/podcast_subtitle_gold_custody.py")
    help_result = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=Path.cwd(),
        check=False,
        capture_output=True,
        text=True,
    )
    assert help_result.returncode == 0
    for command in (
        "init",
        "seal-packets",
        "seal-candidates",
        "import-submissions",
        "seal-gold",
        "export-evaluation",
    ):
        assert command in help_result.stdout

    private_key, public_key = signing_keys
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "init",
            "--workspace",
            str(tmp_path / "cli-custody"),
            "--public-key",
            str(public_key),
            "--signing-key",
            str(private_key),
            "--workspace-id",
            "cli-workspace",
            "--signer-id",
            "cli-signer",
        ],
        cwd=Path.cwd(),
        check=False,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr.decode()
    output = json.loads(result.stdout)
    assert output["phase"] == CustodyPhase.INITIALIZED.value
    assert output["replay_status"] == "committed"
