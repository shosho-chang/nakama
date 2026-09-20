from __future__ import annotations

import io
import math
import struct
import wave
from dataclasses import replace
from pathlib import Path

import pytest

from agents.brook.podcast_subtitles import transcript_pilot as transcript_pilot_module
from agents.brook.podcast_subtitles.hashing import hash_file
from agents.brook.podcast_subtitles.transcript_gold import load_annotation_packet
from agents.brook.podcast_subtitles.transcript_pilot import (
    BOUNDARY_RMS_MAX_PPM_V2,
    INTERIOR_ACTIVE_RATIO_MIN_PPM_V2,
    SELECTION_ALGORITHM_ID,
    SELECTION_POLICY_V2,
    TEMPORAL_STRATA_COUNT,
    TranscriptPilotRequest,
    build_transcript_pilot,
)


def _write_wav(
    path: Path,
    *,
    fill: int = 0,
    duration_ms: int = 4_200,
    sample_rate_hz: int = 1_000,
    tail_frames: int = 0,
) -> None:
    frame_count = duration_ms * sample_rate_hz // 1000 + tail_frames
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(sample_rate_hz)
        writer.writeframes(bytes((fill, 0)) * frame_count)


def _request(tmp_path: Path, *, wav: Path | None = None) -> TranscriptPilotRequest:
    normalized = wav or tmp_path / "normalized.wav"
    if not normalized.exists():
        _write_wav(normalized)
    return TranscriptPilotRequest(
        workspace_root=tmp_path / "pilot",
        normalized_wav_path=normalized,
        episode_id="episode-pilot",
        packet_id="episode-pilot-packet",
        planned_candidate_roots=(
            tmp_path / "v1-DO-NOT-LEAK-marker",
            tmp_path / "v2-DO-NOT-LEAK-marker",
        ),
        margin_duration_ms=100,
        clip_duration_ms=100,
        selection_seed=b"s" * 32,
        selection_nonce=b"n" * 32,
    )


def _write_silence_aligned_wav(
    path: Path,
    *,
    silent: bool = False,
    active_amplitude: int = 2_000,
) -> None:
    samples = bytearray()
    for frame in range(42_000):
        phase = frame % 1_000
        amplitude = 0 if silent or not 250 <= phase < 750 else active_amplitude
        samples.extend(struct.pack("<h", amplitude))
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(1_000)
        writer.writeframes(samples)


def _v2_request(tmp_path: Path, *, silent: bool = False) -> TranscriptPilotRequest:
    normalized = tmp_path / "normalized-v2.wav"
    normalized.parent.mkdir(parents=True, exist_ok=True)
    _write_silence_aligned_wav(normalized, silent=silent)
    return TranscriptPilotRequest(
        workspace_root=tmp_path / "pilot-v2",
        normalized_wav_path=normalized,
        episode_id="episode-pilot-v2",
        packet_id="episode-pilot-v2-packet",
        planned_candidate_roots=(tmp_path / "v1-candidate", tmp_path / "v2-candidate"),
        selection_policy=SELECTION_POLICY_V2,
        margin_duration_ms=500,
        clip_duration_ms=1_000,
        selection_seed=b"v" * 32,
        selection_nonce=b"2" * 32,
    )


def _fresh_clip(source: Path, *, start_frame: int, end_frame: int) -> bytes:
    with wave.open(str(source), "rb") as reader:
        reader.setpos(start_frame)
        pcm = reader.readframes(end_frame - start_frame)
        channels = reader.getnchannels()
        sample_width = reader.getsampwidth()
        sample_rate = reader.getframerate()
    output = io.BytesIO()
    with wave.open(output, "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(sample_width)
        writer.setframerate(sample_rate)
        writer.setcomptype("NONE", "not compressed")
        writer.writeframes(pcm)
    return output.getvalue()


def test_silence_aligned_v2_selects_twenty_quiet_boundaries_with_active_interior(
    tmp_path: Path,
) -> None:
    result = build_transcript_pilot(_v2_request(tmp_path))
    declaration = result.declaration
    assert declaration.schema_version == 2
    assert result.declaration_path.name == "sampling-declaration.v2.json"
    assert result.summary_path.name == "summary.v2.json"
    assert len(declaration.eligible_strata) == 20
    assert all(item.eligible_position_count > 0 for item in declaration.eligible_strata)
    assert len(declaration.selected_intervals) == 20
    for selected, clip_path in zip(
        declaration.selected_intervals,
        result.clip_paths,
        strict=True,
    ):
        with wave.open(str(clip_path), "rb") as reader:
            leading = struct.unpack("<250h", reader.readframes(250))
            interior_cells = tuple(
                struct.unpack("<10h", reader.readframes(10)) for _ in range(50)
            )
            trailing = struct.unpack("<250h", reader.readframes(250))
        leading_sum = sum(value * value for value in leading)
        trailing_sum = sum(value * value for value in trailing)
        denominator = 250 * 32768**2
        leading_ppm = math.isqrt(leading_sum * 1_000_000**2 // denominator)
        trailing_ppm = math.isqrt(trailing_sum * 1_000_000**2 // denominator)
        active_cells = sum(
            sum(value * value for value in cell) * 1_000_000**2
            >= len(cell) * 32768**2 * 10_000**2
            for cell in interior_cells
        )
        active_ratio_ppm = active_cells * 1_000_000 // len(interior_cells)
        assert leading_ppm == selected.leading_boundary_rms_ppm_floor
        assert trailing_ppm == selected.trailing_boundary_rms_ppm_floor
        assert leading_ppm <= BOUNDARY_RMS_MAX_PPM_V2
        assert trailing_ppm <= BOUNDARY_RMS_MAX_PPM_V2
        assert active_ratio_ppm == selected.interior_active_ratio_ppm_floor
        assert (
            active_ratio_ppm >= INTERIOR_ACTIVE_RATIO_MIN_PPM_V2
        )
    replay = build_transcript_pilot(_v2_request(tmp_path))
    assert replay.declaration == declaration
    assert replay.annotation_packet == result.annotation_packet


def test_silence_aligned_v2_fails_when_a_stratum_has_no_active_interior(
    tmp_path: Path,
) -> None:
    request = _v2_request(tmp_path, silent=True)
    with pytest.raises(ValueError, match="no eligible start in temporal stratum"):
        build_transcript_pilot(request)
    assert not request.workspace_root.exists()


def test_silence_aligned_v2_rejects_secret_conflict_and_declaration_tamper(
    tmp_path: Path,
) -> None:
    request = _v2_request(tmp_path)
    result = build_transcript_pilot(request)
    with pytest.raises(ValueError, match="selection seed conflicts"):
        build_transcript_pilot(replace(request, selection_seed=b"x" * 32))
    result.declaration_path.write_bytes(result.declaration_path.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="SamplingDeclarationV2"):
        build_transcript_pilot(request)


def test_silence_aligned_v2_rejects_input_clip_partial_and_candidate_residue(
    tmp_path: Path,
) -> None:
    input_request = _v2_request(tmp_path / "input")
    input_result = build_transcript_pilot(input_request)
    _write_silence_aligned_wav(
        input_request.normalized_wav_path,
        active_amplitude=2_500,
    )
    with pytest.raises(ValueError, match="conflicts"):
        build_transcript_pilot(input_request)

    clip_request = _v2_request(tmp_path / "clip")
    clip_result = build_transcript_pilot(clip_request)
    changed = bytearray(clip_result.clip_paths[0].read_bytes())
    changed[-1] ^= 1
    clip_result.clip_paths[0].write_bytes(changed)
    with pytest.raises(ValueError, match="fresh exact WAV re-extraction"):
        build_transcript_pilot(clip_request)

    partial_request = _v2_request(tmp_path / "partial")
    partial_request.workspace_root.mkdir(parents=True)
    (partial_request.workspace_root / "partial.tmp").write_bytes(b"partial")
    with pytest.raises(ValueError, match="partial|unexpected residue"):
        build_transcript_pilot(partial_request)

    candidate_request = _v2_request(tmp_path / "candidate")
    candidate_request.planned_candidate_roots[0].parent.mkdir(parents=True, exist_ok=True)
    candidate_request.planned_candidate_roots[0].write_bytes(b"must-not-read")
    with pytest.raises(ValueError, match="must not exist"):
        build_transcript_pilot(candidate_request)

    assert input_result.declaration.schema_version == 2


def test_silence_aligned_v2_streams_wav_without_whole_file_read_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _v2_request(tmp_path)
    original_read_bytes = Path.read_bytes

    def guarded_read_bytes(path: Path) -> bytes:
        if path == request.normalized_wav_path:
            raise AssertionError("V2 normalized WAV must not use Path.read_bytes")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", guarded_read_bytes)
    result = build_transcript_pilot(request)
    assert result.summary.clip_count == 20


def test_builds_twenty_candidate_free_strata_and_exact_clips(tmp_path: Path) -> None:
    request = _request(tmp_path)
    result = build_transcript_pilot(request)

    declaration = result.declaration
    assert declaration.selection_algorithm_id == SELECTION_ALGORITHM_ID
    assert declaration.temporal_strata_count == TEMPORAL_STRATA_COUNT
    assert len(declaration.eligible_positions) == 20
    assert len(declaration.selected_intervals) == 20
    assert len(result.clip_paths) == 20
    assert result.annotation_packet.sampling_declaration_hash == declaration.declaration_hash
    assert tuple(item.stratum_index for item in declaration.selected_intervals) == tuple(range(20))
    assert all(
        left.end_frame <= right.start_frame
        for left, right in zip(
            declaration.selected_intervals,
            declaration.selected_intervals[1:],
        )
    )
    for interval, clip_path, binding in zip(
        declaration.selected_intervals,
        result.clip_paths,
        result.annotation_packet.clips,
        strict=True,
    ):
        expected = _fresh_clip(
            request.normalized_wav_path,
            start_frame=interval.start_frame,
            end_frame=interval.end_frame,
        )
        assert clip_path.read_bytes() == expected
        assert hash_file(clip_path) == binding.clip_audio_hash
        assert interval.start_frame * 1000 == interval.start_ms * declaration.sample_rate_hz
        assert interval.end_frame * 1000 == interval.end_ms * declaration.sample_rate_hz
    assert load_annotation_packet(result.annotation_packet_path) == result.annotation_packet
    assert all(not path.exists() for path in request.planned_candidate_roots)

    forbidden_literals = (
        b"v1-DO-NOT-LEAK-marker",
        b"v2-DO-NOT-LEAK-marker",
        b"known-error-window",
        b"reference-literal",
        b"target-answer",
        b"qc-result",
    )
    for path in (
        result.declaration_path,
        result.annotation_packet_path,
        result.summary_path,
    ):
        payload = path.read_bytes()
        assert all(marker not in payload for marker in forbidden_literals)


def test_same_workspace_and_secret_replays_exact_bytes_without_wav_read_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(tmp_path)
    original_read_bytes = Path.read_bytes

    def guarded_read_bytes(path: Path) -> bytes:
        if path == request.normalized_wav_path:
            raise AssertionError("normalized WAV must never be loaded into memory at once")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", guarded_read_bytes)
    first = build_transcript_pilot(request)
    before = {
        path.relative_to(request.workspace_root).as_posix(): hash_file(path)
        for path in request.workspace_root.rglob("*")
        if path.is_file()
    }
    replay = build_transcript_pilot(request)
    after = {
        path.relative_to(request.workspace_root).as_posix(): hash_file(path)
        for path in request.workspace_root.rglob("*")
        if path.is_file()
    }
    assert replay.declaration == first.declaration
    assert replay.annotation_packet == first.annotation_packet
    assert before == after


def test_builder_generates_private_256_bit_replay_secret_when_caller_omits_it(
    tmp_path: Path,
) -> None:
    request = replace(_request(tmp_path), selection_seed=None, selection_nonce=None)
    first = build_transcript_pilot(request)
    replay = build_transcript_pilot(request)
    assert len(bytes.fromhex(first.secret.selection_seed_hex)) == 32
    assert len(bytes.fromhex(first.secret.selection_nonce_hex)) == 32
    assert replay.secret == first.secret
    assert replay.declaration == first.declaration


@pytest.mark.parametrize("conflict", ("seed", "input"))
def test_replay_rejects_different_secret_or_input(tmp_path: Path, conflict: str) -> None:
    request = _request(tmp_path)
    build_transcript_pilot(request)
    if conflict == "seed":
        changed = replace(request, selection_seed=b"x" * 32)
    else:
        other = tmp_path / "other-normalized.wav"
        _write_wav(other, fill=1)
        changed = replace(request, normalized_wav_path=other)
    with pytest.raises(ValueError, match="conflict"):
        build_transcript_pilot(changed)


@pytest.mark.parametrize("artifact", ("declaration", "clip", "wav"))
def test_tampering_declaration_clip_or_wav_fails_closed(
    tmp_path: Path,
    artifact: str,
) -> None:
    request = _request(tmp_path)
    result = build_transcript_pilot(request)
    if artifact == "declaration":
        result.declaration_path.write_bytes(result.declaration_path.read_bytes() + b"\n")
    elif artifact == "clip":
        payload = bytearray(result.clip_paths[0].read_bytes())
        payload[-1] ^= 1
        result.clip_paths[0].write_bytes(payload)
    else:
        _write_wav(request.normalized_wav_path, fill=2)
    with pytest.raises(ValueError):
        build_transcript_pilot(request)


def test_partial_workspace_residue_is_rejected_not_repaired(tmp_path: Path) -> None:
    request = _request(tmp_path)
    request.workspace_root.mkdir()
    (request.workspace_root / "partial.tmp").write_text("partial", encoding="utf-8")
    with pytest.raises(ValueError, match="partial|unexpected residue"):
        build_transcript_pilot(request)
    assert (request.workspace_root / "partial.tmp").read_text(encoding="utf-8") == "partial"


def test_prepublication_failure_preserves_quarantine_and_blocks_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(tmp_path)
    with monkeypatch.context() as scoped:
        scoped.setattr(
            transcript_pilot_module.os,
            "replace",
            lambda *_args: (_ for _ in ()).throw(OSError("injected publish failure")),
        )
        with pytest.raises(ValueError, match="quarantine residue preserved"):
            build_transcript_pilot(request)
    quarantines = tuple(
        path
        for path in tmp_path.iterdir()
        if path.name.startswith(".pilot.transcript-pilot-quarantine-")
    )
    assert len(quarantines) == 1
    assert (quarantines[0] / "sampling-declaration.v1.json").is_file()
    with pytest.raises(ValueError, match="quarantine residue exists"):
        build_transcript_pilot(request)


def test_existing_candidate_root_stops_before_normalized_audio_is_opened(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(tmp_path)
    candidate = request.planned_candidate_roots[0]
    candidate.write_bytes(b"candidate-content-must-not-be-read")
    original_open = Path.open

    def guarded_open(path: Path, *args: object, **kwargs: object):
        if path == request.normalized_wav_path:
            raise AssertionError("WAV work started before candidate-free prerequisite")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    with pytest.raises(ValueError, match="must not exist"):
        build_transcript_pilot(request)


def test_caller_expected_anji_style_topology_is_enforced(tmp_path: Path) -> None:
    request = _request(tmp_path)
    matching = replace(
        request,
        expected_normalized_audio_hash=hash_file(request.normalized_wav_path),
        expected_sample_rate_hz=1_000,
        expected_channel_count=1,
        expected_sample_width_bytes=2,
        expected_frame_count=4_200,
        expected_duration_floor_ms=4_200,
    )
    build_transcript_pilot(matching)
    with pytest.raises(ValueError, match="sample_rate_hz"):
        build_transcript_pilot(replace(matching, expected_sample_rate_hz=48_000))


def test_non_integral_wav_tail_uses_frame_clock_and_replays_exact_clips(
    tmp_path: Path,
) -> None:
    normalized = tmp_path / "normalized-48k-tail.wav"
    _write_wav(normalized, sample_rate_hz=48_000, tail_frames=23)
    request = replace(
        _request(tmp_path, wav=normalized),
        expected_sample_rate_hz=48_000,
        expected_frame_count=201_623,
        expected_duration_floor_ms=4_200,
    )
    first = build_transcript_pilot(request)
    replay = build_transcript_pilot(request)
    assert first.declaration.frame_count == 201_623
    assert first.declaration.duration_floor_ms == 4_200
    assert first.declaration.frame_count % 48 == 23
    assert len(first.annotation_packet.clips) == 20
    assert all(
        interval.start_frame % 48 == interval.end_frame % 48 == 0
        for interval in first.declaration.selected_intervals
    )
    assert replay.declaration == first.declaration
