"""Tests for ``shared.thumbnail_funnel`` (ADR-033 D8 Stages 1+2).

Subprocess is mocked end-to-end — these tests do NOT invoke ffmpeg/ffprobe.
The compute paths (Laplacian variance, top-N sort, dedupe, periodic+peak
union) use synthesised PIL fixtures so they exercise real numpy/scipy.

Project convention: sync test functions calling ``asyncio.run(...)`` (per
``tests/test_thumbnail_worker.py``) instead of ``pytest-asyncio``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from shared import thumbnail_funnel
from shared.thumbnail_funnel import (
    FrameCandidate,
    FunnelError,
    _detect_audio_peaks,
    _ffmpeg_extract,
    _laplacian_variance,
    _probe_duration,
    rank_by_sharpness,
    run,
    stratified_sample,
)

# Subprocess mocking infrastructure


class _FakeProc:
    """Stand-in for asyncio subprocess.Process."""

    def __init__(self, *, returncode: int = 0, stdout: bytes = b"", stderr: bytes = b""):
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, self._stderr


def _install_subprocess(monkeypatch, dispatch):
    """Monkeypatch ``asyncio.create_subprocess_exec`` to call ``dispatch(argv)``.

    ``dispatch`` is a function ``argv: tuple[str, ...] → _FakeProc`` that gets
    to decide what to return based on the command (ffprobe vs ffmpeg
    silencedetect vs ffmpeg extract).
    """
    calls: list[tuple[str, ...]] = []

    async def fake_exec(*argv, stdout=None, stderr=None):
        calls.append(tuple(str(a) for a in argv))
        return dispatch(tuple(str(a) for a in argv))

    monkeypatch.setattr(thumbnail_funnel.asyncio, "create_subprocess_exec", fake_exec)
    return calls


# PNG fixtures


def _write_noise_png(path: Path, *, size: int = 200) -> Path:
    """Random uniform noise — high Laplacian variance (sharp edges everywhere)."""
    rng = np.random.default_rng(seed=int(path.stem.encode()[-1]))
    arr = rng.integers(0, 256, (size, size), dtype=np.uint8)
    Image.fromarray(arr, mode="L").save(path)
    return path


def _write_solid_png(path: Path, *, size: int = 200, value: int = 128) -> Path:
    """Solid-colour image — Laplacian variance is exactly zero."""
    arr = np.full((size, size), value, dtype=np.uint8)
    Image.fromarray(arr, mode="L").save(path)
    return path


def _write_gradient_png(path: Path, *, size: int = 200) -> Path:
    """Smooth gradient — low but non-zero variance (intermediate sharpness)."""
    arr = np.tile(np.linspace(0, 255, size, dtype=np.uint8), (size, 1))
    Image.fromarray(arr, mode="L").save(path)
    return path


# _laplacian_variance


def test_laplacian_variance_solid_image_is_zero(tmp_path):
    p = _write_solid_png(tmp_path / "solid.png")
    assert _laplacian_variance(p) == pytest.approx(0.0, abs=1e-6)


def test_laplacian_variance_sharp_higher_than_gradient_higher_than_solid(tmp_path):
    sharp = _write_noise_png(tmp_path / "sharp.png")
    grad = _write_gradient_png(tmp_path / "grad.png")
    solid = _write_solid_png(tmp_path / "solid.png")
    v_sharp = _laplacian_variance(sharp)
    v_grad = _laplacian_variance(grad)
    v_solid = _laplacian_variance(solid)
    assert v_sharp > v_grad > v_solid >= 0
    assert v_sharp > v_grad * 10


# rank_by_sharpness


def _candidate_with(path: Path, ts: float = 0.0, kind: str = "periodic") -> FrameCandidate:
    return FrameCandidate(path=path, timestamp_sec=ts, sample_kind=kind)


def test_rank_by_sharpness_empty_returns_empty():
    assert rank_by_sharpness([]) == []


def test_rank_by_sharpness_orders_by_variance_desc(tmp_path):
    sharp = _write_noise_png(tmp_path / "sharp.png")
    solid = _write_solid_png(tmp_path / "solid.png")
    grad = _write_gradient_png(tmp_path / "grad.png")
    cands = [
        _candidate_with(solid, ts=10.0),
        _candidate_with(sharp, ts=20.0),
        _candidate_with(grad, ts=30.0),
    ]
    out = rank_by_sharpness(cands, top_pct=1.0)
    assert [c.path for c in out] == [sharp, grad, solid]
    assert all(c.sharpness is not None for c in out)
    assert out[0].sharpness > out[1].sharpness > out[2].sharpness


def test_rank_by_sharpness_top_pct_caps(tmp_path):
    pngs = [_write_noise_png(tmp_path / f"f{i}.png") for i in range(20)]
    cands = [_candidate_with(p, ts=float(i)) for i, p in enumerate(pngs)]
    out = rank_by_sharpness(cands, top_pct=0.25, min_count=1)
    assert len(out) == 5


def test_rank_by_sharpness_min_count_floor(tmp_path):
    pngs = [_write_noise_png(tmp_path / f"f{i}.png") for i in range(4)]
    cands = [_candidate_with(p) for p in pngs]
    out = rank_by_sharpness(cands, top_pct=0.25, min_count=3)
    assert len(out) == 3


def test_rank_by_sharpness_min_count_capped_to_input_length(tmp_path):
    pngs = [_write_noise_png(tmp_path / f"f{i}.png") for i in range(2)]
    cands = [_candidate_with(p) for p in pngs]
    out = rank_by_sharpness(cands, top_pct=0.25, min_count=10)
    assert len(out) == 2


def test_rank_by_sharpness_input_not_mutated(tmp_path):
    sharp = _write_noise_png(tmp_path / "sharp.png")
    cands = [_candidate_with(sharp)]
    out = rank_by_sharpness(cands, top_pct=1.0)
    assert cands[0].sharpness is None
    assert out[0].sharpness is not None


# _probe_duration


def test_probe_duration_parses_float(monkeypatch, tmp_path):
    def dispatch(argv):
        assert argv[0] == "ffprobe"
        return _FakeProc(stdout=b"42.5\n")

    _install_subprocess(monkeypatch, dispatch)
    assert asyncio.run(_probe_duration(tmp_path / "video.mp4")) == pytest.approx(42.5)


def test_probe_duration_raises_funnel_error_on_failure(monkeypatch, tmp_path):
    def dispatch(argv):
        return _FakeProc(returncode=1, stderr=b"No such file")

    _install_subprocess(monkeypatch, dispatch)
    with pytest.raises(FunnelError, match="ffprobe failed"):
        asyncio.run(_probe_duration(tmp_path / "video.mp4"))


def test_probe_duration_raises_on_unparseable_output(monkeypatch, tmp_path):
    def dispatch(argv):
        return _FakeProc(stdout=b"not a number")

    _install_subprocess(monkeypatch, dispatch)
    with pytest.raises(FunnelError, match="non-float"):
        asyncio.run(_probe_duration(tmp_path / "video.mp4"))


# _detect_audio_peaks


_SILENCEDETECT_STDERR = b"""[silencedetect @ 0x1] silence_start: 0.5
[silencedetect @ 0x1] silence_end: 1.0 | silence_duration: 0.5
[silencedetect @ 0x1] silence_start: 3.5
[silencedetect @ 0x1] silence_end: 4.0 | silence_duration: 0.5
[silencedetect @ 0x1] silence_start: 9.0
[silencedetect @ 0x1] silence_end: 9.5 | silence_duration: 0.5
"""


def test_detect_audio_peaks_parses_speech_midpoints(monkeypatch, tmp_path):
    def dispatch(argv):
        return _FakeProc(stderr=_SILENCEDETECT_STDERR)

    _install_subprocess(monkeypatch, dispatch)
    peaks = asyncio.run(_detect_audio_peaks(tmp_path / "video.mp4", min_speech_sec=1.5))
    # Speech segments: (0, 0.5)=0.5s SKIP, (1, 3.5)=2.5s KEEP midpoint=2.25,
    # (4, 9)=5s KEEP midpoint=6.5
    assert peaks == pytest.approx([2.25, 6.5])


def test_detect_audio_peaks_empty_when_no_long_speech(monkeypatch, tmp_path):
    def dispatch(argv):
        return _FakeProc(stderr=_SILENCEDETECT_STDERR)

    _install_subprocess(monkeypatch, dispatch)
    peaks = asyncio.run(_detect_audio_peaks(tmp_path / "video.mp4", min_speech_sec=10.0))
    assert peaks == []


def test_detect_audio_peaks_raises_on_ffmpeg_failure(monkeypatch, tmp_path):
    def dispatch(argv):
        return _FakeProc(returncode=1, stderr=b"codec error")

    _install_subprocess(monkeypatch, dispatch)
    with pytest.raises(FunnelError, match="silencedetect failed"):
        asyncio.run(_detect_audio_peaks(tmp_path / "video.mp4"))


# _ffmpeg_extract


def test_ffmpeg_extract_argv_uses_fast_seek(monkeypatch, tmp_path):
    captured_argv: list[tuple[str, ...]] = []

    def dispatch(argv):
        captured_argv.append(argv)
        return _FakeProc()

    _install_subprocess(monkeypatch, dispatch)
    asyncio.run(_ffmpeg_extract(tmp_path / "in.mp4", 12.345, tmp_path / "out.png"))
    argv = captured_argv[0]
    ss_idx = argv.index("-ss")
    i_idx = argv.index("-i")
    assert ss_idx < i_idx
    assert argv[ss_idx + 1] == "12.345"
    assert "-frames:v" in argv


def test_ffmpeg_extract_raises_on_failure(monkeypatch, tmp_path):
    def dispatch(argv):
        return _FakeProc(returncode=1, stderr=b"seek out of range")

    _install_subprocess(monkeypatch, dispatch)
    with pytest.raises(FunnelError, match="ffmpeg extract failed at t="):
        asyncio.run(_ffmpeg_extract(tmp_path / "in.mp4", 99.0, tmp_path / "out.png"))


# stratified_sample


def _make_dispatch_for_full_funnel(
    *,
    duration: float = 60.0,
    silence_stderr: bytes = b"",
    extracted: dict[Path, None] | None = None,
):
    """Dispatch closure handling ffprobe + silencedetect + per-frame extracts."""
    if extracted is None:
        extracted = {}

    def dispatch(argv):
        if argv[0] == "ffprobe":
            return _FakeProc(stdout=f"{duration}\n".encode())
        if argv[0] == "ffmpeg" and "silencedetect" in " ".join(argv):
            return _FakeProc(stderr=silence_stderr)
        if argv[0] == "ffmpeg" and "-frames:v" in argv:
            out_path = Path(argv[-1])
            _write_noise_png(out_path, size=50)
            extracted[out_path] = None
            return _FakeProc()
        raise AssertionError(f"unexpected argv: {argv}")

    return dispatch


def test_stratified_sample_periodic_only(monkeypatch, tmp_path):
    extracted: dict[Path, None] = {}
    dispatch = _make_dispatch_for_full_funnel(duration=60.0, extracted=extracted)
    _install_subprocess(monkeypatch, dispatch)

    candidates = asyncio.run(
        stratified_sample(
            tmp_path / "video.mp4",
            tmp_path / "frames",
            periodic_interval=10.0,
            audio_burst=False,
        )
    )
    # 60s / 10s = 6 periodic timestamps at t=10, 20, 30, 40, 50, 60
    assert len(candidates) == 6
    assert all(c.sample_kind == "periodic" for c in candidates)
    assert [c.timestamp_sec for c in candidates] == [10.0, 20.0, 30.0, 40.0, 50.0, 60.0]
    assert all(c.path.exists() for c in candidates)


def test_stratified_sample_includes_audio_peaks(monkeypatch, tmp_path):
    dispatch = _make_dispatch_for_full_funnel(
        duration=60.0,
        silence_stderr=_SILENCEDETECT_STDERR,
    )
    _install_subprocess(monkeypatch, dispatch)
    candidates = asyncio.run(
        stratified_sample(
            tmp_path / "video.mp4",
            tmp_path / "frames",
            periodic_interval=10.0,
            audio_burst=True,
        )
    )
    kinds = [c.sample_kind for c in candidates]
    assert kinds.count("audio_peak") == 6  # 2 peaks × 3 frames
    assert kinds.count("periodic") == 6


def test_stratified_sample_caps_max_frames(monkeypatch, tmp_path):
    dispatch = _make_dispatch_for_full_funnel(duration=600.0)
    _install_subprocess(monkeypatch, dispatch)
    candidates = asyncio.run(
        stratified_sample(
            tmp_path / "video.mp4",
            tmp_path / "frames",
            periodic_interval=10.0,
            audio_burst=False,
            max_frames=20,
            seed=42,
        )
    )
    assert len(candidates) == 20
    candidates2 = asyncio.run(
        stratified_sample(
            tmp_path / "video.mp4",
            tmp_path / "frames2",
            periodic_interval=10.0,
            audio_burst=False,
            max_frames=20,
            seed=42,
        )
    )
    assert [c.timestamp_sec for c in candidates] == [c.timestamp_sec for c in candidates2]


def test_stratified_sample_short_video_one_shot(monkeypatch, tmp_path):
    dispatch = _make_dispatch_for_full_funnel(duration=3.0)
    _install_subprocess(monkeypatch, dispatch)
    candidates = asyncio.run(
        stratified_sample(
            tmp_path / "short.mp4",
            tmp_path / "frames",
            periodic_interval=10.0,
            audio_burst=False,
        )
    )
    assert len(candidates) == 1
    assert candidates[0].timestamp_sec == pytest.approx(1.5)


def test_stratified_sample_dedupes_close_timestamps(monkeypatch, tmp_path):
    silence_stderr = (
        b"[silencedetect @ 0x1] silence_start: 9.5\n"
        b"[silencedetect @ 0x1] silence_end: 11.5 | silence_duration: 2.0\n"
    )
    dispatch = _make_dispatch_for_full_funnel(
        duration=60.0,
        silence_stderr=silence_stderr,
    )
    _install_subprocess(monkeypatch, dispatch)
    candidates = asyncio.run(
        stratified_sample(
            tmp_path / "video.mp4",
            tmp_path / "frames",
            periodic_interval=10.0,
            audio_burst=True,
        )
    )
    paths = [c.path for c in candidates]
    assert len(paths) == len(set(paths))


# run


def test_run_conversation_mode(monkeypatch, tmp_path):
    dispatch = _make_dispatch_for_full_funnel(
        duration=60.0,
        silence_stderr=_SILENCEDETECT_STDERR,
    )
    _install_subprocess(monkeypatch, dispatch)
    out = asyncio.run(
        run(
            tmp_path / "video.mp4",
            tmp_path / "frames",
            mode="conversation",
            top_pct=0.5,
        )
    )
    assert all(c.sharpness is not None for c in out)
    sharpnesses = [c.sharpness for c in out]
    assert sharpnesses == sorted(sharpnesses, reverse=True)


def test_run_expression_sample_mode_uses_dense_periodic(monkeypatch, tmp_path):
    """expression_sample mode samples 1/sec and disables audio peaks."""

    def dispatch(argv):
        if argv[0] == "ffprobe":
            return _FakeProc(stdout=b"30.0\n")
        if argv[0] == "ffmpeg" and "silencedetect" in " ".join(argv):
            pytest.fail("expression_sample mode must NOT call silencedetect")
        if argv[0] == "ffmpeg":
            out_path = Path(argv[-1])
            _write_noise_png(out_path, size=50)
            return _FakeProc()
        raise AssertionError(f"unexpected: {argv}")

    _install_subprocess(monkeypatch, dispatch)
    out = asyncio.run(
        run(
            tmp_path / "expr.mp4",
            tmp_path / "frames",
            mode="expression_sample",
            top_pct=1.0,
        )
    )
    assert len(out) == 30
    assert all(c.sample_kind == "periodic" for c in out)


# ── window parameter threading (ADR-054 S3) ──────────────────────────────────


def test_window_shorter_than_periodic_interval_one_shot(monkeypatch, tmp_path):
    """Window narrower than periodic_interval → single frame at window midpoint."""
    dispatch = _make_dispatch_for_full_funnel(duration=60.0)
    _install_subprocess(monkeypatch, dispatch)
    candidates = asyncio.run(
        stratified_sample(
            tmp_path / "video.mp4",
            tmp_path / "frames",
            periodic_interval=10.0,
            audio_burst=False,
            window=(20.0, 25.0),  # span=5s < 10s interval
        )
    )
    assert len(candidates) == 1
    assert candidates[0].timestamp_sec == pytest.approx(22.5)


def test_window_audio_peaks_outside_window_excluded(monkeypatch, tmp_path):
    """Audio peaks outside the window must not be included in candidates."""
    # _SILENCEDETECT_STDERR produces peaks at ~2.25s and ~6.5s (from speech midpoints)
    dispatch = _make_dispatch_for_full_funnel(
        duration=60.0,
        silence_stderr=_SILENCEDETECT_STDERR,
    )
    _install_subprocess(monkeypatch, dispatch)
    # Window (5.0, 60.0) — peak at 2.25s is outside, peak at 6.5s is inside
    candidates = asyncio.run(
        stratified_sample(
            tmp_path / "video.mp4",
            tmp_path / "frames",
            periodic_interval=10.0,
            audio_burst=True,
            window=(5.0, 60.0),
        )
    )
    audio_peak_times = [c.timestamp_sec for c in candidates if c.sample_kind == "audio_peak"]
    # All audio peak times must be within the window
    assert all(5.0 <= t <= 60.0 for t in audio_peak_times), (
        f"some audio peaks outside window: {audio_peak_times}"
    )


def test_window_periodic_timestamps_start_from_window_t_start(monkeypatch, tmp_path):
    """Periodic timestamps must start from window start, not from t=0."""
    dispatch = _make_dispatch_for_full_funnel(duration=60.0)
    _install_subprocess(monkeypatch, dispatch)
    candidates = asyncio.run(
        stratified_sample(
            tmp_path / "video.mp4",
            tmp_path / "frames",
            periodic_interval=10.0,
            audio_burst=False,
            window=(20.0, 50.0),  # span=30s → 3 periodic timestamps
        )
    )
    periodic_times = sorted(c.timestamp_sec for c in candidates if c.sample_kind == "periodic")
    # Expected: 20+10=30, 20+20=40, 20+30=50
    assert periodic_times == pytest.approx([30.0, 40.0, 50.0])


def test_run_passes_window_to_stratified_sample(monkeypatch, tmp_path):
    """run() with window= must honour window in the output timestamps."""
    dispatch = _make_dispatch_for_full_funnel(duration=60.0)
    _install_subprocess(monkeypatch, dispatch)
    out = asyncio.run(
        run(
            tmp_path / "video.mp4",
            tmp_path / "frames",
            mode="conversation",
            top_pct=1.0,
            window=(30.0, 60.0),
        )
    )
    # All timestamps must fall within the window
    assert all(30.0 <= c.timestamp_sec <= 60.0 for c in out), (
        f"timestamps outside window: {[c.timestamp_sec for c in out]}"
    )
