from __future__ import annotations

import json
import shutil
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from pydantic import ValidationError

from agents.brook.podcast_subtitles.hashing import (
    canonical_json_bytes,
    hash_object,
    sha256_bytes,
)
from agents.brook.podcast_subtitles.recognition_run import (
    RecognitionChunkPlanV1,
    RecognitionContentDigestV1,
    RecognitionRunConflictError,
    RecognitionRunFinalizationV1,
    RecognitionRunIntegrityError,
    RecognitionRunPlanV1,
    build_recognition_adapter_identity_snapshot,
    build_recognition_audio_binding,
    build_recognition_chunk_plan,
    build_recognition_chunk_planner,
    build_recognition_request_binding,
    build_recognition_run_failure,
    build_recognition_run_plan,
    derive_recognition_pcm_wav_chunk,
    rebuild_recognition_run_plan,
)
from agents.brook.podcast_subtitles.store import GenerationStore


def _write_wav(path: Path, *, frame_count: int = 10) -> None:
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(10)
        writer.writeframes(b"\x00\x00" * frame_count)


def _identity(*, runtime: str = "runtime-a"):
    return build_recognition_adapter_identity_snapshot(
        adapter_name="fixture-qwen",
        adapter_version="3",
        model="Qwen/Qwen3-ASR-1.7B",
        model_version="a" * 40,
        aligner="Qwen/Qwen3-ForcedAligner-0.6B",
        aligner_version="b" * 40,
        runtime_components=(("python", "3.12"), ("runtime", runtime)),
        adapter_code_hash=hash_object({"adapter": "code-v3"}),
        config_hash=hash_object({"owned": 4, "context": 1}),
        execution_mode="fixture",
    )


def _build_plan(tmp_path: Path):
    audio_path = tmp_path / "normalized.wav"
    _write_wav(audio_path)
    audio = build_recognition_audio_binding(audio_path)
    request = build_recognition_request_binding(
        episode_id="episode-anji",
        invocation_id="recognition-primary",
        logical_namespace="primary-recognizer-0",
        language_hint="zh-TW",
        context_policy_id="nakama-verbatim-no-lexical-context-v1",
        context_bytes=b"",
        audio=audio,
    )
    planner = build_recognition_chunk_planner(
        planner_name="qwen-bounded-overlap",
        planner_version="1",
        owned_chunk_samples=4,
        seam_context_samples=1,
    )
    ranges = ((0, 5, 0, 4), (3, 9, 4, 8), (7, 10, 8, 10))
    chunks = tuple(
        build_recognition_chunk_plan(
            index=index,
            count=3,
            inference_start_sample=inference_start,
            inference_end_sample=inference_end,
            owned_start_sample=owned_start,
            owned_end_sample=owned_end,
            normalized_audio=audio_path,
            audio=audio,
        )
        for index, (
            inference_start,
            inference_end,
            owned_start,
            owned_end,
        ) in enumerate(ranges)
    )
    derived = tuple(
        derive_recognition_pcm_wav_chunk(
            audio_path,
            audio=audio,
            start_sample=chunk.inference_start_sample,
            end_sample=chunk.inference_end_sample,
        )
        for chunk in chunks
    )
    plan = build_recognition_run_plan(
        request=request,
        audio=audio,
        adapter_identity=_identity(),
        planner=planner,
        chunks=chunks,
    )
    return plan, derived


def _drifted_plan(plan, *, runtime: str = "runtime-b"):
    return build_recognition_run_plan(
        request=plan.request,
        audio=plan.audio,
        adapter_identity=_identity(runtime=runtime),
        planner=plan.planner,
        chunks=plan.chunks,
    )


def _repo(tmp_path: Path):
    return GenerationStore(tmp_path).recognition_run_repository()


def _commit_all(repo, plan, derived):
    for index, payload in enumerate(derived):
        repo.commit_chunk(
            plan=plan,
            chunk_index=index,
            derived_chunk_bytes=payload,
            adapter_observation={
                "language": "Chinese",
                "transcript_text": f"chunk-{index}",
                "words": [{"text": f"字{index}", "start": 0.1, "end": 0.2}],
            },
        )


def _evidence_bytes(label: str) -> bytes:
    return canonical_json_bytes(
        {
            "schema_version": 1,
            "kind": "fixture-recognition-evidence",
            "label": label,
        }
    )


def _symlink_directory_or_skip(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"directory symlinks are unavailable in this test environment: {exc}")


def test_plan_binds_request_audio_topology_identity_planner_and_exact_partition(
    tmp_path: Path,
) -> None:
    plan, _derived = _build_plan(tmp_path)

    assert rebuild_recognition_run_plan(plan) == plan
    assert RecognitionRunPlanV1.model_validate_json(canonical_json_bytes(plan)) == plan
    assert plan.request.audio_binding_hash == plan.audio.content_hash
    assert plan.audio.normalized_audio.size_bytes == (tmp_path / "normalized.wav").stat().st_size
    assert plan.audio.frame_count == 10
    assert tuple((chunk.owned_start_sample, chunk.owned_end_sample) for chunk in plan.chunks) == (
        (0, 4),
        (4, 8),
        (8, 10),
    )
    assert all(
        chunk.inference_start_sample
        <= chunk.owned_start_sample
        < chunk.owned_end_sample
        <= chunk.inference_end_sample
        for chunk in plan.chunks
    )
    assert "uri" not in type(plan.chunks[0].derived_audio).model_fields


def test_plan_rejects_gap_overlap_bad_context_and_caller_filled_hashes(
    tmp_path: Path,
) -> None:
    plan, _derived = _build_plan(tmp_path)
    bad_second = build_recognition_chunk_plan(
        index=1,
        count=3,
        inference_start_sample=4,
        inference_end_sample=9,
        owned_start_sample=5,
        owned_end_sample=8,
        normalized_audio=tmp_path / "normalized.wav",
        audio=plan.audio,
    )
    with pytest.raises(ValidationError, match="gap or overlap"):
        build_recognition_run_plan(
            request=plan.request,
            audio=plan.audio,
            adapter_identity=plan.adapter_identity,
            planner=plan.planner,
            chunks=(plan.chunks[0], bad_second, plan.chunks[2]),
        )

    payload = plan.model_dump(mode="python")
    payload["chunk_plan_hash"] = "0" * 64
    with pytest.raises(ValidationError, match="chunk_plan_hash"):
        RecognitionRunPlanV1.model_validate(payload)


def test_prepare_creates_initial_prefix_and_isolated_exact_layout(tmp_path: Path) -> None:
    plan, _derived = _build_plan(tmp_path)
    repo = _repo(tmp_path)

    stored = repo.prepare(plan, normalized_audio=tmp_path / "normalized.wav")
    fresh = _repo(tmp_path).load_state(plan.run_key, expected_plan=plan)

    assert stored.completed_prefix == fresh.completed_prefix == 0
    assert fresh.checkpoint.receipt_ids == ()
    assert fresh.checkpoint.previous_checkpoint_id is None
    assert fresh.head.previous_head_id is None
    assert (
        repo.load_plan(
            plan.run_key,
            expected_adapter_identity=plan.adapter_identity,
        )
        == plan
    )
    assert stored.directory == (tmp_path / ".subtitle-v2" / "recognition-runs" / "runs" / plan.id)
    assert set(path.name for path in stored.directory.iterdir()) == {
        "active-head.json",
        "checkpoints",
        "chunks",
        "finalizations",
        "heads",
        "plan.json",
    }


def test_stable_run_key_rejects_runtime_drift_instead_of_forking(tmp_path: Path) -> None:
    plan, _derived = _build_plan(tmp_path)
    drifted = _drifted_plan(plan)
    assert drifted.run_key == plan.run_key
    assert drifted.id != plan.id
    repo = _repo(tmp_path)
    repo.prepare(plan, normalized_audio=tmp_path / "normalized.wav")

    with pytest.raises(RecognitionRunIntegrityError, match="different complete plan"):
        repo.prepare(drifted, normalized_audio=tmp_path / "normalized.wav")

    run_dirs = [
        path for path in repo.runs_dir.iterdir() if path.is_dir() and not path.name.startswith(".")
    ]
    assert run_dirs == [repo.runs_dir / plan.id]


def test_missing_key_repairs_exact_orphan_but_drift_cannot_rebind_paid_prefix(
    tmp_path: Path,
) -> None:
    plan, derived = _build_plan(tmp_path)
    repo = _repo(tmp_path)
    repo.prepare(plan, normalized_audio=tmp_path / "normalized.wav")
    _commit_all(repo, plan, derived)
    finalized = repo.commit_finalization(
        plan=plan,
        status="verified",
        recognition_evidence_bytes=_evidence_bytes("orphan"),
    )
    key_path = repo.keys_dir / f"{plan.run_key}.json"
    key_path.unlink()

    recovered = _repo(tmp_path).prepare(
        plan,
        normalized_audio=tmp_path / "normalized.wav",
    )

    assert recovered.completed_prefix == len(plan.chunks)
    assert _repo(tmp_path).load_finalization(plan=plan) == finalized

    active_pointer = (recovered.directory / "active-head.json").read_bytes()
    finalization_path = recovered.directory / "finalizations" / f"{finalized.id}.json"
    finalization_bytes = finalization_path.read_bytes()
    key_path.unlink()
    drifted = _drifted_plan(plan)
    with pytest.raises(RecognitionRunIntegrityError, match="different complete plan|orphan"):
        _repo(tmp_path).prepare(
            drifted,
            normalized_audio=tmp_path / "normalized.wav",
        )

    assert not key_path.exists()
    assert (recovered.directory / "active-head.json").read_bytes() == active_pointer
    assert finalization_path.read_bytes() == finalization_bytes
    assert {
        path.name
        for path in repo.runs_dir.iterdir()
        if path.is_dir() and not path.name.startswith(".")
    } == {plan.id}


def test_attempt_invocation_cannot_evade_existing_run_key(tmp_path: Path) -> None:
    plan, _derived = _build_plan(tmp_path)
    restarted_request = build_recognition_request_binding(
        episode_id=plan.request.episode_id,
        invocation_id="recognition-primary-new-attempt",
        logical_namespace=plan.request.logical_namespace,
        language_hint=plan.request.language_hint,
        context_policy_id=plan.request.context_policy_id,
        context_bytes=b"",
        audio=plan.audio,
    )
    restarted = build_recognition_run_plan(
        request=restarted_request,
        audio=plan.audio,
        adapter_identity=plan.adapter_identity,
        planner=plan.planner,
        chunks=plan.chunks,
    )
    assert restarted.run_key == plan.run_key
    assert restarted.request.invocation_id != plan.request.invocation_id
    repo = _repo(tmp_path)
    repo.prepare(plan, normalized_audio=tmp_path / "normalized.wav")
    with pytest.raises(RecognitionRunIntegrityError, match="different complete plan"):
        repo.prepare(
            restarted,
            normalized_audio=tmp_path / "normalized.wav",
        )


def test_recognition_run_key_binds_exact_empty_context_policy(tmp_path: Path) -> None:
    plan, _derived = _build_plan(tmp_path)

    payload = plan.request.model_dump(mode="json")
    payload["context_sha256"] = "0" * 64

    with pytest.raises(ValueError, match="exact empty context"):
        type(plan.request).model_validate(payload)


def test_prepare_rederives_every_chunk_from_exact_normalized_pcm(tmp_path: Path) -> None:
    plan, _derived = _build_plan(tmp_path)
    chunk_payload = plan.chunks[0].model_dump(mode="python", exclude={"id"})
    chunk_payload["derived_audio"] = RecognitionContentDigestV1(
        sha256="e" * 64,
        size_bytes=plan.chunks[0].derived_audio.size_bytes,
    )
    forged_chunk = RecognitionChunkPlanV1(
        **chunk_payload,
        id="recognition-chunk-" + hash_object(chunk_payload),
    )
    forged_plan = build_recognition_run_plan(
        request=plan.request,
        audio=plan.audio,
        adapter_identity=plan.adapter_identity,
        planner=plan.planner,
        chunks=(forged_chunk, *plan.chunks[1:]),
    )
    repo = _repo(tmp_path)

    with pytest.raises(RecognitionRunIntegrityError, match="does not derive"):
        repo.prepare(
            forged_plan,
            normalized_audio=tmp_path / "normalized.wav",
        )

    assert not (repo.keys_dir / f"{forged_plan.run_key}.json").exists()
    assert not (repo.runs_dir / forged_plan.id).exists()


def test_prepare_rejects_audio_bytes_changed_after_binding(tmp_path: Path) -> None:
    plan, _derived = _build_plan(tmp_path)
    with wave.open(str(tmp_path / "normalized.wav"), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(10)
        writer.writeframes(b"\x01\x00" * 10)
    repo = _repo(tmp_path)

    with pytest.raises(RecognitionRunIntegrityError, match="differs from"):
        repo.prepare(plan, normalized_audio=tmp_path / "normalized.wav")

    assert not (repo.keys_dir / f"{plan.run_key}.json").exists()
    assert not (repo.runs_dir / plan.id).exists()


@pytest.mark.parametrize("owned_directory", ["root", "keys", "runs", "locks"])
def test_prepare_rejects_repository_symlink_escape_before_any_outside_write(
    tmp_path: Path,
    owned_directory: str,
) -> None:
    plan, _derived = _build_plan(tmp_path)
    subtitle_root = tmp_path / ".subtitle-v2"
    subtitle_root.mkdir()
    repo = _repo(tmp_path)
    outside = tmp_path / f"outside-{owned_directory}"
    outside.mkdir()
    if owned_directory == "root":
        link = repo.root
    else:
        repo.root.mkdir()
        link = getattr(repo, f"{owned_directory}_dir")
    _symlink_directory_or_skip(link, outside)

    with pytest.raises(RecognitionRunIntegrityError, match="symbolic link|escape"):
        repo.prepare(plan, normalized_audio=tmp_path / "normalized.wav")

    assert tuple(outside.iterdir()) == ()


def test_chunk_commit_is_exact_idempotent_and_replays_fresh_prefix(tmp_path: Path) -> None:
    plan, derived = _build_plan(tmp_path)
    repo = _repo(tmp_path)
    repo.prepare(plan, normalized_audio=tmp_path / "normalized.wav")
    observation = {
        "language": "Chinese",
        "transcript_text": "第一塊",
        "words": [{"text": "第一", "start": 0.1, "end": 0.4}],
    }

    first = repo.commit_chunk(
        plan=plan,
        chunk_index=0,
        derived_chunk_bytes=derived[0],
        adapter_observation=observation,
    )
    second = _repo(tmp_path).commit_chunk(
        plan=plan,
        chunk_index=0,
        derived_chunk_bytes=derived[0],
        adapter_observation=observation,
    )
    replay = _repo(tmp_path).replay_prefix(plan=plan)

    assert first.receipt == second.receipt == replay[0].receipt
    assert replay[0].adapter_observation == observation
    assert replay[0].receipt.previous_receipt_id is None
    assert replay[0].receipt.observation_semantics == (
        "adapter_observed_sdk_mapping_canonical_json_v1"
    )
    assert replay[0].receipt.provider_wire_bytes_claimed is False
    assert _repo(tmp_path).load_state(plan.run_key).completed_prefix == 1
    assert not tuple((tmp_path / ".subtitle-v2" / "recognition-runs").rglob("*.wav"))


def test_chunk_commit_rejects_gap_wrong_derived_bytes_and_conflicting_retry(
    tmp_path: Path,
) -> None:
    plan, derived = _build_plan(tmp_path)
    repo = _repo(tmp_path)
    repo.prepare(plan, normalized_audio=tmp_path / "normalized.wav")

    with pytest.raises(RecognitionRunIntegrityError, match="prefix gap"):
        repo.commit_chunk(
            plan=plan,
            chunk_index=1,
            derived_chunk_bytes=derived[1],
            adapter_observation={"text": "too early"},
        )
    assert repo.load_state(plan.run_key).completed_prefix == 0
    assert tuple((repo.runs_dir / plan.id / "chunks").iterdir()) == ()
    with pytest.raises(RecognitionRunIntegrityError, match="differ from"):
        repo.commit_chunk(
            plan=plan,
            chunk_index=0,
            derived_chunk_bytes=b"wrong derived bytes",
            adapter_observation={"text": "wrong"},
        )
    repo.commit_chunk(
        plan=plan,
        chunk_index=0,
        derived_chunk_bytes=derived[0],
        adapter_observation={"text": "winner"},
    )
    with pytest.raises(RecognitionRunConflictError, match="different bytes"):
        repo.commit_chunk(
            plan=plan,
            chunk_index=0,
            derived_chunk_bytes=derived[0],
            adapter_observation={"text": "loser"},
        )


def test_slot_after_pointer_crash_repairs_without_adapter_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, derived = _build_plan(tmp_path)
    repo = _repo(tmp_path)
    repo.prepare(plan, normalized_audio=tmp_path / "normalized.wav")

    def crash_before_pointer(*_args, **_kwargs):
        raise OSError("simulated pointer publication crash")

    monkeypatch.setattr(repo, "_publish_active_pointer", crash_before_pointer)
    with pytest.raises(OSError, match="simulated"):
        repo.commit_chunk(
            plan=plan,
            chunk_index=0,
            derived_chunk_bytes=derived[0],
            adapter_observation={"text": "durable once"},
        )

    fresh = _repo(tmp_path)
    with pytest.raises(RecognitionRunIntegrityError, match="awaiting head repair"):
        fresh.load_plan(plan.run_key)
    repaired = fresh.repair_published_tail(plan=plan)

    assert repaired.completed_prefix == 1
    assert fresh.replay_prefix(plan=plan)[0].adapter_observation == {"text": "durable once"}


def test_partial_published_tail_fails_closed_including_repair(tmp_path: Path) -> None:
    plan, _derived = _build_plan(tmp_path)
    repo = _repo(tmp_path)
    stored = repo.prepare(plan, normalized_audio=tmp_path / "normalized.wav")
    partial = stored.directory / "chunks" / "000000"
    partial.mkdir()
    (partial / "execution.json").write_text("{}", encoding="utf-8")

    with pytest.raises(RecognitionRunIntegrityError, match="inventory mismatch"):
        repo.load_plan(plan.run_key)
    with pytest.raises(RecognitionRunIntegrityError, match="inventory mismatch"):
        repo.repair_published_tail(plan=plan)


@pytest.mark.parametrize(
    "target_name",
    ["execution.json", "adapter-observation.json", "derived-chunk.json"],
)
def test_chunk_record_or_artifact_tamper_fails_closed(
    tmp_path: Path,
    target_name: str,
) -> None:
    plan, derived = _build_plan(tmp_path)
    repo = _repo(tmp_path)
    repo.prepare(plan, normalized_audio=tmp_path / "normalized.wav")
    replay = repo.commit_chunk(
        plan=plan,
        chunk_index=0,
        derived_chunk_bytes=derived[0],
        adapter_observation={"text": "untampered"},
    )
    (replay.directory / target_name).write_bytes(b'{"tampered":true}')

    with pytest.raises(RecognitionRunIntegrityError):
        _repo(tmp_path).replay_prefix(plan=plan)


def test_plan_key_pointer_and_unknown_inventory_tamper_fail_closed(tmp_path: Path) -> None:
    plan, _derived = _build_plan(tmp_path)
    repo = _repo(tmp_path)
    stored = repo.prepare(plan, normalized_audio=tmp_path / "normalized.wav")

    pointer = json.loads((stored.directory / "active-head.json").read_text("utf-8"))
    pointer["completed_prefix"] = 1
    (stored.directory / "active-head.json").write_text(
        json.dumps(pointer, separators=(",", ":")), encoding="utf-8"
    )
    with pytest.raises(RecognitionRunIntegrityError, match="pointer"):
        _repo(tmp_path).load_plan(plan.run_key)


@pytest.mark.parametrize("target", ["plan", "key", "checkpoint", "head"])
def test_plan_key_checkpoint_and_head_record_tamper_fail_closed(
    tmp_path: Path,
    target: str,
) -> None:
    plan, _derived = _build_plan(tmp_path)
    repo = _repo(tmp_path)
    stored = repo.prepare(plan, normalized_audio=tmp_path / "normalized.wav")
    paths = {
        "plan": stored.directory / "plan.json",
        "key": repo.keys_dir / f"{plan.run_key}.json",
        "checkpoint": (stored.directory / "checkpoints" / f"{stored.checkpoint.id}.json"),
        "head": stored.directory / "heads" / f"{stored.head.id}.json",
    }
    paths[target].write_bytes(b'{"schema_version":1}')

    with pytest.raises(RecognitionRunIntegrityError):
        _repo(tmp_path).load_plan(plan.run_key)


@pytest.mark.parametrize(
    ("relative", "directory"),
    [
        ("unknown.bin", False),
        (".evil.tmp", False),
        ("chunks/.evil", True),
        ("checkpoints/not-a-checkpoint.json", False),
    ],
)
def test_unknown_files_and_directories_fail_closed(
    tmp_path: Path,
    relative: str,
    directory: bool,
) -> None:
    plan, _derived = _build_plan(tmp_path)
    repo = _repo(tmp_path)
    stored = repo.prepare(plan, normalized_audio=tmp_path / "normalized.wav")
    target = stored.directory / relative
    if directory:
        target.mkdir(parents=True)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"unknown")

    with pytest.raises(RecognitionRunIntegrityError, match="inventory|unknown|filename"):
        _repo(tmp_path).load_plan(plan.run_key)


def test_post_gap_chunk_slot_is_rejected_not_silently_reordered(tmp_path: Path) -> None:
    plan, derived = _build_plan(tmp_path)
    repo = _repo(tmp_path)
    repo.prepare(plan, normalized_audio=tmp_path / "normalized.wav")
    first = repo.commit_chunk(
        plan=plan,
        chunk_index=0,
        derived_chunk_bytes=derived[0],
        adapter_observation={"text": "first"},
    )
    shutil.copytree(first.directory, first.directory.parent / "000002")

    with pytest.raises(RecognitionRunIntegrityError, match="gapped"):
        _repo(tmp_path).load_plan(plan.run_key)


def test_expected_request_plan_and_runtime_must_match_exactly(tmp_path: Path) -> None:
    plan, _derived = _build_plan(tmp_path)
    repo = _repo(tmp_path)
    repo.prepare(plan, normalized_audio=tmp_path / "normalized.wav")
    drifted = _drifted_plan(plan)

    with pytest.raises(RecognitionRunIntegrityError, match="different complete plan"):
        repo.load_plan(plan.run_key, expected_plan=drifted)
    with pytest.raises(RecognitionRunIntegrityError, match="runtime/model identity"):
        repo.load_plan(
            plan.run_key,
            expected_adapter_identity=drifted.adapter_identity,
        )


def test_two_writers_cannot_overwrite_or_fork_one_slot(tmp_path: Path) -> None:
    plan, derived = _build_plan(tmp_path)
    _repo(tmp_path).prepare(plan, normalized_audio=tmp_path / "normalized.wav")
    barrier = Barrier(2)

    def write(candidate: str):
        barrier.wait()
        return _repo(tmp_path).commit_chunk(
            plan=plan,
            chunk_index=0,
            derived_chunk_bytes=derived[0],
            adapter_observation={"candidate": candidate},
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(write, "left"), pool.submit(write, "right")]
        outcomes = []
        for future in futures:
            try:
                outcomes.append(("ok", future.result()))
            except RecognitionRunConflictError as exc:
                outcomes.append(("conflict", exc))

    assert sorted(kind for kind, _value in outcomes) == ["conflict", "ok"]
    replay = _repo(tmp_path).replay_prefix(plan=plan)
    assert len(replay) == 1
    assert replay[0].adapter_observation["candidate"] in {"left", "right"}
    assert len(tuple((replay[0].directory.parent).iterdir())) == 1


def test_finalization_requires_full_prefix_and_verified_evidence_hash(tmp_path: Path) -> None:
    plan, derived = _build_plan(tmp_path)
    repo = _repo(tmp_path)
    repo.prepare(plan, normalized_audio=tmp_path / "normalized.wav")
    with pytest.raises(RecognitionRunIntegrityError, match="complete chunk prefix"):
        repo.commit_finalization(
            plan=plan,
            status="verified",
            recognition_evidence_bytes=_evidence_bytes("a"),
        )

    _commit_all(repo, plan, derived)
    verified = repo.commit_finalization(
        plan=plan,
        status="verified",
        recognition_evidence_bytes=_evidence_bytes("a"),
    )

    assert verified.status == "verified"
    assert verified.recognition_evidence_hash == sha256_bytes(_evidence_bytes("a"))
    assert verified.failure is None
    assert _repo(tmp_path).load_finalization(plan=plan) == verified
    assert (
        repo.commit_finalization(
            plan=plan,
            status="verified",
            recognition_evidence_bytes=_evidence_bytes("a"),
        )
        == verified
    )


def test_rejected_finalization_requires_typed_failure_and_no_evidence(tmp_path: Path) -> None:
    plan, derived = _build_plan(tmp_path)
    repo = _repo(tmp_path)
    repo.prepare(plan, normalized_audio=tmp_path / "normalized.wav")
    _commit_all(repo, plan, derived)
    failure = build_recognition_run_failure(
        code="seam_conflict",
        affected_chunk_ids=(plan.chunks[0].id, plan.chunks[1].id),
        diagnostics={"seam_sample": 4, "left": "安", "right": "岸"},
    )
    assert failure.affected_chunk_ids == tuple(sorted(failure.affected_chunk_ids))

    rejected = repo.commit_finalization(
        plan=plan,
        status="rejected",
        failure=failure,
    )

    assert rejected.status == "rejected"
    assert rejected.failure == failure
    assert rejected.recognition_evidence_hash is None
    with pytest.raises((ValidationError, RecognitionRunConflictError)):
        repo.commit_finalization(
            plan=plan,
            status="verified",
            recognition_evidence_bytes=_evidence_bytes("b"),
        )


def test_rejected_finalization_rejects_foreign_affected_chunk_id(tmp_path: Path) -> None:
    plan, derived = _build_plan(tmp_path)
    repo = _repo(tmp_path)
    repo.prepare(plan, normalized_audio=tmp_path / "normalized.wav")
    _commit_all(repo, plan, derived)
    failure = build_recognition_run_failure(
        code="seam_conflict",
        affected_chunk_ids=("recognition-chunk-" + "e" * 64,),
        diagnostics={"seam_sample": 4},
    )

    with pytest.raises(RecognitionRunIntegrityError, match="immutable plan"):
        repo.commit_finalization(
            plan=plan,
            status="rejected",
            failure=failure,
        )


@pytest.mark.parametrize("operation", ["load", "repair"])
def test_disk_replay_rejects_content_addressed_finalization_with_foreign_chunk(
    tmp_path: Path,
    operation: str,
) -> None:
    plan, derived = _build_plan(tmp_path)
    repo = _repo(tmp_path)
    stored = repo.prepare(plan, normalized_audio=tmp_path / "normalized.wav")
    _commit_all(repo, plan, derived)
    completed = repo.load_state(plan.run_key, expected_plan=plan)
    failure = build_recognition_run_failure(
        code="seam_conflict",
        affected_chunk_ids=("recognition-chunk-" + "d" * 64,),
        diagnostics={"seam_sample": 4},
    )
    payload = {
        "schema_version": 1,
        "run_id": plan.id,
        "run_key": plan.run_key,
        "plan_id": plan.id,
        "head_id": completed.head.id,
        "checkpoint_id": completed.checkpoint.id,
        "completed_prefix": len(plan.chunks),
        "status": "rejected",
        "recognition_evidence_hash": None,
        "failure": failure,
    }
    forged = RecognitionRunFinalizationV1(
        **payload,
        id="recognition-run-finalization-" + hash_object(payload),
    )
    finalization_path = stored.directory / "finalizations" / f"{forged.id}.json"
    finalization_path.write_bytes(canonical_json_bytes(forged))

    with pytest.raises(RecognitionRunIntegrityError, match="affected chunks crossed"):
        if operation == "load":
            _repo(tmp_path).load_finalization(plan=plan)
        else:
            _repo(tmp_path).repair_published_finalization(plan=plan)


def test_finalization_shape_rejects_failure_evidence_ambiguity(tmp_path: Path) -> None:
    plan, derived = _build_plan(tmp_path)
    repo = _repo(tmp_path)
    repo.prepare(plan, normalized_audio=tmp_path / "normalized.wav")
    _commit_all(repo, plan, derived)
    failure = build_recognition_run_failure(
        code="deterministic_postprocessing_failure",
        diagnostics={"gate": "owned-word-order"},
    )

    with pytest.raises(ValidationError, match="requires Evidence and no failure"):
        repo.commit_finalization(
            plan=plan,
            status="verified",
            recognition_evidence_bytes=_evidence_bytes("c"),
            failure=failure,
        )
    with pytest.raises(ValidationError, match="requires typed failure and no Evidence"):
        repo.commit_finalization(
            plan=plan,
            status="rejected",
            recognition_evidence_bytes=_evidence_bytes("c"),
            failure=failure,
        )


def test_verified_finalization_rejects_noncanonical_evidence_bytes(tmp_path: Path) -> None:
    plan, derived = _build_plan(tmp_path)
    repo = _repo(tmp_path)
    repo.prepare(plan, normalized_audio=tmp_path / "normalized.wav")
    _commit_all(repo, plan, derived)
    noncanonical = json.dumps(
        {"schema_version": 1, "kind": "fixture-recognition-evidence"},
        indent=2,
    ).encode("utf-8")

    with pytest.raises(RecognitionRunIntegrityError, match="canonical JSON"):
        repo.commit_finalization(
            plan=plan,
            status="verified",
            recognition_evidence_bytes=noncanonical,
        )

    assert repo.load_finalization(plan=plan) is None


def test_finalization_tamper_and_post_finalization_chunk_conflict_fail_closed(
    tmp_path: Path,
) -> None:
    plan, derived = _build_plan(tmp_path)
    repo = _repo(tmp_path)
    repo.prepare(plan, normalized_audio=tmp_path / "normalized.wav")
    _commit_all(repo, plan, derived)
    finalized = repo.commit_finalization(
        plan=plan,
        status="verified",
        recognition_evidence_bytes=_evidence_bytes("d"),
    )
    with pytest.raises(RecognitionRunConflictError, match="different bytes"):
        repo.commit_chunk(
            plan=plan,
            chunk_index=0,
            derived_chunk_bytes=derived[0],
            adapter_observation={"text": "post-finalization overwrite"},
        )
    path = repo.runs_dir / plan.id / "finalizations" / f"{finalized.id}.json"
    path.write_bytes(b'{"schema_version":1}')

    with pytest.raises(RecognitionRunIntegrityError, match="finalization"):
        _repo(tmp_path).load_finalization(plan=plan)


def test_terminal_pointer_prevents_deleted_finalization_from_flipping_outcome(
    tmp_path: Path,
) -> None:
    plan, derived = _build_plan(tmp_path)
    repo = _repo(tmp_path)
    repo.prepare(plan, normalized_audio=tmp_path / "normalized.wav")
    _commit_all(repo, plan, derived)
    failure = build_recognition_run_failure(
        code="seam_conflict",
        affected_chunk_ids=(plan.chunks[0].id,),
        diagnostics={"seam_sample": 4},
    )
    rejected = repo.commit_finalization(
        plan=plan,
        status="rejected",
        failure=failure,
    )
    (repo.runs_dir / plan.id / "finalizations" / f"{rejected.id}.json").unlink()

    with pytest.raises(RecognitionRunIntegrityError, match="terminal|finalization"):
        _repo(tmp_path).load_finalization(plan=plan)
    with pytest.raises(RecognitionRunIntegrityError, match="terminal|finalization"):
        _repo(tmp_path).commit_finalization(
            plan=plan,
            status="verified",
            recognition_evidence_bytes=_evidence_bytes("replacement"),
        )


def test_finalization_pointer_crash_repairs_same_outcome_without_provider_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, derived = _build_plan(tmp_path)
    repo = _repo(tmp_path)
    repo.prepare(plan, normalized_audio=tmp_path / "normalized.wav")
    _commit_all(repo, plan, derived)

    def crash_before_terminal_pointer(*_args, **_kwargs):
        raise OSError("simulated terminal pointer publication crash")

    monkeypatch.setattr(repo, "_publish_active_pointer", crash_before_terminal_pointer)
    with pytest.raises(OSError, match="terminal pointer publication crash"):
        repo.commit_finalization(
            plan=plan,
            status="verified",
            recognition_evidence_bytes=_evidence_bytes("terminal-crash"),
        )

    fresh = _repo(tmp_path)
    with pytest.raises(RecognitionRunIntegrityError, match="terminal pointer repair"):
        fresh.load_finalization(plan=plan)
    repaired = fresh.repair_published_finalization(plan=plan)

    assert repaired.status == "verified"
    assert (
        fresh.commit_finalization(
            plan=plan,
            status="verified",
            recognition_evidence_bytes=_evidence_bytes("terminal-crash"),
        )
        == repaired
    )
    with pytest.raises(RecognitionRunConflictError, match="different finalization"):
        fresh.commit_finalization(
            plan=plan,
            status="rejected",
            failure=build_recognition_run_failure(
                code="deterministic_postprocessing_failure",
                diagnostics={"gate": "terminal-conflict"},
            ),
        )


def test_finalization_filename_must_equal_typed_finalization_id(tmp_path: Path) -> None:
    plan, derived = _build_plan(tmp_path)
    repo = _repo(tmp_path)
    repo.prepare(plan, normalized_audio=tmp_path / "normalized.wav")
    _commit_all(repo, plan, derived)
    finalized = repo.commit_finalization(
        plan=plan,
        status="verified",
        recognition_evidence_bytes=_evidence_bytes("renamed"),
    )
    source = repo.runs_dir / plan.id / "finalizations" / f"{finalized.id}.json"
    renamed = source.with_name("recognition-run-finalization-" + "e" * 64 + ".json")
    source.rename(renamed)

    with pytest.raises(RecognitionRunIntegrityError, match="filename identity"):
        _repo(tmp_path).load_finalization(plan=plan)


def test_generation_store_exposes_narrow_repository_without_package_export(
    tmp_path: Path,
) -> None:
    store = GenerationStore(tmp_path)
    repository = store.recognition_run_repository()

    assert repository.root == store.recognition_runs_dir
    assert repository.subtitle_root == store.root
