from __future__ import annotations

import hashlib
import inspect
import io
import json
import wave
from pathlib import Path

import pytest

from agents.brook.podcast_subtitles.hashing import canonical_json_bytes
from agents.brook.podcast_subtitles.memo_bundled_runner import (
    MemoBundledRunnerExecutionReceiptV1,
)
from scripts import podcast_subtitle_release as release
from scripts import podcast_subtitle_v2_simple_step7 as simple


@pytest.fixture
def tmp_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("subtitle-release") / "fixture-episode"
    root.mkdir()
    return root


def _canonical(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode()


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _srt(cue_count: int = 4) -> bytes:
    blocks = []
    for cue in range(1, cue_count + 1):
        blocks.append(
            f"{cue}\n00:00:{cue:02d},000 --> 00:00:{cue:02d},900\n原文{cue}"
        )
    return ("\n\n".join(blocks) + "\n").encode()


def _record(proposal: str, *, major_risk: bool = True) -> dict[str, object]:
    return {
        "cue_numbers": [2],
        "start": "00:00:02,000",
        "end": "00:00:02,900",
        "original": "原文2",
        "proposed": proposal,
        "confidence": 0.99,
        "category": "proper_noun",
        "reason": "fixture",
        "evidence": "fixture",
        "major_risk": major_risk,
    }


def _audit(agent: str, cue_count: int, findings: list[dict[str, object]]) -> bytes:
    payload: dict[str, object] = {
        "agent": agent,
        "cues_reviewed": cue_count,
        "audio_reviewed": False,
        "findings": findings,
    }
    if agent == "B":
        payload["risk_cues"] = []
    return _canonical(payload)


def _write(root: Path, relative: str, raw: bytes) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return path


def _ref(root: Path, path: Path) -> dict[str, object]:
    raw = path.read_bytes()
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": _digest(raw),
        "size_bytes": len(raw),
    }


def _pcm_wav(
    duration_ms: int = 3000,
    *,
    sample_width_bytes: int = 2,
    channels: int = 1,
) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(sample_width_bytes)
        handle.setframerate(16_000)
        frame = b"\0" * (sample_width_bytes * channels)
        handle.writeframes(frame * (16_000 * duration_ms // 1000))
    return buffer.getvalue()


def _memo_execution_lineage(root: Path, *, audio: bytes, memo: bytes) -> dict[str, object]:
    input_wav = _write(root, release._DEFAULT_INPUT_PATHS["normalized_audio"], audio)
    output_srt = _write(root, release._DEFAULT_INPUT_PATHS["memo_srt"], memo)
    runner = _write(root, "subtitle-v2/runtime/memo-whisper.exe", b"memo-runner")
    model = _write(root, "subtitle-v2/runtime/ggml-large-v2.bin", b"large-v2-model")
    stdout = _write(root, "subtitle-v2/memo.stdout", b"")
    stderr = _write(root, "subtitle-v2/memo.stderr", b"")
    receipt_path = root / "subtitle-v2/memo-execution.v1.json"
    invocation_input = root / "subtitle-v2/.memo-staging/normalized.wav"
    prompt = "fixture prompt"
    argv = [
        str(runner.resolve()),
        "-m",
        str(model.resolve()),
        "-l",
        "zh",
        "--prompt",
        prompt,
        "--no-colors",
        "--use-gpu",
        "auto",
        "--output-srt",
        "--max-context",
        "0",
        "--max-len",
        "0",
        "-f",
        str(invocation_input.resolve()),
    ]
    receipt_draft = _canonical(
        {
            "schema_version": 1,
            "contract": "memo-bundled-runner-execution-v1",
            "argv": argv,
            "runner_path": str(runner.resolve()),
            "runner_sha256": _digest(runner.read_bytes()),
            "runner_size_bytes": runner.stat().st_size,
            "model_path": str(model.resolve()),
            "model_sha256": _digest(model.read_bytes()),
            "model_size_bytes": model.stat().st_size,
            "input_wav_path": str(input_wav.resolve()),
            "input_wav_sha256": _digest(audio),
            "input_wav_size_bytes": len(audio),
            "invocation_input_path": str(invocation_input.resolve()),
            "gpu": "auto",
            "language": "zh",
            "prompt": prompt,
            "max_context": 0,
            "max_len": 0,
            "started_at": "2026-08-19T00:00:00+00:00",
            "completed_at": "2026-08-19T00:01:00+00:00",
            "exit_code": 0,
            "stdout_sha256": _digest(b""),
            "stdout_size_bytes": 0,
            "stderr_sha256": _digest(b""),
            "stderr_size_bytes": 0,
            "output_srt_path": str(output_srt.resolve()),
            "output_srt_sha256": _digest(memo),
            "output_srt_size_bytes": len(memo),
        }
    )
    receipt = canonical_json_bytes(
        MemoBundledRunnerExecutionReceiptV1.model_validate_json(
            receipt_draft, strict=True
        )
    )
    receipt_path = _write(root, "subtitle-v2/memo-execution.v1.json", receipt)
    return {
        "contract": "memo-bundled-runner-execution-v1",
        "path": receipt_path.relative_to(root).as_posix(),
        "runner_path": str(runner.resolve()),
        "model_path": str(model.resolve()),
        "input_wav_path": input_wav.relative_to(root).as_posix(),
        "output_srt_path": output_srt.relative_to(root).as_posix(),
        "stdout_path": stdout.relative_to(root).as_posix(),
        "stderr_path": stderr.relative_to(root).as_posix(),
        "sha256": _digest(receipt),
        "size_bytes": len(receipt),
        "runner_sha256": _digest(runner.read_bytes()),
        "model_sha256": _digest(model.read_bytes()),
        "input_wav_sha256": _digest(audio),
        "output_srt_sha256": _digest(memo),
        "stdout_sha256": _digest(b""),
        "stderr_sha256": _digest(b""),
        "exit_code": 0,
    }


def _early_inputs(
    root: Path, *, cue_count: int = 4, audio: bytes | None = None
) -> bytes:
    audio = audio or _pcm_wav(max(6000, (cue_count + 1) * 1000))
    memo = _srt(cue_count)
    memo_execution = _memo_execution_lineage(root, audio=audio, memo=memo)
    handoff = _canonical(
        {
            "schema_version": 1,
            "contract": "normalized-audio-handoff-v1",
            "accepted_at": "fixture",
            "normalized_audio_sha256": _digest(audio),
            "normalized_audio_size_bytes": len(audio),
            "normalized_audio_duration_ms": cue_count * 1000,
        }
    )
    recognition = _canonical(
        {
            "schema_version": 1,
            "contract": "memo-recognition-evidence-v1",
            "memo_version": "1.7.5",
            "model": "ggml-large-v2.bin",
            "normalized_audio_sha256": _digest(audio),
            "normalized_audio_size_bytes": len(audio),
            "source_export_sha256": _digest(memo),
            "source_export_size_bytes": len(memo),
            "memo_execution_receipt": memo_execution,
            "tokens": [{"text": f"原文{cue}"} for cue in range(1, cue_count + 1)],
            "unresolved_findings": [],
        }
    )
    recognition_review_sha = _digest(b"fixture-recognition-review")
    recognition_audits = []
    for worker in ("worker-a", "worker-b"):
        audit_path = _write(
            root,
            f"subtitle-v2/audits/recognition-{worker}.json",
            _canonical(
                {
                    "schema_version": 1,
                    "contract": "memo-recognition-worker-audit-v1",
                    "episode_id": root.name,
                    "worker_id": worker,
                    "normalized_audio_sha256": _digest(audio),
                    "normalized_audio_size_bytes": len(audio),
                    "source_export_sha256": _digest(memo),
                    "source_export_size_bytes": len(memo),
                    "review_manifest_sha256": recognition_review_sha,
                    "token_export_sha256": _digest(memo),
                    "memo_execution_receipt_sha256": memo_execution["sha256"],
                    "reviewed_item_count": cue_count,
                    "qc_passed": True,
                    "accepted": True,
                    "unresolved_findings": [],
                }
            ),
        )
        recognition_audits.append(
            {
                "contract": "memo-recognition-worker-audit-v1",
                "worker_id": worker,
                **_ref(root, audit_path),
            }
        )
    recognition_acceptance = _canonical(
        {
            "schema_version": 1,
            "contract": "accepted-memo-recognition-v1",
            "accepted": True,
            "normalized_handoff_manifest_sha256": _digest(handoff),
            "normalized_audio_sha256": _digest(audio),
            "source_export_sha256": _digest(memo),
            "source_export_size_bytes": len(memo),
            "review_manifest_sha256": recognition_review_sha,
            "token_export_sha256": _digest(memo),
            "memo_execution_receipt": memo_execution,
            "reviewer": "agent-quorum",
            "episode_id": root.name,
            "agent_audits": recognition_audits,
            "unresolved_findings": [],
        }
    )
    cue_review_sha = _digest(b"fixture-cue-review")
    cue_audits = []
    for worker in ("worker-c", "worker-d"):
        audit_path = _write(
            root,
            f"subtitle-v2/audits/cue-{worker}.json",
            _canonical(
                {
                    "schema_version": 1,
                    "contract": "memo-cue-worker-audit-v1",
                    "episode_id": root.name,
                    "worker_id": worker,
                    "normalized_audio_sha256": _digest(audio),
                    "normalized_audio_size_bytes": len(audio),
                    "source_export_sha256": _digest(memo),
                    "source_export_size_bytes": len(memo),
                    "review_manifest_sha256": cue_review_sha,
                    "recognition_manifest_sha256": _digest(recognition),
                    "reviewed_item_count": cue_count,
                    "qc_passed": True,
                    "accepted": True,
                    "unresolved_findings": [],
                }
            ),
        )
        cue_audits.append(
            {
                "contract": "memo-cue-worker-audit-v1",
                "worker_id": worker,
                **_ref(root, audit_path),
            }
        )
    cue_acceptance = _canonical(
        {
            "schema_version": 1,
            "contract": "accepted-memo-gui-srt-v1",
            "accepted": True,
            "source_export_sha256": _digest(memo),
            "source_export_size_bytes": len(memo),
            "recognition_manifest_sha256": _digest(recognition),
            "review_manifest_sha256": cue_review_sha,
            "reviewer": "agent-quorum",
            "episode_id": root.name,
            "agent_audits": cue_audits,
            "unresolved_findings": [],
        }
    )
    roles = {
        "normalized_audio": audio,
        "normalized_handoff": handoff,
        "memo_srt": memo,
        "memo_recognition_evidence": recognition,
        "memo_recognition_acceptance": recognition_acceptance,
        "memo_cue_acceptance": cue_acceptance,
    }
    for role, raw in roles.items():
        _write(root, release._DEFAULT_INPUT_PATHS[role], raw)
    return memo


def _text_inputs(
    root: Path, *, cue_count: int = 4, unresolved: str = "major"
) -> tuple[bytes, bytes]:
    memo = (root / release._DEFAULT_INPUT_PATHS["memo_srt"]).read_bytes()
    major_risk = unresolved == "major"
    findings_a = [] if unresolved == "none" else [_record("甲", major_risk=major_risk)]
    findings_b = [] if unresolved == "none" else [_record("乙", major_risk=major_risk)]
    audit_a = _audit("A", cue_count, findings_a)
    audit_b = _audit("B", cue_count, findings_b)
    base_srt, base_ledger, base_needs = simple.merge_official_text_audits(
        srt_bytes=memo, audit_a_bytes=audit_a, audit_b_bytes=audit_b
    )
    if unresolved == "none":
        items: list[dict[str, object]] = []
    else:
        items = [
            {
                "a_proposals": ["甲"],
                "b_proposals": ["乙"],
                "b_risks": [],
                "confidence": "low",
                "cue_numbers": [2],
                "decision": "keep_unresolved",
                "evidence": "fixture",
                "major_risk": unresolved == "major",
                "original": "原文2",
                "reason": "fixture",
                "replacement": None,
            }
        ]
    arbitration = _canonical(
        {
            "schema_version": 1,
            "contract": simple._OFFICIAL_ARBITRATION_CONTRACT,
            "policy_version": simple._OFFICIAL_POLICY_VERSION,
            "episode_id": "fixture-episode",
            "input_hashes": {
                "srt_sha256": _digest(memo),
                "audit_a_sha256": _digest(audit_a),
                "audit_b_sha256": _digest(audit_b),
                "base_queue_sha256": _digest(base_needs),
            },
            "accepted_count": 0,
            "unresolved_count": len(items),
            "items": items,
        }
    )
    text_srt, text_ledger, unresolved_raw = simple.apply_official_arbitration(
        episode_id="fixture-episode",
        srt_bytes=memo,
        audit_a_bytes=audit_a,
        audit_b_bytes=audit_b,
        base_corrected_bytes=base_srt,
        base_ledger_bytes=base_ledger,
        base_needs_audio_bytes=base_needs,
        arbitration_bytes=arbitration,
    )
    roles = {
        "text_audit_a": audit_a,
        "text_audit_b": audit_b,
        "base_corrected_srt": base_srt,
        "base_consensus_ledger": base_ledger,
        "base_needs_audio": base_needs,
        "arbitration": arbitration,
        "text_corrected_srt": text_srt,
        "text_arbitration_ledger": text_ledger,
        "unresolved_components": unresolved_raw,
    }
    for role, raw in roles.items():
        _write(root, release._DEFAULT_INPUT_PATHS[role], raw)
    return text_srt, unresolved_raw


def _audio_inputs(
    root: Path,
    *,
    text_srt: bytes,
    unresolved_raw: bytes,
    population: str = "major",
    decision: str = "retain",
    include_qwen: bool = True,
    reason_code: str | None = None,
) -> None:
    evidence_root = root / "subtitle-work/memo-dual-audit-v1/evidence/cue-2"
    relative_root = evidence_root.relative_to(root).as_posix()
    clip = _write(root, relative_root + "/clip.wav", _pcm_wav())
    observed = "甲" if decision == "accept" else "原文2"
    segments = _write(
        root,
        relative_root + "/segments.json",
        _canonical(
            {"segments": [{"start_ms": 1000, "end_ms": 1900, "text": observed}]}
        ),
    )
    evidence_paths: dict[str, Path] = {}
    for family, model, adapter in (
        ("faster", "faster-whisper-large-v3", "faster-word-timestamps"),
        ("qwen", "Qwen3-ASR-1.7B", "qwen-forced-alignment"),
    ):
        engine = {
            "family": family,
            "model": model,
            "revision": "a" * 40,
            "adapter": adapter,
            "runtime": "fixture-runtime",
        }
        provider = _write(
            root,
            f"{relative_root}/{family}-provider.json",
            _canonical(
                {
                    "schema_version": 1,
                    "contract": release.ASR_PROVIDER_OUTPUT_CONTRACT,
                    "episode_id": "fixture-episode",
                    "component_id": "cue-2",
                    "engine": engine,
                    "clip": _ref(root, clip),
                    "completed": True,
                    "exit_code": 0,
                    "transcript": observed,
                    "segments": [
                        {"start_ms": 1000, "end_ms": 1900, "text": observed}
                    ],
                    "provider_result": {"provider": family, "result": "原文2"},
                }
            ),
        )
        raw_result = root / f"{relative_root}/{family}-raw-result.json"
        evidence_path = root / f"{relative_root}/{family}-evidence.json"
        release.build_asr_evidence(
            episode_root=root,
            episode_id="fixture-episode",
            component_id="cue-2",
            cue_numbers=(2,),
            normalized_audio=root / release._DEFAULT_INPUT_PATHS["normalized_audio"],
            clip=clip,
            clip_start_ms=1000,
            clip_end_ms=4000,
            target_start_ms=2000,
            target_end_ms=2900,
            family=family,
            model=model,
            revision="a" * 40,
            adapter=adapter,
            runtime="fixture-runtime",
            provider_output=provider,
            transcript=observed,
            segments_json=segments,
            raw_result_output=raw_result,
            evidence_output=evidence_path,
        )
        evidence_paths[family] = evidence_path
    major: list[dict[str, object]] = []
    nonmajor: list[dict[str, object]] = []
    if population == "major":
        evidence: dict[str, object] = {
            "clip": _ref(root, clip),
            "faster": {"file": _ref(root, evidence_paths["faster"])},
        }
        if include_qwen:
            evidence["qwen"] = {"file": _ref(root, evidence_paths["qwen"])}
        accepted = decision == "accept"
        major.append(
            {
                "component_id": "cue-2",
                "cue_numbers": [2],
                "decision": "accept_replacement" if accepted else "retain_memo_original",
                "reason_code": reason_code
                or ("dual_asr_consensus" if accepted else "dual_asr_conflict"),
                "original": {"2": "原文2"},
                "replacements": {"2": "甲"} if accepted else {},
                "evidence": evidence,
            }
        )
    elif population == "nonmajor":
        nonmajor.append(
            {
                "component_id": "cue-2",
                "cue_numbers": [2],
                "original": {"2": "原文2"},
            }
        )
    audio = _canonical(
        {
            "schema_version": 1,
            "contract": release.AUDIO_DECISIONS_CONTRACT,
            "episode_id": "fixture-episode",
            "source_srt_sha256": _digest(text_srt),
            "source_unresolved_sha256": _digest(unresolved_raw),
            "major_components": major,
            "nonmajor_retained_original_components": nonmajor,
        }
    )
    _write(root, release._DEFAULT_INPUT_PATHS["audio_decisions"], audio)


def _complete_fixture(
    root: Path,
    *,
    cue_count: int = 4,
    population: str = "major",
    decision: str = "retain",
) -> Path:
    _early_inputs(root, cue_count=cue_count)
    unresolved = "none" if population == "zero" else population
    text_srt, unresolved_raw = _text_inputs(
        root, cue_count=cue_count, unresolved=unresolved
    )
    _audio_inputs(
        root,
        text_srt=text_srt,
        unresolved_raw=unresolved_raw,
        population=population if population != "zero" else "zero",
        decision=decision,
    )
    request_path = release.init_request(root, episode_id="fixture-episode")
    release.seal_request(request_path)
    return request_path


def _refresh_audio_decision_refs(root: Path) -> None:
    audio_path = root / release._DEFAULT_INPUT_PATHS["audio_decisions"]
    audio = json.loads(audio_path.read_bytes())
    evidence_root = root / "subtitle-work/memo-dual-audit-v1/evidence/cue-2"
    audio["major_components"][0]["evidence"]["clip"] = _ref(
        root, evidence_root / "clip.wav"
    )
    for family in ("faster", "qwen"):
        audio["major_components"][0]["evidence"][family]["file"] = _ref(
            root, evidence_root / f"{family}-evidence.json"
        )
    audio_path.write_bytes(_canonical(audio))


def _mutate_evidence(root: Path, family: str, mutation) -> None:
    path = (
        root
        / f"subtitle-work/memo-dual-audit-v1/evidence/cue-2/{family}-evidence.json"
    )
    payload = json.loads(path.read_bytes())
    mutation(payload)
    path.write_bytes(_canonical(payload))
    _refresh_audio_decision_refs(root)


def test_init_status_progresses_to_finalize_without_future_hashes(tmp_path: Path) -> None:
    _early_inputs(tmp_path)
    request_path = release.init_request(tmp_path, episode_id="fixture-episode")
    request_raw = json.loads(request_path.read_bytes())
    assert request_raw["inputs"]["text_audit_a"]["sha256"] is None
    code, raw = release.status(release.load_request(request_path))
    assert code == 3
    assert json.loads(raw)["phase"] == "awaiting_text_audits"

    _write(
        tmp_path,
        release._DEFAULT_INPUT_PATHS["text_audit_a"],
        _audit("A", 4, [_record("甲")]),
    )
    _write(
        tmp_path,
        release._DEFAULT_INPUT_PATHS["text_audit_b"],
        _audit("B", 4, [_record("乙")]),
    )
    request = release.seal_request(request_path)
    code, raw = release.status(request)
    assert code == 3
    assert json.loads(raw)["phase"] == "awaiting_arbitration"

    text_srt, unresolved_raw = _text_inputs(tmp_path, unresolved="major")
    request = release.seal_request(request_path)
    code, raw = release.status(request)
    assert code == 3
    assert json.loads(raw)["phase"] == "awaiting_major_dual_asr"

    _audio_inputs(
        tmp_path, text_srt=text_srt, unresolved_raw=unresolved_raw, population="major"
    )
    assert release.main(["finalize", "--request", str(request_path)]) == 0
    output = tmp_path / "subtitle-release/memo-dual-audit-v1"
    assert {path.name for path in output.iterdir()} == {
        "release.srt",
        "release-ledger.json",
        "export-manifest.json",
        "STAGE5-HANDOFF.json",
    }
    assert json.loads((output / "release-ledger.json").read_bytes())["contract"] == (
        release.RELEASE_CONTRACT
    )
    assert json.loads((output / "STAGE5-HANDOFF.json").read_bytes())[
        "contract"
    ] == release.STAGE5_HANDOFF_CONTRACT


def test_non_2630_episode_and_conflict_retain_memo(tmp_path: Path) -> None:
    request_path = _complete_fixture(tmp_path, cue_count=3)
    bundle = release.finalize(release.load_request(request_path))
    cues = simple._parse_srt(bundle.release_srt)
    ledger = json.loads(bundle.release_ledger)
    assert len(cues) == 3
    assert cues[1].text == "原文2"
    assert ledger["cue_count"] == 3
    assert ledger["audio_audit"]["retained_major_component_count"] == 1


def test_zero_major_episode_is_valid(tmp_path: Path) -> None:
    request_path = _complete_fixture(tmp_path, population="zero")
    bundle = release.build_release(release.load_request(request_path))
    ledger = json.loads(bundle.release_ledger)
    assert ledger["audio_audit"]["major_component_count"] == 0
    assert ledger["audio_audit"]["major_audio_reviewed_count"] == 0


def test_nonmajor_unresolved_retains_memo_without_audio(tmp_path: Path) -> None:
    request_path = _complete_fixture(tmp_path, population="nonmajor")
    bundle = release.build_release(release.load_request(request_path))
    ledger = json.loads(bundle.release_ledger)
    assert simple._parse_srt(bundle.release_srt)[1].text == "原文2"
    assert ledger["audio_audit"]["major_component_count"] == 0
    assert ledger["audio_audit"]["nonmajor_retained_original_count"] == 1


def test_dual_asr_consensus_applies_replacement(tmp_path: Path) -> None:
    request_path = _complete_fixture(tmp_path, decision="accept")
    bundle = release.build_release(release.load_request(request_path))
    assert simple._parse_srt(bundle.release_srt)[1].text == "甲"


def test_missing_one_major_asr_fails_closed(tmp_path: Path) -> None:
    _early_inputs(tmp_path)
    text_srt, unresolved_raw = _text_inputs(tmp_path, unresolved="major")
    _audio_inputs(
        tmp_path,
        text_srt=text_srt,
        unresolved_raw=unresolved_raw,
        population="major",
        include_qwen=False,
    )
    request_path = release.init_request(tmp_path, episode_id="fixture-episode")
    with pytest.raises(release.SubtitleReleaseError, match="missing keys"):
        release.build_release(release.load_request(request_path))


def test_unknown_audio_category_fails_closed(tmp_path: Path) -> None:
    _early_inputs(tmp_path)
    text_srt, unresolved_raw = _text_inputs(tmp_path, unresolved="major")
    _audio_inputs(
        tmp_path,
        text_srt=text_srt,
        unresolved_raw=unresolved_raw,
        population="major",
        reason_code="invented_category",
    )
    request_path = release.init_request(tmp_path, episode_id="fixture-episode")
    with pytest.raises(release.SubtitleReleaseError, match="unknown audio reason"):
        release.build_release(release.load_request(request_path))


def test_missing_arbitration_major_risk_cannot_downgrade_component(
    tmp_path: Path,
) -> None:
    request_path = _complete_fixture(tmp_path)
    arbitration_path = tmp_path / release._DEFAULT_INPUT_PATHS["arbitration"]
    arbitration = json.loads(arbitration_path.read_bytes())
    del arbitration["items"][0]["major_risk"]
    arbitration_path.write_bytes(_canonical(arbitration))
    request = release.seal_request(request_path)
    with pytest.raises(release.SubtitleReleaseError, match="major_risk is required"):
        release.build_release(request)


def test_missing_raw_audit_major_risk_fails_official_merge(tmp_path: Path) -> None:
    memo = _srt()
    a = _record("甲")
    del a["major_risk"]
    with pytest.raises(simple.SimpleStep7Error, match="requires boolean major_risk"):
        simple.merge_official_text_audits(
            srt_bytes=memo,
            audit_a_bytes=_audit("A", 4, [a]),
            audit_b_bytes=_audit("B", 4, [_record("乙")]),
        )


def test_fake_clip_with_synchronized_hashes_is_not_audio_evidence(tmp_path: Path) -> None:
    request_path = _complete_fixture(tmp_path)
    evidence_root = tmp_path / "subtitle-work/memo-dual-audit-v1/evidence/cue-2"
    clip = evidence_root / "clip.wav"
    clip.write_bytes(b"clip")
    for family in ("faster", "qwen"):
        evidence_path = evidence_root / f"{family}-evidence.json"
        evidence = json.loads(evidence_path.read_bytes())
        evidence["clip"]["file"] = _ref(tmp_path, clip)
        evidence_path.write_bytes(_canonical(evidence))
    _refresh_audio_decision_refs(tmp_path)
    request = release.seal_request(request_path)
    with pytest.raises(release.SubtitleReleaseError, match="valid PCM WAV"):
        release.build_release(request)


def test_valid_same_duration_clip_from_wrong_audio_window_fails_closed(
    tmp_path: Path,
) -> None:
    request_path = _complete_fixture(tmp_path)
    evidence_root = tmp_path / "subtitle-work/memo-dual-audit-v1/evidence/cue-2"
    clip = evidence_root / "clip.wav"
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16_000)
        handle.writeframes(b"\x01\x00" * (16_000 * 3))
    clip.write_bytes(buffer.getvalue())
    for family in ("faster", "qwen"):
        evidence_path = evidence_root / f"{family}-evidence.json"
        evidence = json.loads(evidence_path.read_bytes())
        evidence["clip"]["file"] = _ref(tmp_path, clip)
        evidence_path.write_bytes(_canonical(evidence))
    _refresh_audio_decision_refs(tmp_path)
    request = release.seal_request(request_path)
    with pytest.raises(release.SubtitleReleaseError, match="normalized-audio window"):
        release.build_release(request)


def test_builder_refuses_transcript_not_derived_from_provider_output(
    tmp_path: Path,
) -> None:
    _early_inputs(tmp_path)
    text_srt, unresolved_raw = _text_inputs(tmp_path)
    _audio_inputs(tmp_path, text_srt=text_srt, unresolved_raw=unresolved_raw)
    evidence_root = tmp_path / "subtitle-work/memo-dual-audit-v1/evidence/cue-2"
    with pytest.raises(release.SubtitleReleaseError, match="differs from provider"):
        release.build_asr_evidence(
            episode_root=tmp_path,
            episode_id="fixture-episode",
            component_id="cue-2",
            cue_numbers=(2,),
            normalized_audio=tmp_path / release._DEFAULT_INPUT_PATHS["normalized_audio"],
            clip=evidence_root / "clip.wav",
            clip_start_ms=1000,
            clip_end_ms=4000,
            target_start_ms=2000,
            target_end_ms=2900,
            family="faster",
            model="faster-whisper-large-v3",
            revision="a" * 40,
            adapter="faster-word-timestamps",
            runtime="fixture-runtime",
            provider_output=evidence_root / "faster-provider.json",
            transcript="偽造文字",
            segments_json=None,
            raw_result_output=evidence_root / "should-not-exist-raw.json",
            evidence_output=evidence_root / "should-not-exist-evidence.json",
        )
    assert not (evidence_root / "should-not-exist-raw.json").exists()


def test_model_adapter_only_json_is_not_asr_evidence(tmp_path: Path) -> None:
    request_path = _complete_fixture(tmp_path)
    evidence_path = (
        tmp_path
        / "subtitle-work/memo-dual-audit-v1/evidence/cue-2/faster-evidence.json"
    )
    evidence_path.write_bytes(_canonical({"model": "faster", "adapter": "faster"}))
    _refresh_audio_decision_refs(tmp_path)
    request = release.seal_request(request_path)
    with pytest.raises(release.SubtitleReleaseError, match="missing keys"):
        release.build_release(request)


@pytest.mark.parametrize(
    ("family", "mutation", "message"),
    [
        (
            "faster",
            lambda item: item.update({"episode_id": "wrong-episode"}),
            "another episode",
        ),
        (
            "faster",
            lambda item: item["normalized_audio"].update({"sha256": "0" * 64}),
            "audio binding",
        ),
        (
            "faster",
            lambda item: item["target"].update({"start_ms": 2001}),
            "target window",
        ),
        (
            "faster",
            lambda item: item["engine"].update(
                {"model": "not-the-configured-family", "adapter": "not-faster"}
            ),
            "model family",
        ),
        (
            "qwen",
            lambda item: item["engine"].update({"family": "faster"}),
            "wrong ASR family",
        ),
    ],
)
def test_wrong_typed_asr_identity_fails_closed(
    tmp_path: Path, family: str, mutation, message: str
) -> None:
    request_path = _complete_fixture(tmp_path)
    _mutate_evidence(tmp_path, family, mutation)
    request = release.seal_request(request_path)
    with pytest.raises(release.SubtitleReleaseError, match=message):
        release.build_release(request)


def test_tampered_provider_result_fails_closed(tmp_path: Path) -> None:
    request_path = _complete_fixture(tmp_path)
    provider = (
        tmp_path
        / "subtitle-work/memo-dual-audit-v1/evidence/cue-2/faster-provider.json"
    )
    provider.write_bytes(b"tampered")
    with pytest.raises(release.SubtitleReleaseError, match="evidence drift"):
        release.build_release(release.load_request(request_path))


def test_official_outputs_contain_no_legacy_degraded_provenance(tmp_path: Path) -> None:
    request_path = _complete_fixture(tmp_path)
    bundle = release.build_release(release.load_request(request_path))
    for raw in (
        bundle.release_ledger,
        bundle.export_manifest,
        bundle.stage5_handoff,
    ):
        assert b"degraded" not in raw
        assert simple._ARBITRATION_MIGRATED_FROM_SHA256.encode() not in raw


def test_tampered_sealed_input_fails_status(tmp_path: Path) -> None:
    request_path = _complete_fixture(tmp_path)
    audit = tmp_path / release._DEFAULT_INPUT_PATHS["text_audit_a"]
    audit.write_bytes(b"tampered")
    with pytest.raises(release.SubtitleReleaseError, match="sealed input drift"):
        release.status(release.load_request(request_path))


def test_release_rejects_missing_or_tampered_quorum_audit(tmp_path: Path) -> None:
    request_path = _complete_fixture(tmp_path)
    receipt_path = tmp_path / release._DEFAULT_INPUT_PATHS[
        "memo_recognition_acceptance"
    ]
    receipt = json.loads(receipt_path.read_bytes())
    audit_path = tmp_path / receipt["agent_audits"][0]["path"]
    audit_path.write_bytes(b"tampered")
    with pytest.raises(release.SubtitleReleaseError, match="evidence drift"):
        release.build_release(release.load_request(request_path))

    receipt["agent_audits"][0]["path"] = "subtitle-v2/audits/missing.json"
    receipt_path.write_bytes(_canonical(receipt))
    request = release.seal_request(request_path)
    with pytest.raises(release.SubtitleReleaseError, match="missing major audio evidence"):
        release.build_release(request)


def test_release_rejects_cross_episode_worker_audit_with_fresh_hash(
    tmp_path: Path,
) -> None:
    request_path = _complete_fixture(tmp_path)
    receipt_path = tmp_path / release._DEFAULT_INPUT_PATHS["memo_cue_acceptance"]
    receipt = json.loads(receipt_path.read_bytes())
    audit_path = tmp_path / receipt["agent_audits"][0]["path"]
    audit = json.loads(audit_path.read_bytes())
    audit["episode_id"] = "another-episode"
    audit_path.write_bytes(_canonical(audit))
    receipt["agent_audits"][0].update(_ref(tmp_path, audit_path))
    receipt_path.write_bytes(_canonical(receipt))
    request = release.seal_request(request_path)
    with pytest.raises(release.SubtitleReleaseError, match="source/review binding"):
        release.build_release(request)


@pytest.mark.parametrize(
    "ref_field",
    [
        "path",
        "runner_path",
        "model_path",
        "output_srt_path",
        "stdout_path",
        "stderr_path",
    ],
)
def test_release_fresh_replay_rejects_tampered_memo_execution_artifact(
    tmp_path: Path, ref_field: str
) -> None:
    request_path = _complete_fixture(tmp_path)
    recognition = json.loads(
        (tmp_path / release._DEFAULT_INPUT_PATHS["memo_recognition_evidence"]).read_bytes()
    )
    ref = recognition["memo_execution_receipt"]
    artifact = Path(ref[ref_field])
    if not artifact.is_absolute():
        artifact = tmp_path / artifact
    artifact.write_bytes(artifact.read_bytes() + b"tamper")

    with pytest.raises(
        release.SubtitleReleaseError, match="Memo execution|sealed input drift"
    ):
        release.build_release(release.load_request(request_path))


def test_release_rejects_missing_memo_execution_receipt(tmp_path: Path) -> None:
    request_path = _complete_fixture(tmp_path)
    recognition_path = tmp_path / release._DEFAULT_INPUT_PATHS[
        "memo_recognition_evidence"
    ]
    acceptance_path = tmp_path / release._DEFAULT_INPUT_PATHS[
        "memo_recognition_acceptance"
    ]
    recognition = json.loads(recognition_path.read_bytes())
    acceptance = json.loads(acceptance_path.read_bytes())
    recognition["memo_execution_receipt"]["path"] = "subtitle-v2/missing.json"
    acceptance["memo_execution_receipt"]["path"] = "subtitle-v2/missing.json"
    recognition_path.write_bytes(_canonical(recognition))
    acceptance_path.write_bytes(_canonical(acceptance))
    request = release.seal_request(request_path)

    with pytest.raises(release.SubtitleReleaseError, match="Memo execution"):
        release.build_release(request)


def test_official_release_rejects_legacy_recognition_without_execution_lineage(
    tmp_path: Path,
) -> None:
    request_path = _complete_fixture(tmp_path)
    recognition_path = tmp_path / release._DEFAULT_INPUT_PATHS[
        "memo_recognition_evidence"
    ]
    acceptance_path = tmp_path / release._DEFAULT_INPUT_PATHS[
        "memo_recognition_acceptance"
    ]
    recognition = json.loads(recognition_path.read_bytes())
    acceptance = json.loads(acceptance_path.read_bytes())
    recognition.pop("memo_execution_receipt")
    acceptance.pop("memo_execution_receipt")
    recognition_path.write_bytes(_canonical(recognition))
    acceptance_path.write_bytes(_canonical(acceptance))
    request = release.seal_request(request_path)

    with pytest.raises(release.SubtitleReleaseError, match="invalid Memo execution"):
        release.build_release(request)


def test_release_rejects_execution_reference_path_escape(tmp_path: Path) -> None:
    request_path = _complete_fixture(tmp_path)
    recognition_path = tmp_path / release._DEFAULT_INPUT_PATHS[
        "memo_recognition_evidence"
    ]
    acceptance_path = tmp_path / release._DEFAULT_INPUT_PATHS[
        "memo_recognition_acceptance"
    ]
    recognition = json.loads(recognition_path.read_bytes())
    acceptance = json.loads(acceptance_path.read_bytes())
    recognition["memo_execution_receipt"]["path"] = "../another-episode/receipt.json"
    acceptance["memo_execution_receipt"]["path"] = "../another-episode/receipt.json"
    recognition_path.write_bytes(_canonical(recognition))
    acceptance_path.write_bytes(_canonical(acceptance))
    request = release.seal_request(request_path)

    with pytest.raises(release.SubtitleReleaseError, match="invalid Memo execution"):
        release.build_release(request)


def test_tampered_dual_asr_evidence_fails_closed(tmp_path: Path) -> None:
    request_path = _complete_fixture(tmp_path)
    evidence = (
        tmp_path
        / "subtitle-work/memo-dual-audit-v1/evidence/cue-2/faster-evidence.json"
    )
    evidence.write_bytes(b"tampered")
    with pytest.raises(release.SubtitleReleaseError, match="evidence drift"):
        release.build_release(release.load_request(request_path))


def test_request_path_escape_is_rejected(tmp_path: Path) -> None:
    _early_inputs(tmp_path)
    request_path = release.init_request(tmp_path, episode_id="fixture-episode")
    payload = json.loads(request_path.read_bytes())
    payload["inputs"]["normalized_audio"]["path"] = "../escape.wav"
    request_path.write_bytes(_canonical(payload))
    with pytest.raises(release.SubtitleReleaseError, match="escapes"):
        release.load_request(request_path)


def test_init_rejects_episode_id_different_from_episode_directory(
    tmp_path: Path,
) -> None:
    with pytest.raises(release.SubtitleReleaseError, match="episode directory"):
        release.init_request(tmp_path, episode_id="wrong-episode")


def test_load_rejects_tampered_episode_id(tmp_path: Path) -> None:
    _early_inputs(tmp_path)
    request_path = release.init_request(tmp_path, episode_id="fixture-episode")
    payload = json.loads(request_path.read_bytes())
    payload["episode_id"] = "tampered-episode"
    request_path.write_bytes(_canonical(payload))
    with pytest.raises(release.SubtitleReleaseError, match="episode directory"):
        release.load_request(request_path)


def test_cli_status_output_is_replaceable_across_phase_transitions(
    tmp_path: Path,
) -> None:
    _early_inputs(tmp_path)
    request_path = release.init_request(tmp_path, episode_id="fixture-episode")
    status_path = tmp_path / "subtitle-release/memo-dual-audit-v1/status.json"
    assert (
        release.main(
            [
                "status",
                "--request",
                str(request_path),
                "--status-output",
                str(status_path),
            ]
        )
        == 3
    )
    assert json.loads(status_path.read_bytes())["phase"] == "awaiting_text_audits"

    text_srt, unresolved_raw = _text_inputs(tmp_path, unresolved="major")
    assert (
        release.main(
            [
                "seal",
                "--request",
                str(request_path),
                "--status-output",
                str(status_path),
            ]
        )
        == 3
    )
    assert json.loads(status_path.read_bytes())["phase"] == "awaiting_major_dual_asr"

    _audio_inputs(
        tmp_path,
        text_srt=text_srt,
        unresolved_raw=unresolved_raw,
        population="major",
    )
    assert (
        release.main(
            [
                "finalize",
                "--request",
                str(request_path),
                "--status-output",
                str(status_path),
            ]
        )
        == 0
    )
    assert json.loads(status_path.read_bytes())["phase"] == "complete"


def test_partial_destination_conflict_writes_nothing_else(tmp_path: Path) -> None:
    request_path = _complete_fixture(tmp_path)
    request = release.load_request(request_path)
    output = tmp_path / request.output_directory
    output.mkdir(parents=True)
    (output / "release-ledger.json").write_bytes(b"conflict")
    with pytest.raises(release.SubtitleReleaseError, match="overwrite refused"):
        release.finalize(request)
    assert not (output / "release.srt").exists()
    assert not (output / "export-manifest.json").exists()
    assert not (output / "STAGE5-HANDOFF.json").exists()


def test_same_input_rerun_is_byte_identical(tmp_path: Path) -> None:
    request_path = _complete_fixture(tmp_path)
    request = release.load_request(request_path)
    first = release.finalize(request)
    second = release.finalize(request)
    assert first == second


def test_init_rerun_is_byte_identical(tmp_path: Path) -> None:
    _early_inputs(tmp_path)
    first = release.init_request(tmp_path, episode_id="fixture-episode")
    first_raw = first.read_bytes()
    second = release.init_request(tmp_path, episode_id="fixture-episode")
    assert second == first
    assert second.read_bytes() == first_raw


def test_production_runner_has_no_formal_module_import() -> None:
    source = inspect.getsource(release)
    assert "agents.brook.podcast_subtitles.module" not in source
    assert "PodcastSubtitleV2" not in source
    assert "VerifiedProjection" not in source


def test_prepare_major_audio_and_run_both_official_asr_families(
    tmp_path: Path,
) -> None:
    _early_inputs(
        tmp_path,
        audio=_pcm_wav(6000, sample_width_bytes=3, channels=2),
    )
    _text_inputs(tmp_path, unresolved="major")
    request_path = release.init_request(tmp_path, episode_id="fixture-episode")
    request = release.seal_request(request_path)

    plan_raw = release.prepare_major_audio(request, padding_ms=1000)
    plan_path = tmp_path / release.DEFAULT_MAJOR_AUDIO_PLAN
    assert plan_path.read_bytes() == plan_raw
    plan = json.loads(plan_raw)
    assert plan["contract"] == release.MAJOR_AUDIO_PLAN_CONTRACT
    assert len(plan["jobs"]) == 1
    assert plan["jobs"][0]["target"] == {"start_ms": 2000, "end_ms": 2900}
    clip = tmp_path / plan["jobs"][0]["clip"]["path"]
    with wave.open(str(clip), "rb") as handle:
        assert handle.getnchannels() == 2
        assert handle.getsampwidth() == 3
        assert handle.getnframes() == 16_000 * 2_900 // 1000

    calls: list[str] = []

    def fake_provider(**kwargs):
        calls.append(kwargs["family"])
        return {
            "transcript": "原文2",
            "segments": [{"start_ms": 1000, "end_ms": 1900, "text": "原文2"}],
            "provider_result": {
                "family": kwargs["family"],
                "completed": True,
                "clip_sha256": _digest(kwargs["clip"].read_bytes()),
            },
            "runtime": f"{kwargs['family']}-fixture-runtime",
        }

    for family, model in (
        ("faster", "Systran/faster-whisper-large-v3"),
        ("qwen", "Qwen/Qwen3-ASR-1.7B"),
    ):
        manifest_raw = release.run_major_asr(
            episode_root=tmp_path,
            plan_path=plan_path,
            family=family,
            model=model,
            revision="a" * 40,
            device="cpu",
            compute_type="int8",
            forced_aligner="Qwen/Qwen3-ForcedAligner-0.6B" if family == "qwen" else None,
            forced_aligner_revision="b" * 40 if family == "qwen" else None,
            provider_runner=fake_provider,
        )
        manifest = json.loads(manifest_raw)
        assert manifest["contract"] == release.MAJOR_ASR_RUN_CONTRACT
        evidence_path = tmp_path / manifest["evidence"][0]["file"]["path"]
        evidence = json.loads(evidence_path.read_bytes())
        assert evidence["engine"]["family"] == family
        assert evidence["component_id"] == "cue-2"
    assert calls == ["faster", "qwen"]
    for family, model in (
        ("faster", "Systran/faster-whisper-large-v3"),
        ("qwen", "Qwen/Qwen3-ASR-1.7B"),
    ):
        release.run_major_asr(
            episode_root=tmp_path,
            plan_path=plan_path,
            family=family,
            model=model,
            revision="a" * 40,
            device="cpu",
            compute_type="int8",
            forced_aligner="Qwen/Qwen3-ForcedAligner-0.6B" if family == "qwen" else None,
            forced_aligner_revision="b" * 40 if family == "qwen" else None,
            provider_runner=fake_provider,
        )
    assert calls == ["faster", "qwen"]

    decisions = json.loads(
        release.build_audio_decisions(
            request,
            faster_manifest=(
                tmp_path
                / "subtitle-work/memo-dual-audit-v1/major-audio/asr/faster/manifest.json"
            ),
            qwen_manifest=(
                tmp_path
                / "subtitle-work/memo-dual-audit-v1/major-audio/asr/qwen/manifest.json"
            ),
        )
    )
    assert decisions["major_components"][0]["decision"] == "retain_memo_original"


def test_audio_decision_accepts_only_exact_audit_candidate_and_replays(
    tmp_path: Path,
) -> None:
    _early_inputs(tmp_path)
    _text_inputs(tmp_path, unresolved="major")
    request_path = release.init_request(tmp_path, episode_id="fixture-episode")
    request = release.load_request(request_path)
    release.prepare_major_audio(request, padding_ms=1000)
    plan_path = tmp_path / release.DEFAULT_MAJOR_AUDIO_PLAN

    def candidate_provider(**_kwargs):
        return {
            "transcript": "甲",
            "segments": [{"start_ms": 1000, "end_ms": 1900, "text": "甲"}],
            "provider_result": {"completed": True},
            "runtime": "fixture-runtime",
        }

    for family, model in (
        ("faster", "Systran/faster-whisper-large-v3"),
        ("qwen", "Qwen/Qwen3-ASR-1.7B"),
    ):
        release.run_major_asr(
            episode_root=tmp_path,
            plan_path=plan_path,
            family=family,
            model=model,
            revision="a" * 40,
            device="cpu",
            compute_type="int8",
            forced_aligner="Qwen/Qwen3-ForcedAligner-0.6B" if family == "qwen" else None,
            forced_aligner_revision="b" * 40 if family == "qwen" else None,
            provider_runner=candidate_provider,
        )
    decisions_raw = release.build_audio_decisions(
        request,
        faster_manifest=(
            tmp_path
            / "subtitle-work/memo-dual-audit-v1/major-audio/asr/faster/manifest.json"
        ),
        qwen_manifest=(
            tmp_path / "subtitle-work/memo-dual-audit-v1/major-audio/asr/qwen/manifest.json"
        ),
    )
    decisions = json.loads(decisions_raw)
    assert decisions["major_components"][0]["replacements"] == {"2": "甲"}
    request = release.seal_request(request_path)
    assert simple._parse_srt(release.build_release(request).release_srt)[1].text == "甲"

    decisions["major_components"][0]["replacements"] = {"2": "任意注入"}
    decision_path = tmp_path / release._DEFAULT_INPUT_PATHS["audio_decisions"]
    decision_path.write_bytes(_canonical(decisions))
    request = release.seal_request(request_path)
    with pytest.raises(release.SubtitleReleaseError, match="fresh audit/dual-ASR"):
        release.build_release(request)


def test_audio_decision_candidates_ignore_explicit_null_audit_proposals() -> None:
    unresolved_item = {
        "a_proposals": [None],
        "b_proposals": ["既有文字候選"],
    }

    assert release._audit_candidates(unresolved_item) == ("既有文字候選",)


def test_prepare_major_audio_zero_major_is_complete_empty_plan(tmp_path: Path) -> None:
    _early_inputs(tmp_path)
    _text_inputs(tmp_path, unresolved="none")
    request = release.load_request(
        release.init_request(tmp_path, episode_id="fixture-episode")
    )
    plan = json.loads(release.prepare_major_audio(request))
    assert plan["jobs"] == []
    assert plan["major_component_count"] == 0


def test_audio_decisions_retain_nonmajor_without_provider_calls(tmp_path: Path) -> None:
    _early_inputs(tmp_path)
    _text_inputs(tmp_path, unresolved="nonmajor")
    request = release.load_request(
        release.init_request(tmp_path, episode_id="fixture-episode")
    )
    release.prepare_major_audio(request)
    plan_path = tmp_path / release.DEFAULT_MAJOR_AUDIO_PLAN

    def forbidden_provider(**_kwargs):
        raise AssertionError("zero-major plan must not invoke a provider")

    for family, model in (
        ("faster", "Systran/faster-whisper-large-v3"),
        ("qwen", "Qwen/Qwen3-ASR-1.7B"),
    ):
        release.run_major_asr(
            episode_root=tmp_path,
            plan_path=plan_path,
            family=family,
            model=model,
            revision="a" * 40,
            device="cpu",
            compute_type="int8",
            forced_aligner="Qwen/Qwen3-ForcedAligner-0.6B" if family == "qwen" else None,
            forced_aligner_revision="b" * 40 if family == "qwen" else None,
            provider_runner=forbidden_provider,
        )
    decisions = json.loads(
        release.build_audio_decisions(
            request,
            faster_manifest=(
                tmp_path
                / "subtitle-work/memo-dual-audit-v1/major-audio/asr/faster/manifest.json"
            ),
            qwen_manifest=(
                tmp_path
                / "subtitle-work/memo-dual-audit-v1/major-audio/asr/qwen/manifest.json"
            ),
        )
    )
    assert decisions["major_components"] == []
    assert decisions["nonmajor_retained_original_components"][0]["cue_numbers"] == [2]


def test_run_major_asr_rejects_plan_tamper_and_unpinned_revision(
    tmp_path: Path,
) -> None:
    _early_inputs(tmp_path)
    _text_inputs(tmp_path, unresolved="major")
    request = release.load_request(
        release.init_request(tmp_path, episode_id="fixture-episode")
    )
    release.prepare_major_audio(request)
    plan_path = tmp_path / release.DEFAULT_MAJOR_AUDIO_PLAN
    plan = json.loads(plan_path.read_bytes())
    plan["jobs"][0]["clip"]["path"] = "../escape.wav"
    plan_path.write_bytes(_canonical(plan))
    with pytest.raises(release.SubtitleReleaseError, match="escapes"):
        release.run_major_asr(
            episode_root=tmp_path,
            plan_path=plan_path,
            family="faster",
            model="Systran/faster-whisper-large-v3",
            revision="a" * 40,
            device="cpu",
            compute_type="int8",
        )


def test_major_asr_loads_once_and_resumes_only_missing_components(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _early_inputs(tmp_path)
    _text_inputs(tmp_path, unresolved="major")
    request = release.load_request(
        release.init_request(tmp_path, episode_id="fixture-episode")
    )
    release.prepare_major_audio(request, padding_ms=1000)
    plan_path = tmp_path / release.DEFAULT_MAJOR_AUDIO_PLAN
    plan = json.loads(plan_path.read_bytes())
    second = json.loads(json.dumps(plan["jobs"][0]))
    second["component_id"] = "cue-3"
    second["cue_numbers"] = [3]
    second["target"] = {"start_ms": 3000, "end_ms": 3900}
    second_clip = tmp_path / second["clip"]["path"].replace("cue-2", "cue-3")
    second_clip.parent.mkdir(parents=True, exist_ok=True)
    source_clip = tmp_path / plan["jobs"][0]["clip"]["path"]
    second_clip.write_bytes(source_clip.read_bytes())
    second["clip"]["path"] = second_clip.relative_to(tmp_path).as_posix()
    plan["jobs"].append(second)
    plan["major_component_count"] = 2
    plan_path.write_bytes(_canonical(plan))

    factory_calls = 0
    provider_calls: list[str] = []

    def failing_factory(**_kwargs):
        nonlocal factory_calls
        factory_calls += 1

        def runner(**kwargs):
            provider_calls.append(kwargs["clip"].stem)
            if kwargs["clip"].stem == "cue-3":
                raise release.SubtitleReleaseError("fixture interruption")
            return {
                "transcript": "原文2",
                "segments": [
                    {"start_ms": 1000, "end_ms": 1900, "text": "原文2"}
                ],
                "provider_result": {"completed": True},
                "runtime": "fixture-runtime",
            }

        return runner

    monkeypatch.setattr(release, "_create_major_provider_runner", failing_factory)
    with pytest.raises(release.SubtitleReleaseError, match="fixture interruption"):
        release.run_major_asr(
            episode_root=tmp_path,
            plan_path=plan_path,
            family="faster",
            model="Systran/faster-whisper-large-v3",
            revision="a" * 40,
            device="cpu",
            compute_type="int8",
        )
    assert factory_calls == 1
    assert provider_calls == ["cue-2", "cue-3"]

    resumed_calls: list[str] = []

    def resumed_factory(**_kwargs):
        def runner(**kwargs):
            resumed_calls.append(kwargs["clip"].stem)
            return {
                "transcript": "原文3",
                "segments": [
                    {"start_ms": 2000, "end_ms": 2900, "text": "原文3"}
                ],
                "provider_result": {"completed": True},
                "runtime": "fixture-runtime",
            }

        return runner

    monkeypatch.setattr(release, "_create_major_provider_runner", resumed_factory)
    release.run_major_asr(
        episode_root=tmp_path,
        plan_path=plan_path,
        family="faster",
        model="Systran/faster-whisper-large-v3",
        revision="a" * 40,
        device="cpu",
        compute_type="int8",
    )
    assert resumed_calls == ["cue-3"]

    monkeypatch.setattr(
        release,
        "_create_major_provider_runner",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("must not reload")),
    )
    release.run_major_asr(
        episode_root=tmp_path,
        plan_path=plan_path,
        family="faster",
        model="Systran/faster-whisper-large-v3",
        revision="a" * 40,
        device="cpu",
        compute_type="int8",
    )
    with pytest.raises(release.SubtitleReleaseError, match="immutable 40-hex"):
        release.run_major_asr(
            episode_root=tmp_path,
            plan_path=plan_path,
            family="faster",
            model="Systran/faster-whisper-large-v3",
            revision="unpinned",
            device="cpu",
            compute_type="int8",
        )


def test_moboo_legacy_bundle_read_compatibility() -> None:
    root = Path(r"G:\Footages\20260814 抹布\subtitle-v2\degraded-audio-release-v1")
    if not root.is_dir():
        pytest.skip("read-only Moboo forensic fixture unavailable")
    raw = release.verify_legacy_bundle(
        root,
        expected_sha256=(
            "8cf28558050e9c5d7cf4fbbcfa430fda9ba534acf20297ac7f4a0b49a674681c"
        ),
    )
    result = json.loads(raw)
    assert result["compatible"] is True
    assert result["cue_count"] == 2630
