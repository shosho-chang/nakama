from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip(
    "scripts.podcast_subtitle_recognition_pilot",
    reason="legacy pilot CLI is outside the Memo-first Subtitle V2 implementation scope",
)

from agents.brook.podcast_subtitles.hashing import canonical_json_bytes, hash_object, sha256_bytes
from agents.brook.podcast_subtitles.legacy_v1_recognition import (
    _slim_aligned_segments,
    build_legacy_v1_whisperx_adapter,
    legacy_v1_initial_prompt,
    load_legacy_v1_manifest,
)
from agents.brook.podcast_subtitles.ports import (
    AdapterInputError as RecognitionAdapterInputError,
)
from agents.brook.podcast_subtitles.ports import RecognitionModelIdentity, RecognitionRequest
from agents.brook.podcast_subtitles.recognition_pilot import (
    run_recognition_pilot,
    verify_recognition_pilot,
)
from agents.brook.podcast_subtitles.transcript_gold import (
    AudioClipBinding,
    TranscriptAnnotationPacket,
    TranscriptAnnotationProtocol,
)
from scripts.podcast_subtitle_recognition_pilot import build_parser
from scripts.podcast_subtitle_recognition_pilot import main as pilot_cli_main
from shared.schemas.podcast_subtitles_v2 import ArtifactDigest, EvidenceToken, RecognitionEvidence


class FakeRecognizer:
    def __init__(
        self, *, interrupt_after: int | None = None, language: str = "zh-TW"
    ) -> None:
        self.recognize_calls: list[RecognitionRequest] = []
        self.verify_calls: list[RecognitionRequest] = []
        self.interrupt_after = interrupt_after
        self.language = language
        runtime_components = (("fake-runtime", "1"),)
        self._identity = RecognitionModelIdentity(
            adapter_name="fake-exact-clip",
            adapter_version="1",
            model="fake-model",
            model_version="a" * 40,
            aligner="fake-aligner",
            aligner_version="b" * 40,
            runtime_components=runtime_components,
            runtime_hash=hash_object({"runtime_components": runtime_components}),
            adapter_code_hash="d" * 64,
            config_hash="e" * 64,
            execution_mode="fixture",
        )

    @property
    def identity(self) -> RecognitionModelIdentity:
        return self._identity

    def recognize(self, request: RecognitionRequest) -> RecognitionEvidence:
        if self.interrupt_after is not None and len(self.recognize_calls) >= self.interrupt_after:
            raise RuntimeError("simulated interruption")
        self.recognize_calls.append(request)
        assert request.language_hint == "zh-TW"
        assert request.context_policy.context_bytes == b""
        raw = canonical_json_bytes(
            {"invocation_id": request.invocation_id, "text": "測試"}
        )
        raw_dir = Path(request.raw_output_dir or "")
        raw_dir.mkdir(parents=True, exist_ok=True)
        path = raw_dir / "provider-output.json"
        path.write_bytes(raw)
        return RecognitionEvidence(
            episode_id=request.episode_id,
            invocation_id=request.invocation_id,
            adapter=self.identity.adapter_name,
            model=f"{self.identity.model}@{self.identity.model_version}",
            language=self.language,
            config_hash=self.identity.config_hash,
            raw_output=ArtifactDigest(
                uri=path.resolve().as_uri(), sha256=sha256_bytes(raw), size_bytes=len(raw)
            ),
            raw_output_hash=sha256_bytes(raw),
            normalized_audio_hash=request.expected_normalized_audio_hash,
            tokens=(EvidenceToken(id="token-1", text="測試", start_ms=0, end_ms=1),),
        )

    def verify(
        self,
        evidence: RecognitionEvidence,
        *,
        request: RecognitionRequest,
        raw_output: bytes,
    ) -> RecognitionEvidence:
        self.verify_calls.append(request)
        assert sha256_bytes(raw_output) == evidence.raw_output_hash
        assert request.context_policy.context_bytes == b""
        return evidence


def _formal_inputs(tmp_path: Path, count: int = 3) -> tuple[Path, Path]:
    clips_dir = tmp_path / "clips"
    clips_dir.mkdir()
    normalized = b"full-normalized-audio"
    clips = []
    for index in range(count):
        content = f"clip-{index}".encode()
        clip_id = f"clip-{index + 1:03d}"
        (clips_dir / f"{clip_id}.wav").write_bytes(content)
        clips.append(
            AudioClipBinding.build(
                clip_id=clip_id,
                start_ms=index * 10,
                end_ms=index * 10 + 5,
                clip_audio_hash=sha256_bytes(content),
                clip_audio_size_bytes=len(content),
                normalized_audio_hash=sha256_bytes(normalized),
                normalized_audio_size_bytes=len(normalized),
            )
        )
    packet = TranscriptAnnotationPacket.build(
        packet_id="packet-1",
        episode_id="episode-1",
        normalized_audio_hash=sha256_bytes(normalized),
        normalized_audio_size_bytes=len(normalized),
        instruction_profile_id="audio-only-v1",
        protocol=TranscriptAnnotationProtocol(protocol_id="formal-v1"),
        clips=clips,
    )
    packet_path = tmp_path / "packet.json"
    packet_path.write_bytes(packet.canonical_bytes())
    return packet_path, clips_dir


def _interrupted_prefix(tmp_path: Path) -> tuple[Path, Path, Path]:
    packet, clips = _formal_inputs(tmp_path, 3)
    output = tmp_path / "output"
    with pytest.raises(RuntimeError, match="simulated"):
        run_recognition_pilot(
            annotation_packet_path=packet,
            clips_dir=clips,
            output_dir=output,
            system_id="qwen-primary",
            adapter=FakeRecognizer(interrupt_after=1),
        )
    return packet, clips, output


def _legacy_manifest(tmp_path: Path, hotwords: tuple[str, ...] = ("主持人", "來賓")) -> Path:
    path = tmp_path / "legacy-v1-manifest.json"
    path.write_text(
        json.dumps(
            {
                "stage": "gen",
                "completed_at": "2026-04-15T12:00:00Z",
                "audio": "episode.wav",
                "model": "large-v3",
                "language": "zh",
                "segments": 42,
                "cues": 40,
                "words": 1234,
                "hotwords": hotwords,
                "elapsed_min": 12.5,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def _legacy_adapter(manifest: Path, calls: list[str]):
    def runner(_path: Path, request: RecognitionRequest) -> dict[str, object]:
        calls.append(request.invocation_id)
        return {
            "language": "zh",
            "segments": [
                {"words": [{"word": "舊版", "start": 0.0, "end": 0.001}]}
            ],
        }

    return build_legacy_v1_whisperx_adapter(
        manifest,
        runner=runner,
        runner_runtime_components={"fixture-whisperx": "1"},
        runner_code_hash="f" * 64,
        runner_execution_mode="fixture",
    )


@pytest.mark.parametrize("count", [3, 20])
def test_run_exact_clip_order_and_complete_replay_without_recognition(
    tmp_path: Path, count: int
) -> None:
    packet, clips = _formal_inputs(tmp_path, count)
    output = tmp_path / "output"
    first = FakeRecognizer()
    manifest = run_recognition_pilot(
        annotation_packet_path=packet,
        clips_dir=clips,
        output_dir=output,
        system_id="qwen-primary",
        adapter=first,
    )
    assert len(first.recognize_calls) == count
    assert tuple(item.clip.clip_id for item in manifest.clips) == tuple(
        f"clip-{index + 1:03d}" for index in range(count)
    )
    replay = FakeRecognizer()
    assert run_recognition_pilot(
        annotation_packet_path=packet,
        clips_dir=clips,
        output_dir=output,
        system_id="qwen-primary",
        adapter=replay,
    ) == manifest
    assert replay.recognize_calls == []
    assert len(replay.verify_calls) == count


def test_interrupted_prefix_resumes_only_missing_and_fresh_verify_uses_verify(
    tmp_path: Path,
) -> None:
    packet, clips = _formal_inputs(tmp_path, 4)
    output = tmp_path / "output"
    interrupted = FakeRecognizer(interrupt_after=2)
    with pytest.raises(RuntimeError, match="simulated"):
        run_recognition_pilot(
            annotation_packet_path=packet,
            clips_dir=clips,
            output_dir=output,
            system_id="faster-corroboration",
            adapter=interrupted,
        )
    resumed = FakeRecognizer()
    manifest = run_recognition_pilot(
        annotation_packet_path=packet,
        clips_dir=clips,
        output_dir=output,
        system_id="faster-corroboration",
        adapter=resumed,
    )
    assert len(resumed.recognize_calls) == 2
    fresh = FakeRecognizer()
    assert verify_recognition_pilot(
        annotation_packet_path=packet,
        clips_dir=clips,
        output_dir=output,
        system_id="faster-corroboration",
        adapter=fresh,
    ) == manifest
    assert fresh.recognize_calls == []
    assert len(fresh.verify_calls) == 4


def test_tampered_clip_and_extra_artifact_fail_closed(tmp_path: Path) -> None:
    packet, clips = _formal_inputs(tmp_path)
    output = tmp_path / "output"
    run_recognition_pilot(
        annotation_packet_path=packet,
        clips_dir=clips,
        output_dir=output,
        system_id="qwen-primary",
        adapter=FakeRecognizer(),
    )
    (clips / "clip-001.wav").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="clip binding"):
        verify_recognition_pilot(
            annotation_packet_path=packet,
            clips_dir=clips,
            output_dir=output,
            system_id="qwen-primary",
            adapter=FakeRecognizer(),
        )

    (clips / "clip-001.wav").write_bytes(b"clip-0")
    (output / "raw" / "extra.bin").write_bytes(b"extra")
    with pytest.raises(ValueError, match="file set mismatch"):
        verify_recognition_pilot(
            annotation_packet_path=packet,
            clips_dir=clips,
            output_dir=output,
            system_id="qwen-primary",
            adapter=FakeRecognizer(),
        )


def test_noncanonical_manifest_fails_and_cli_help_loads_no_provider(tmp_path: Path) -> None:
    packet, clips = _formal_inputs(tmp_path)
    output = tmp_path / "output"
    run_recognition_pilot(
        annotation_packet_path=packet,
        clips_dir=clips,
        output_dir=output,
        system_id="qwen-primary",
        adapter=FakeRecognizer(),
    )
    manifest_path = output / "recognition-pilot-manifest.v1.json"
    manifest_path.write_bytes(manifest_path.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="canonical JSON"):
        verify_recognition_pilot(
            annotation_packet_path=packet,
            clips_dir=clips,
            output_dir=output,
            system_id="qwen-primary",
            adapter=FakeRecognizer(),
        )

    result = subprocess.run(
        [sys.executable, "scripts/podcast_subtitle_recognition_pilot.py", "--help"],
        cwd=Path(__file__).parents[4],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "{run,verify}" in result.stdout


def test_provider_model_is_bound_to_model_and_revision(tmp_path: Path) -> None:
    packet, clips = _formal_inputs(tmp_path, 1)
    manifest = run_recognition_pilot(
        annotation_packet_path=packet,
        clips_dir=clips,
        output_dir=tmp_path / "output",
        system_id="qwen-primary",
        adapter=FakeRecognizer(),
    )
    evidence_path = tmp_path / "output" / manifest.clips[0].evidence.relative_path
    evidence = RecognitionEvidence.model_validate_json(evidence_path.read_bytes(), strict=True)
    assert evidence.model == "fake-model@" + "a" * 40


def test_qwen_chinese_language_label_is_accepted_for_zh_tw_pilot(tmp_path: Path) -> None:
    packet, clips = _formal_inputs(tmp_path, 1)
    manifest = run_recognition_pilot(
        annotation_packet_path=packet,
        clips_dir=clips,
        output_dir=tmp_path / "output",
        system_id="qwen-primary",
        adapter=FakeRecognizer(language="Chinese"),
    )

    assert manifest.status == "complete"


@pytest.mark.parametrize(
    "artifact",
    ["request", "evidence", "provider_raw", "candidate", "manifest"],
)
def test_every_manifest_artifact_tamper_fails_closed(
    tmp_path: Path, artifact: str
) -> None:
    packet, clips = _formal_inputs(tmp_path, 1)
    output = tmp_path / "output"
    manifest = run_recognition_pilot(
        annotation_packet_path=packet,
        clips_dir=clips,
        output_dir=output,
        system_id="qwen-primary",
        adapter=FakeRecognizer(),
    )
    binding = {
        "request": manifest.clips[0].request,
        "evidence": manifest.clips[0].evidence,
        "provider_raw": manifest.clips[0].provider_raw,
        "candidate": manifest.candidate,
        "manifest": None,
    }[artifact]
    path = (
        output / "recognition-pilot-manifest.v1.json"
        if binding is None
        else output / binding.relative_path
    )
    path.write_bytes(path.read_bytes() + b"x")
    with pytest.raises(ValueError):
        verify_recognition_pilot(
            annotation_packet_path=packet,
            clips_dir=clips,
            output_dir=output,
            system_id="qwen-primary",
            adapter=FakeRecognizer(),
        )


def test_evidence_hole_fails_before_first_recognize(tmp_path: Path) -> None:
    packet, clips = _formal_inputs(tmp_path, 3)
    output = tmp_path / "output"
    manifest = run_recognition_pilot(
        annotation_packet_path=packet,
        clips_dir=clips,
        output_dir=output,
        system_id="qwen-primary",
        adapter=FakeRecognizer(),
    )
    stash = tmp_path / "recoverable-stash"
    stash.mkdir()
    for path in (
        output / "recognition-pilot-manifest.v1.json",
        output / manifest.candidate.relative_path,
        output / manifest.clips[0].evidence.relative_path,
        output / manifest.clips[0].provider_raw.relative_path,
    ):
        os.replace(path, stash / path.name)
    resumed = FakeRecognizer()
    with pytest.raises(ValueError, match="Evidence state is not a valid prefix"):
        run_recognition_pilot(
            annotation_packet_path=packet,
            clips_dir=clips,
            output_dir=output,
            system_id="qwen-primary",
            adapter=resumed,
        )
    assert resumed.recognize_calls == []


@pytest.mark.parametrize("category", ["requests", "evidence", "raw"])
def test_extra_category_artifact_fails_before_first_recognize(
    tmp_path: Path, category: str
) -> None:
    packet, clips, output = _interrupted_prefix(tmp_path)
    extra = output / category / "unbound-extra.bin"
    extra.parent.mkdir(parents=True, exist_ok=True)
    extra.write_bytes(b"unbound")
    resumed = FakeRecognizer()
    with pytest.raises(ValueError, match="extra .* artifacts"):
        run_recognition_pilot(
            annotation_packet_path=packet,
            clips_dir=clips,
            output_dir=output,
            system_id="qwen-primary",
            adapter=resumed,
        )
    assert resumed.recognize_calls == []


def test_nonempty_quarantine_fails_before_first_recognize(tmp_path: Path) -> None:
    packet, clips, output = _interrupted_prefix(tmp_path)
    residue = output / "quarantine" / "crashed-invocation" / "raw.json"
    residue.parent.mkdir(parents=True, exist_ok=True)
    residue.write_bytes(b"forensic residue")
    resumed = FakeRecognizer()
    with pytest.raises(ValueError, match="quarantine residue"):
        run_recognition_pilot(
            annotation_packet_path=packet,
            clips_dir=clips,
            output_dir=output,
            system_id="qwen-primary",
            adapter=resumed,
        )
    assert resumed.recognize_calls == []


@pytest.mark.parametrize("category", ["requests", "evidence", "raw"])
def test_existing_prefix_binding_tamper_fails_before_first_recognize(
    tmp_path: Path, category: str
) -> None:
    packet, clips, output = _interrupted_prefix(tmp_path)
    target = next((output / category).iterdir())
    target.write_bytes(target.read_bytes() + b"tamper")
    resumed = FakeRecognizer()
    with pytest.raises(ValueError):
        run_recognition_pilot(
            annotation_packet_path=packet,
            clips_dir=clips,
            output_dir=output,
            system_id="qwen-primary",
            adapter=resumed,
        )
    assert resumed.recognize_calls == []


def test_cli_legacy_requires_manifest_but_nonlegacy_does_not(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    packet, clips = _formal_inputs(tmp_path, 1)
    common = [
        "run",
        "--packet",
        str(packet),
        "--clips",
        str(clips),
        "--output",
        str(tmp_path / "output"),
        "--system",
    ]
    with pytest.raises(SystemExit) as failed:
        pilot_cli_main([*common, "legacy-v1-whisperx"])
    assert failed.value.code == 2
    assert "legacy-v1-whisperx requires --legacy-manifest" in capsys.readouterr().err
    parsed = build_parser().parse_args([*common, "qwen-primary"])
    assert parsed.legacy_manifest is None


def test_legacy_identity_binds_exact_manifest_prompt_and_honest_system_id(
    tmp_path: Path,
) -> None:
    manifest_path = _legacy_manifest(tmp_path, ("主持人", "來賓", "主持人"))
    manifest, _raw, manifest_hash = load_legacy_v1_manifest(manifest_path)
    adapter = _legacy_adapter(manifest_path, [])
    components = dict(adapter.identity.runtime_components)
    assert legacy_v1_initial_prompt(manifest) == "主持人、來賓"
    assert components["legacy_manifest_sha256"] == manifest_hash
    assert components["legacy_initial_prompt_sha256"] == sha256_bytes(
        "主持人、來賓".encode("utf-8")
    )
    assert adapter.identity.model_version == "edaa852ec7e145841d8ffdb056a99866b5f0a478"


def test_actual_shaped_pretty_legacy_gen_manifest_is_preserved_exactly(
    tmp_path: Path,
) -> None:
    path = _legacy_manifest(tmp_path, ("安吉", "健康"))
    before = path.read_bytes()
    manifest, raw, digest = load_legacy_v1_manifest(path)
    assert manifest.stage == "gen"
    assert manifest.model == "large-v3"
    assert raw == before
    assert digest == sha256_bytes(before)
    assert b"\n  \"completed_at\"" in raw


def test_nonlegacy_manifest_codec_remains_byte_compatible_without_null_legacy_keys(
    tmp_path: Path,
) -> None:
    packet, clips = _formal_inputs(tmp_path, 1)
    output = tmp_path / "output"
    result = run_recognition_pilot(
        annotation_packet_path=packet,
        clips_dir=clips,
        output_dir=output,
        system_id="qwen-primary",
        adapter=FakeRecognizer(),
    )
    raw = (output / "recognition-pilot-manifest.v1.json").read_bytes()
    assert b"legacy_manifest" not in raw
    assert b"legacy_initial_prompt_sha256" not in raw
    assert result.canonical_bytes() == raw


def test_legacy_run_and_fresh_verify_never_confuse_faster_or_reinfer(
    tmp_path: Path,
) -> None:
    packet, clips = _formal_inputs(tmp_path, 2)
    legacy_manifest = _legacy_manifest(tmp_path)
    calls: list[str] = []
    result = run_recognition_pilot(
        annotation_packet_path=packet,
        clips_dir=clips,
        output_dir=tmp_path / "output",
        system_id="legacy-v1-whisperx",
        legacy_manifest_path=legacy_manifest,
        adapter=_legacy_adapter(legacy_manifest, calls),
    )
    assert result.system_id == "legacy-v1-whisperx"
    assert result.system_id != "faster-corroboration"
    assert len(calls) == 2
    fresh_calls: list[str] = []
    assert verify_recognition_pilot(
        annotation_packet_path=packet,
        clips_dir=clips,
        output_dir=tmp_path / "output",
        system_id="legacy-v1-whisperx",
        legacy_manifest_path=legacy_manifest,
        adapter=_legacy_adapter(legacy_manifest, fresh_calls),
    ) == result
    assert fresh_calls == []


def test_tampered_legacy_manifest_rejects_before_verify_inference(tmp_path: Path) -> None:
    packet, clips = _formal_inputs(tmp_path, 1)
    legacy_manifest = _legacy_manifest(tmp_path)
    run_recognition_pilot(
        annotation_packet_path=packet,
        clips_dir=clips,
        output_dir=tmp_path / "output",
        system_id="legacy-v1-whisperx",
        legacy_manifest_path=legacy_manifest,
        adapter=_legacy_adapter(legacy_manifest, []),
    )
    legacy_manifest.write_bytes(
        json.dumps(
            {
                "stage": "gen",
                "model": "large-v3",
                "language": "zh",
                "hotwords": ["被竄改"],
            },
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")
    )
    calls: list[str] = []
    with pytest.raises(ValueError):
        verify_recognition_pilot(
            annotation_packet_path=packet,
            clips_dir=clips,
            output_dir=tmp_path / "output",
            system_id="legacy-v1-whisperx",
            legacy_manifest_path=legacy_manifest,
            adapter=_legacy_adapter(legacy_manifest, calls),
        )
    assert calls == []


def test_legacy_whisperx_alignment_is_slimmed_to_historical_word_contract() -> None:
    aligned = {
        "segments": [
            {
                "avg_logprob": -0.15,
                "start": 0.03,
                "end": 0.63,
                "text": "我破壞",
                "words": [
                    {
                        "word": "我",
                        "start": 0.03,
                        "end": 0.25,
                        "score": 0.91,
                    },
                    {
                        "word": "破壞",
                        "start": 0.25,
                        "end": 0.63,
                        "score": 0.99,
                    },
                ],
            }
        ]
    }

    assert _slim_aligned_segments(aligned) == [
        {
            "words": [
                {"word": "我", "start": 0.03, "end": 0.25},
                {"word": "破壞", "start": 0.25, "end": 0.63},
            ]
        }
    ]


@pytest.mark.parametrize("missing", ["word", "start", "end"])
def test_legacy_whisperx_alignment_never_silently_drops_unaligned_words(
    missing: str,
) -> None:
    word: dict[str, object] = {"word": "文字", "start": 0.1, "end": 0.4}
    word.pop(missing)

    with pytest.raises(RecognitionAdapterInputError, match="aligned word"):
        _slim_aligned_segments({"segments": [{"words": [word]}]})
