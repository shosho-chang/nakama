"""5-stage Script-Driven Video Production pipeline.

Stage 0: Extract audio/video streams from raw_recording.mp4
Stage 1: WhisperX ASR + Mistake Removal (clap marker detection)
Stage 2: DSL Parser → Manifest  (TypeScript parser via subprocess)
Stage 3: Quote Visualisation    (Slice 3 — stub)
Stage 4: Remotion B-roll Render (Slice 2 — stub)
Stage 5: FCPXML + SRT emit

CLI: python -m agents.brook.script_video --episode <id>
"""

from __future__ import annotations

import dataclasses
import json
import logging
import subprocess
from pathlib import Path

from agents.brook.script_video import fcpxml_emitter, mistake_removal, srt_emitter
from agents.brook.script_video.cuts import CutPoint
from agents.brook.script_video.manifest import Manifest

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DATA_ROOT = _REPO_ROOT / "data" / "script_video"
_VIDEO_DIR = _REPO_ROOT / "video"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class EpisodeResult:
    episode_id: str
    fcpxml_path: Path
    srt_path: Path
    cuts: list[CutPoint]


def run(episode_id: str) -> EpisodeResult:
    """Orchestrate all five pipeline stages for *episode_id*."""
    paths = _EpisodePaths(episode_id)
    paths.validate()

    # Stage 0 ─ extract audio/video streams
    _stage0_extract(paths)

    # Stage 1 ─ WhisperX ASR + mistake removal
    whisperx_words = _stage1_whisperx(paths)
    cuts = mistake_removal.detect_clap_markers(paths.aroll_audio)

    # Stage 2 ─ DSL parse → manifest.json
    manifest = _stage2_parse(paths, cuts)

    # Stage 3 ─ quote visualisation (stub, Slice 3)
    _stage3_quote_stub(manifest)

    # Stage 4 ─ Remotion B-roll render (stub, Slice 2)
    _stage4_render_stub(manifest)

    # Stage 5 ─ FCPXML + SRT emit
    fcpxml_path = paths.out_dir / "episode.fcpxml"
    srt_path = paths.out_dir / "episode.srt"
    fcpxml_emitter.emit(manifest, fcpxml_path)
    srt_emitter.emit(srt_path, whisperx_words, cuts)

    logger.info("Episode %s: pipeline complete → %s", episode_id, fcpxml_path)
    return EpisodeResult(
        episode_id=episode_id,
        fcpxml_path=fcpxml_path,
        srt_path=srt_path,
        cuts=cuts,
    )


# ---------------------------------------------------------------------------
# Stage implementations
# ---------------------------------------------------------------------------


def _stage0_extract(paths: "_EpisodePaths") -> None:
    """Extract aroll-audio.mp3 and aroll-video.mp4 from raw_recording.mp4."""
    if paths.aroll_audio.exists() and paths.aroll_video.exists():
        logger.debug("Stage 0: streams already extracted, skipping ffmpeg")
        return

    if not paths.raw_recording.exists():
        raise FileNotFoundError(f"raw_recording.mp4 not found: {paths.raw_recording}")

    # Extract audio
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(paths.raw_recording),
            "-vn",
            "-acodec",
            "libmp3lame",
            "-q:a",
            "2",
            str(paths.aroll_audio),
        ],
        check=True,
        capture_output=True,
    )
    # Extract video (no audio)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(paths.raw_recording),
            "-an",
            "-vcodec",
            "copy",
            str(paths.aroll_video),
        ],
        check=True,
        capture_output=True,
    )
    logger.info("Stage 0: extracted streams for %s", paths.episode_id)


def _stage1_whisperx(paths: "_EpisodePaths") -> list[dict]:
    """Stage 1 ASR — Slice 1 only ships marker-primary path; ASR lands in Slice 2."""
    # Slice 1 marker-primary (α) does not need WhisperX. Returning [] keeps
    # downstream pipeline running on the marker-only path; Slice 2 will wire
    # in `.claude/skills/transcribe` and gate alignment fallback (β) on the
    # word-level output.
    logger.debug("Stage 1: ASR deferred to Slice 2; marker-primary path only")
    return []


def _stage2_parse(paths: "_EpisodePaths", cuts: list[CutPoint]) -> Manifest:
    """Parse script.md into a Manifest via the TypeScript Node.js parser.

    ADR-015 §Q1 chose a process-boundary architecture between Python orchestrator
    and the Node.js video subproject; there is no in-process Python DSL fallback
    on purpose. If `video/node_modules/` is missing, fail fast with a clear
    bring-up command rather than silently degrading to a partial parser.
    """
    manifest_path = paths.episode_dir / "manifest.json"

    parser_dist = _VIDEO_DIR / "dist" / "parser" / "parse.js"
    if not parser_dist.exists():
        raise RuntimeError(
            f"video/ subproject is not built — run: cd {_VIDEO_DIR} && npm install && npm run build"
        )

    result = subprocess.run(
        [
            "node",
            str(parser_dist),
            "--script",
            str(paths.script_md),
            "--out",
            str(manifest_path),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"TypeScript parser failed:\n{result.stderr}")

    # Load and augment with cuts
    with manifest_path.open() as f:
        data = json.load(f)

    data["aroll_audio"] = str(paths.aroll_audio)
    data["aroll_video"] = str(paths.aroll_video)
    data["cuts"] = [dataclasses.asdict(c) for c in cuts]

    manifest = Manifest.model_validate(data)
    manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    return manifest


def _stage3_quote_stub(manifest: Manifest) -> None:
    """Quote visualisation — stub until Slice 3."""
    logger.debug("Stage 3: quote visualisation stub (Slice 3)")


def _stage4_render_stub(manifest: Manifest) -> None:
    """Remotion B-roll render — stub until Slice 2."""
    logger.debug("Stage 4: Remotion render stub (Slice 2)")


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class _EpisodePaths:
    episode_id: str

    @property
    def episode_dir(self) -> Path:
        return _DATA_ROOT / self.episode_id

    @property
    def raw_recording(self) -> Path:
        return self.episode_dir / "raw_recording.mp4"

    @property
    def aroll_audio(self) -> Path:
        return self.episode_dir / "aroll-audio.mp3"

    @property
    def aroll_video(self) -> Path:
        return self.episode_dir / "aroll-video.mp4"

    @property
    def script_md(self) -> Path:
        return self.episode_dir / "script.md"

    @property
    def out_dir(self) -> Path:
        return self.episode_dir / "out"

    def validate(self) -> None:
        if not self.episode_dir.exists():
            raise FileNotFoundError(
                f"Episode directory not found: {self.episode_dir}\n"
                f"Create it with: mkdir -p {self.episode_dir}/refs {self.episode_dir}/out"
            )
        if not self.script_md.exists():
            raise FileNotFoundError(f"script.md not found: {self.script_md}")
