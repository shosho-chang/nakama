from __future__ import annotations

import json
import os
import struct
import subprocess
import sys
import tracemalloc
import wave
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

import agents.brook.podcast_subtitles.paired_study_artifacts as paired_artifacts
from agents.brook.podcast_subtitles.benchmark import (
    GateStatus,
    evaluate_paired_boundary_study,
    load_paired_boundary_study,
)
from agents.brook.podcast_subtitles.hashing import canonical_json_bytes, hash_file, hash_object
from agents.brook.podcast_subtitles.paired_study_artifacts import (
    CandidateClipInput,
    CandidateInput,
    GeneratorIdentity,
    MaterializeRequest,
    PredeclareRequest,
    SealHumanLabelsRequest,
    materialize_candidates_and_commit_mapping,
    predeclare,
    reveal_sealed_human_labels,
    seal_human_labels,
    seal_human_labels_and_reveal,
    verify_blinded_workspace,
)


def _generator_identity(system: str) -> GeneratorIdentity:
    return GeneratorIdentity(
        schema_version=1,
        system=system,  # type: ignore[arg-type]
        generator_id=f"frozen-{system}-boundary-generator",
        code_hash=hash_object({"code": system}),
        config_hash=hash_object({"config": system}),
        model_identity_hash=hash_object({"model": system}),
        generation_protocol_id="canonical-generation-v1",
        projection_protocol_id=f"boundary-projection-{system}-v1",
    )


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(value))


def _write_wav(path: Path, *, seconds: int = 80, sample_rate: int = 1000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = bytes(((index % 255) for index in range(seconds * sample_rate)))
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(1)
        writer.setframerate(sample_rate)
        writer.writeframes(pcm)


def _srt(cue_size_ms: int) -> bytes:
    def stamp(value: int) -> str:
        hours, remainder = divmod(value, 3_600_000)
        minutes, remainder = divmod(remainder, 60_000)
        seconds, millis = divmod(remainder, 1_000)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"

    lines: list[str] = []
    for index in range(40):
        start = index * 2_000
        end = start + 2_000
        lines.extend(
            [
                str(index + 1),
                f"{stamp(start)} --> {stamp(end)}",
                "字字" if cue_size_ms == 2_000 else "字字",
                "",
            ]
        )
    return "\n".join(lines).encode()


def _parse_srt_for_clip(raw: bytes, clip_id: str, start_ms: int, end_ms: int) -> dict:
    blocks = raw.decode().strip().split("\n\n")
    cues = []
    for block in blocks:
        lines = block.splitlines()
        index = int(lines[0])
        cue_start = (index - 1) * 2_000
        cue_end = cue_start + 2_000
        if cue_end > start_ms and cue_start < end_ms:
            cues.append(
                {
                    "cue_id": str(index),
                    "start_ms": cue_start,
                    "end_ms": cue_end,
                    "text": "字字",
                }
            )
    return {"schema_version": 1, "clip_id": clip_id, "cues": cues}


def _prepare(tmp_path: Path):
    audio = tmp_path / "normalized.wav"
    _write_wav(audio)
    frame = {
        "schema_version": 1,
        "episode_id": "episode-1",
        "normalized_audio_hash": hash_file(audio),
        "sample_rate_hz": 1000,
        "channel_count": 1,
        "sample_width_bytes": 1,
        "frame_count": 80_000,
        "selection_algorithm": "frozen_stratified_nonoverlap_v1",
        "selection_count": 20,
        "eligible_windows": [
            {
                "window_id": f"window-{index:02d}",
                "start_frame": index * 4_000,
                "end_frame": index * 4_000 + 4_000,
                "stratum": "even" if index % 2 == 0 else "odd",
            }
            for index in range(20)
        ],
    }
    frame_path = tmp_path / "sampling-frame.json"
    _write_json(frame_path, frame)
    workspace = tmp_path / "study"
    v1_root = tmp_path / "candidate-one"
    v2_root = tmp_path / "candidate-two"
    pre_req = PredeclareRequest(
        workspace_root=workspace,
        normalized_wav_path=audio,
        sampling_frame_path=frame_path,
        planned_v1_root=v1_root,
        planned_v2_root=v2_root,
        study_id="study-1",
        episode_id="episode-1",
        lineage_id="lineage-1",
        benchmark_suite_hash=hash_object({"suite": 1}),
        frozen_at_utc="2026-08-13T01:00:00Z",
        selection_seed="selection seed",
        selection_nonce="selection nonce",
        v1_generator=_generator_identity("v1"),
        v2_generator=_generator_identity("v2"),
    )
    declaration = predeclare(pre_req).predeclaration
    content = "字字" * 40
    canonical = {"schema_version": 1, "content": content}
    tokens = {
        "schema_version": 1,
        "tokens": [
            {"token_id": f"token-{index:03d}", "text": character}
            for index, character in enumerate(content)
        ],
    }
    subtitle = _srt(2_000)

    def candidate(root: Path, system: str, generated: str) -> CandidateInput:
        root.mkdir()
        _write_json(root / "canonical.json", canonical)
        _write_json(root / "tokens.json", tokens)
        (root / "subtitle.srt").write_bytes(subtitle)
        generator = _generator_identity(system)
        provenance = {
            "schema_version": 1,
            "system": system,
            "generator_identity": generator.to_dict(),
            "generator_identity_hash": generator.identity_hash,
            "generation_id": "generation-one" if system == "v1" else "generation-two",
            "projection_id": "candidate-one-id" if system == "v1" else "candidate-two-id",
            "generated_at_utc": generated,
            "normalized_audio_hash": hash_file(audio),
            "canonical_content_hash": __import__("hashlib").sha256(content.encode()).hexdigest(),
            "token_sequence_hash": hash_object(
                [[item["token_id"], item["text"]] for item in tokens["tokens"]]
            ),
            "subtitle_bytes_hash": __import__("hashlib").sha256(subtitle).hexdigest(),
        }
        provenance["provenance_record_hash"] = hash_object(provenance)
        _write_json(root / "candidate.json", provenance)
        clip_inputs = []
        for clip in declaration.clips:
            _write_json(
                root / f"{clip.clip_id}.cues.json",
                _parse_srt_for_clip(subtitle, clip.clip_id, clip.start_ms, clip.end_ms),
            )
            clip_inputs.append(
                CandidateClipInput(
                    clip_id=clip.clip_id,
                    cue_set_relpath=f"{clip.clip_id}.cues.json",
                )
            )
        return CandidateInput(
            system=system,  # type: ignore[arg-type]
            input_root=root,
            normalized_wav_path=audio,
            candidate_artifact_relpath="candidate.json",
            subtitle_relpath="subtitle.srt",
            canonical_content_relpath="canonical.json",
            token_sequence_relpath="tokens.json",
            clip_inputs=tuple(clip_inputs),
        )

    v1 = candidate(v1_root, "v1", "2026-08-13T01:01:00Z")
    v2 = candidate(v2_root, "v2", "2026-08-13T01:02:00Z")
    materialize_req = MaterializeRequest(
        workspace_root=workspace,
        v1=v1,
        v2=v2,
        mapping_secret="mapping secret",
        committed_at_utc="2026-08-13T01:03:00Z",
    )
    materialized = materialize_candidates_and_commit_mapping(materialize_req)
    manifest = json.loads(materialized.blinded_manifest_path.read_text())
    labels = {
        "schema_version": 1,
        "study_id": "study-1",
        "mapping_commitment_hash": materialized.mapping_commitment_hash,
        "labels_created_by_humans": True,
        "candidate_identity_hidden_during_labelling": True,
        "model_generated": False,
        "operator_attestation": "The operator attests that these per-clip labels are human.",
        "judgements": [
            {
                "clip_id": item["clip_id"],
                "evaluator_id": "human-operator",
                "outcome": "a_better",
                "a_unacceptable": False,
                "b_unacceptable": False,
                "a_presentation_artifact_hash": item["A"]["presentation_artifact_hash"],
                "b_presentation_artifact_hash": item["B"]["presentation_artifact_hash"],
                "submitted_at_utc": f"2026-08-13T01:{10 + index:02d}:00Z",
            }
            for index, item in enumerate(manifest["clips"])
        ],
    }
    labels_path = tmp_path / "human-labels.json"
    _write_json(labels_path, labels)
    return pre_req, materialize_req, labels_path, workspace


def test_legal_twenty_clip_fixture_round_trips_existing_loader_and_evaluator(
    tmp_path: Path,
) -> None:
    pre_req, materialize_req, labels_path, workspace = _prepare(tmp_path)
    verification = verify_blinded_workspace(workspace)
    assert verification.clip_count == 20

    sealed = seal_human_labels_and_reveal(
        SealHumanLabelsRequest(
            workspace_root=workspace,
            raw_human_labels_path=labels_path,
            labels_completed_at_utc="2026-08-13T01:31:00Z",
            revealed_at_utc="2026-08-13T01:32:00Z",
        )
    )
    loaded = load_paired_boundary_study(sealed.study_path)
    assert loaded.study_hash == sealed.study.study_hash
    assert evaluate_paired_boundary_study(loaded).status in {
        GateStatus.PASSED,
        GateStatus.FAILED,
    }

    assert predeclare(pre_req).predeclaration == sealed.study.predeclaration
    assert (
        materialize_candidates_and_commit_mapping(materialize_req).mapping_commitment_hash
        == sealed.study.mapping.commitment_hash
    )


def test_predeclare_rejects_candidate_existing_and_wav_drift(tmp_path: Path) -> None:
    pre_req, _, _, _ = _prepare(tmp_path)
    wrong_workspace = tmp_path / "wrong-study"
    wrong_v1 = tmp_path / "already-there"
    wrong_v1.mkdir()
    with pytest.raises(ValueError, match="must not exist before predeclare"):
        predeclare(
            replace(
                pre_req,
                workspace_root=wrong_workspace,
                planned_v1_root=wrong_v1,
                planned_v2_root=tmp_path / "future-two",
            )
        )

    with pre_req.normalized_wav_path.open("ab") as stream:
        stream.write(b"drift")
    with pytest.raises(ValueError, match="input bytes differ|WAV|source hash mismatch"):
        predeclare(pre_req)


def test_blind_workspace_rejects_extra_secret_or_tamper(tmp_path: Path) -> None:
    _, _, _, workspace = _prepare(tmp_path)
    (workspace / "blinded" / "mapping-secret.json").write_text("secret")
    with pytest.raises(ValueError, match="file set mismatch"):
        verify_blinded_workspace(workspace)


def test_fresh_process_verifier_needs_no_original_or_secret_and_blind_tree_is_opaque(
    tmp_path: Path,
) -> None:
    pre_req, materialize_req, _, workspace = _prepare(tmp_path)
    pre_req.normalized_wav_path.rename(tmp_path / "detached-normalized.wav")
    materialize_req.v1.input_root.rename(tmp_path / "detached-candidate-one")
    materialize_req.v2.input_root.rename(tmp_path / "detached-candidate-two")
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from pathlib import Path; "
                "from agents.brook.podcast_subtitles.paired_study_artifacts "
                "import verify_blinded_workspace; "
                f"assert verify_blinded_workspace(Path({str(workspace)!r})).clip_count == 20"
            ),
        ],
        cwd=Path(__file__).parents[4],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    blind = workspace / "blinded"
    paths = "\n".join(path.relative_to(blind).as_posix() for path in blind.rglob("*"))
    manifest = (blind / "manifest.json").read_text()
    assert "selection seed" not in manifest
    assert "mapping secret" not in manifest
    assert "candidate-one-id" not in manifest
    assert "candidate-two-id" not in manifest
    assert "private" not in paths.lower()
    assert "mapping" not in paths.lower()


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        (lambda payload: payload.update({"aggregate": {"a_wins": 20}}), "unknown"),
        (lambda payload: payload.update({"model_generated": True}), "model_generated"),
        (lambda payload: payload["judgements"].pop(), "each clip exactly once"),
        (
            lambda payload: payload["judgements"].append(payload["judgements"][0].copy()),
            "each clip exactly once",
        ),
    ],
)
def test_seal_rejects_aggregate_model_generated_incomplete_or_duplicate_labels(
    tmp_path: Path, mutation, match: str
) -> None:
    _, _, labels_path, workspace = _prepare(tmp_path)
    labels = json.loads(labels_path.read_text())
    mutation(labels)
    _write_json(labels_path, labels)
    with pytest.raises(ValueError, match=match):
        seal_human_labels_and_reveal(
            SealHumanLabelsRequest(
                workspace_root=workspace,
                raw_human_labels_path=labels_path,
                labels_completed_at_utc="2026-08-13T01:31:00Z",
                revealed_at_utc="2026-08-13T01:32:00Z",
            )
        )


def test_reveal_before_label_seal_and_candidate_lineage_drift_fail_closed(tmp_path: Path) -> None:
    _, materialize_req, labels_path, workspace = _prepare(tmp_path)
    with pytest.raises(ValueError, match="after labels are sealed"):
        seal_human_labels_and_reveal(
            SealHumanLabelsRequest(
                workspace_root=workspace,
                raw_human_labels_path=labels_path,
                labels_completed_at_utc="2026-08-13T01:31:00Z",
                revealed_at_utc="2026-08-13T01:30:00Z",
            )
        )

    (materialize_req.v1.input_root / "subtitle.srt").write_bytes(b"1\ncorrupt")
    with pytest.raises(ValueError, match="subtitle|drifted"):
        materialize_candidates_and_commit_mapping(materialize_req)


def test_label_seal_is_durable_terminal_before_reveal_and_rejects_changed_votes(
    tmp_path: Path,
) -> None:
    _, _, labels_path, workspace = _prepare(tmp_path)
    sealed = seal_human_labels(
        workspace_root=workspace,
        raw_human_labels_path=labels_path,
        labels_completed_at_utc="2026-08-13T01:31:00Z",
    )
    assert sealed.labels_snapshot_path.is_file()
    assert sealed.seal_anchor_path.is_file()
    assert not (workspace / "sealed" / "studies").exists()
    assert not (workspace / "sealed" / "reveal-anchor.json").exists()
    private_bytes = b"".join(
        path.read_bytes() for path in (workspace / "sealed").rglob("*") if path.is_file()
    )
    assert b"a_candidate_record_hash" not in private_bytes
    assert b"mapping_secret" not in private_bytes

    changed = json.loads(labels_path.read_text())
    changed["judgements"][0]["outcome"] = "b_better"
    _write_json(labels_path, changed)
    with pytest.raises(ValueError, match="sealed|conflict|labels"):
        seal_human_labels(
            workspace_root=workspace,
            raw_human_labels_path=labels_path,
            labels_completed_at_utc="2026-08-13T01:31:00Z",
        )
    with pytest.raises(ValueError, match="sealed|conflict|labels"):
        reveal_sealed_human_labels(
            workspace_root=workspace,
            raw_human_labels_path=labels_path,
            revealed_at_utc="2026-08-13T01:32:00Z",
        )


def test_label_seal_crash_before_or_after_anchor_has_deterministic_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, labels_path, workspace = _prepare(tmp_path)
    original_write = paired_artifacts._atomic_write_immutable
    seal_anchor = workspace / "sealed" / "label-seal-anchor.json"

    def crash_before(path: Path, data: bytes, *, workspace_root: Path) -> None:
        if path.absolute() == seal_anchor.absolute():
            raise RuntimeError("injected-before-seal-anchor")
        original_write(path, data, workspace_root=workspace_root)

    monkeypatch.setattr(paired_artifacts, "_atomic_write_immutable", crash_before)
    with pytest.raises(RuntimeError, match="before-seal-anchor"):
        seal_human_labels(
            workspace_root=workspace,
            raw_human_labels_path=labels_path,
            labels_completed_at_utc="2026-08-13T01:31:00Z",
        )
    assert not seal_anchor.exists()

    def crash_after(path: Path, data: bytes, *, workspace_root: Path) -> None:
        if "/sealed/labels/" in path.as_posix():
            raise RuntimeError("injected-after-seal-anchor")
        original_write(path, data, workspace_root=workspace_root)

    monkeypatch.setattr(paired_artifacts, "_atomic_write_immutable", crash_after)
    with pytest.raises(RuntimeError, match="after-seal-anchor"):
        seal_human_labels(
            workspace_root=workspace,
            raw_human_labels_path=labels_path,
            labels_completed_at_utc="2026-08-13T01:31:00Z",
        )
    assert seal_anchor.is_file()

    original_labels = labels_path.read_bytes()
    changed = json.loads(original_labels)
    changed["judgements"][0]["outcome"] = "b_better"
    _write_json(labels_path, changed)
    monkeypatch.setattr(paired_artifacts, "_atomic_write_immutable", original_write)
    with pytest.raises(ValueError, match="conflict"):
        seal_human_labels(
            workspace_root=workspace,
            raw_human_labels_path=labels_path,
            labels_completed_at_utc="2026-08-13T01:31:00Z",
        )
    labels_path.write_bytes(original_labels)
    assert seal_human_labels(
        workspace_root=workspace,
        raw_human_labels_path=labels_path,
        labels_completed_at_utc="2026-08-13T01:31:00Z",
    ).labels_snapshot_path.is_file()


def test_reveal_mid_crash_cannot_change_sealed_votes_and_replay_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, labels_path, workspace = _prepare(tmp_path)
    seal_human_labels(
        workspace_root=workspace,
        raw_human_labels_path=labels_path,
        labels_completed_at_utc="2026-08-13T01:31:00Z",
    )
    original_labels = labels_path.read_bytes()
    original_write = paired_artifacts._atomic_write_immutable

    def crash_during_reveal(path: Path, data: bytes, *, workspace_root: Path) -> None:
        if "/sealed/studies/" in path.as_posix():
            raise RuntimeError("injected-reveal-crash")
        original_write(path, data, workspace_root=workspace_root)

    monkeypatch.setattr(paired_artifacts, "_atomic_write_immutable", crash_during_reveal)
    with pytest.raises(RuntimeError, match="reveal-crash"):
        reveal_sealed_human_labels(
            workspace_root=workspace,
            raw_human_labels_path=labels_path,
            revealed_at_utc="2026-08-13T01:32:00Z",
        )
    assert (workspace / "sealed" / "reveal-anchor.json").is_file()

    changed = json.loads(original_labels)
    changed["judgements"][0]["outcome"] = "b_better"
    _write_json(labels_path, changed)
    monkeypatch.setattr(paired_artifacts, "_atomic_write_immutable", original_write)
    with pytest.raises(ValueError, match="immutable sealed labels"):
        reveal_sealed_human_labels(
            workspace_root=workspace,
            raw_human_labels_path=labels_path,
            revealed_at_utc="2026-08-13T01:32:00Z",
        )
    labels_path.write_bytes(original_labels)
    first = reveal_sealed_human_labels(
        workspace_root=workspace,
        raw_human_labels_path=labels_path,
        revealed_at_utc="2026-08-13T01:32:00Z",
    )
    second = reveal_sealed_human_labels(
        workspace_root=workspace,
        raw_human_labels_path=labels_path,
        revealed_at_utc="2026-08-13T01:32:00Z",
    )
    assert first.study.study_hash == second.study.study_hash


def test_predeclare_rejects_candidate_workspace_or_custody_root_overlap(tmp_path: Path) -> None:
    audio = tmp_path / "normalized.wav"
    _write_wav(audio)
    frame_path = tmp_path / "frame.json"
    _write_json(
        frame_path,
        {
            "schema_version": 1,
            "episode_id": "episode-1",
            "normalized_audio_hash": hash_file(audio),
            "sample_rate_hz": 1000,
            "channel_count": 1,
            "sample_width_bytes": 1,
            "frame_count": 80_000,
            "selection_algorithm": "frozen_stratified_nonoverlap_v1",
            "selection_count": 20,
            "eligible_windows": [
                {
                    "window_id": f"window-{index:02d}",
                    "start_frame": index * 4_000,
                    "end_frame": index * 4_000 + 4_000,
                    "stratum": "all",
                }
                for index in range(20)
            ],
        },
    )
    workspace = tmp_path / "study"
    base = PredeclareRequest(
        workspace_root=workspace,
        normalized_wav_path=audio,
        sampling_frame_path=frame_path,
        planned_v1_root=workspace / "private" / "candidate",
        planned_v2_root=tmp_path / "future-two",
        study_id="study-overlap",
        episode_id="episode-1",
        lineage_id="lineage-1",
        benchmark_suite_hash=hash_object({"suite": 1}),
        frozen_at_utc="2026-08-13T01:00:00Z",
        selection_seed="seed",
        selection_nonce="nonce",
        v1_generator=_generator_identity("v1"),
        v2_generator=_generator_identity("v2"),
    )
    with pytest.raises(ValueError, match="overlap|disjoint|custody"):
        predeclare(base)
    with pytest.raises(ValueError, match="overlap|disjoint|custody"):
        predeclare(replace(base, planned_v1_root=tmp_path, planned_v2_root=tmp_path / "future"))


@pytest.mark.skipif(os.name != "nt", reason="NTFS alternate data streams are Windows-only")
def test_blind_workspace_rejects_ntfs_alternate_data_stream(tmp_path: Path) -> None:
    _, _, _, workspace = _prepare(tmp_path)
    manifest = workspace / "blinded" / "manifest.json"
    ads = Path(str(manifest) + ":mapping-secret")
    try:
        ads.write_text("secret mapping material")
    except OSError:
        pytest.skip("temporary filesystem does not support NTFS ADS")
    with pytest.raises(ValueError, match="alternate data stream|ADS"):
        verify_blinded_workspace(workspace)


def test_candidate_canonical_input_is_single_read_and_rejects_validation_swap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    canonical_path = tmp_path / "candidate-one" / "canonical.json"
    original_read = Path.read_bytes
    reads = 0

    def swapping_read(path: Path) -> bytes:
        nonlocal reads
        value = original_read(path)
        if path.absolute() == canonical_path.absolute():
            reads += 1
            if reads > 1:
                return canonical_json_bytes({"schema_version": 1, "content": "異" * 80})
        return value

    monkeypatch.setattr(Path, "read_bytes", swapping_read)
    _prepare(tmp_path)
    assert reads == 1


def test_fresh_verifier_rebuilds_canonical_token_and_subtitle_relationships(
    tmp_path: Path,
) -> None:
    _, _, _, workspace = _prepare(tmp_path)
    anchor_path = workspace / "private" / "materialization-anchor.json"
    anchor = json.loads(anchor_path.read_text())
    record_path = workspace / anchor["record_relpath"]
    record = json.loads(record_path.read_text())
    canonical_object = next(
        item
        for item in record["source_snapshots"][0]["objects"]
        if item["role"] == "canonical_content"
    )
    changed_bytes = canonical_json_bytes({"schema_version": 1, "content": "異" * 80})
    changed_hash = __import__("hashlib").sha256(changed_bytes).hexdigest()
    changed_relpath = f"private/objects/{changed_hash}.bin"
    (workspace / changed_relpath).write_bytes(changed_bytes)
    canonical_object["sha256"] = changed_hash
    canonical_object["relpath"] = changed_relpath
    changed_record_bytes = canonical_json_bytes(record)
    changed_record_hash = __import__("hashlib").sha256(changed_record_bytes).hexdigest()
    changed_record_relpath = f"private/materializations/{changed_record_hash}.json"
    (workspace / changed_record_relpath).write_bytes(changed_record_bytes)
    anchor["record_hash"] = changed_record_hash
    anchor["record_relpath"] = changed_record_relpath
    anchor_path.write_bytes(canonical_json_bytes(anchor))

    with pytest.raises(ValueError, match="canonical|token|subtitle|semantic"):
        verify_blinded_workspace(workspace)


def test_presentations_are_rebuilt_by_pinned_canonical_renderer(tmp_path: Path) -> None:
    _, _, _, workspace = _prepare(tmp_path)
    presentation = next((workspace / "blinded" / "clips").glob("*/A.presentation.bin"))
    payload = json.loads(presentation.read_text())
    assert payload["schema_version"] == 1
    assert payload["renderer_id"] == "paired-canonical-text-audio-v1"
    assert payload["audio_clip_hash"]
    assert payload["cues"]


def test_swapped_system_or_generation_provenance_fails_frozen_generator_replay(
    tmp_path: Path,
) -> None:
    _, materialize_req, _, _ = _prepare(tmp_path)
    provenance_path = materialize_req.v1.input_root / "candidate.json"
    provenance = json.loads(provenance_path.read_text())
    provenance["system"] = "v2"
    provenance["generator_identity"] = _generator_identity("v2").to_dict()
    provenance["generator_identity_hash"] = _generator_identity("v2").identity_hash
    provenance["provenance_record_hash"] = hash_object(
        {key: value for key, value in provenance.items() if key != "provenance_record_hash"}
    )
    _write_json(provenance_path, provenance)
    with pytest.raises(ValueError, match="generator|system|provenance|drift"):
        materialize_candidates_and_commit_mapping(materialize_req)


def test_predeclare_streams_large_wav_with_peak_memory_bounded_by_selected_clip(
    tmp_path: Path,
) -> None:
    audio = tmp_path / "large.wav"
    sample_rate = 1000
    frame_count = 32 * 1024 * 1024
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + frame_count,
        b"WAVE",
        b"fmt ",
        16,
        1,
        1,
        sample_rate,
        sample_rate,
        1,
        8,
        b"data",
        frame_count,
    )
    audio.write_bytes(header)
    with audio.open("r+b") as stream:
        stream.truncate(len(header) + frame_count)
    audio_hash = __import__("hashlib").sha256()
    with audio.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            audio_hash.update(chunk)
    frame = {
        "schema_version": 1,
        "episode_id": "episode-large",
        "normalized_audio_hash": audio_hash.hexdigest(),
        "sample_rate_hz": sample_rate,
        "channel_count": 1,
        "sample_width_bytes": 1,
        "frame_count": frame_count,
        "selection_algorithm": "frozen_stratified_nonoverlap_v1",
        "selection_count": 20,
        "eligible_windows": [
            {
                "window_id": f"window-{index:02d}",
                "start_frame": index * 4_000,
                "end_frame": index * 4_000 + 4_000,
                "stratum": "all",
            }
            for index in range(20)
        ],
    }
    frame_path = tmp_path / "large-frame.json"
    _write_json(frame_path, frame)
    tracemalloc.start()
    predeclare(
        PredeclareRequest(
            workspace_root=tmp_path / "large-study",
            normalized_wav_path=audio,
            sampling_frame_path=frame_path,
            planned_v1_root=tmp_path / "large-v1",
            planned_v2_root=tmp_path / "large-v2",
            study_id="large-study",
            episode_id="episode-large",
            lineage_id="large-lineage",
            benchmark_suite_hash=hash_object({"suite": "large"}),
            frozen_at_utc="2026-08-13T01:00:00Z",
            selection_seed="large-seed",
            selection_nonce="large-nonce",
            v1_generator=_generator_identity("v1"),
            v2_generator=_generator_identity("v2"),
        )
    )
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert peak < 16 * 1024 * 1024


@pytest.mark.parametrize("attack", ["extra", "traversal", "duplicate", "noncanonical"])
def test_canonical_blinded_archive_rejects_unsafe_or_noncanonical_zip(
    tmp_path: Path, attack: str
) -> None:
    _, _, _, workspace = _prepare(tmp_path)
    archive_path = next((workspace / "exports" / "blinded").glob("*.zip"))
    if attack == "noncanonical":
        with zipfile.ZipFile(archive_path, "a") as archive:
            archive.comment = b"noncanonical metadata"
    else:
        name = {
            "extra": "extra.txt",
            "traversal": "../mapping-secret.txt",
            "duplicate": "manifest.json",
        }[attack]
        if attack == "duplicate":
            with pytest.warns(UserWarning, match="Duplicate name"):
                with zipfile.ZipFile(archive_path, "a", compression=zipfile.ZIP_STORED) as archive:
                    archive.writestr(name, b"secret")
        else:
            with zipfile.ZipFile(archive_path, "a", compression=zipfile.ZIP_STORED) as archive:
                archive.writestr(name, b"secret")
    with pytest.raises(ValueError, match="archive|duplicate|traversal|extra|noncanonical"):
        verify_blinded_workspace(workspace)
