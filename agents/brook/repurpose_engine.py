"""Brook Repurpose Engine — line-agnostic orchestration for multi-channel content repurposing.

I/O Path Scheme
--------------
All artifacts land in::

    data/repurpose/<YYYY-MM-DD>-<slug>/

where ``<YYYY-MM-DD>`` is the Asia/Taipei date of the run and ``<slug>`` is an
ASCII-safe identifier derived from the episode slug passed to
``EpisodeMetadata``.

Per-run directory layout::

    data/repurpose/<YYYY-MM-DD>-<slug>/
        stage1.json                     # Stage 1 extractor output
        blog.md                         # Blog renderer output
        fb-light.md                     # FB renderer, light tonal variant
        fb-emotional.md                 # FB renderer, emotional tonal variant
        fb-serious.md                   # FB renderer, serious tonal variant
        fb-neutral.md                   # FB renderer, neutral tonal variant
        ig-cards.json                   # IG renderer output (card sequence JSON)

Plug-in Pattern (Line 2 / Line 3)
----------------------------------
``RepurposeEngine`` is line-agnostic.  To onboard Line 2 (book notes) or
Line 3 (literature → science popularization), implement two protocols:

1. ``Stage1Extractor`` — converts raw source into a ``Stage1Result`` carrying
   the structured narrative materials for downstream renderers.

2. ``ChannelRenderer`` — consumes a ``Stage1Result`` + episode metadata and
   produces one or more ``ChannelArtifact`` instances.

Then wire them into the engine::

    engine = RepurposeEngine(
        extractor=MyLine2Extractor(),
        renderers={"blog": BlogRenderer(), "fb": FBRenderer()},
    )
    artifacts = engine.run(source_input, episode_metadata)

The engine handles:
- Parallel fan-out across all renderers (``concurrent.futures.ThreadPoolExecutor``)
- Per-renderer error isolation (one failure does not abort others)
- Stage 1 JSON persistence to ``stage1.json``
- Artifact path resolution and writing
- Structured logging of each stage

Stage 1 schema is NOT shared across lines — each line's extractor defines its
own JSON shape because podcast / book / literature narrative skeletons differ
fundamentally.  Renderers receive the raw ``data`` dict and consume only the
keys they expect.
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol, runtime_checkable
from zoneinfo import ZoneInfo

from shared.log import get_logger

logger = get_logger("nakama.brook.repurpose_engine")

_TAIPEI = ZoneInfo("Asia/Taipei")

# Public constants — shared with thousand_sunny.routers.repurpose so the I/O
# scheme has a single source of truth.
DATA_ROOT = Path("data/repurpose")
FB_TONALS: tuple[str, ...] = ("light", "emotional", "serious", "neutral")
STAGE1_FILENAME = "stage1.json"
BLOG_FILENAME = "blog.md"
IG_FILENAME = "ig-cards.json"


def fb_filename(tonal: str) -> str:
    """Return the FB renderer output filename for a tonal variant."""
    return f"fb-{tonal}.md"


# Internal alias retained for engine internals; routers should import DATA_ROOT.
_DATA_ROOT = DATA_ROOT


# ---------------------------------------------------------------------------
# Domain types
# ---------------------------------------------------------------------------


@dataclass
class EpisodeMetadata:
    """Metadata injected at ``engine.run()`` time — line-agnostic."""

    slug: str
    """Short ASCII identifier (e.g. ``'dr-chu-longevity'``).
    Used in the output directory name after sanitization."""

    date: str = ""
    """YYYY-MM-DD run date (Asia/Taipei).  Auto-set to today if empty."""

    host: str = "張修修"
    """Host name for speaker attribution in templates."""

    extra: dict = field(default_factory=dict)
    """Line-specific extras (e.g. ``guest`` name for podcast, ``author`` for book)."""


@dataclass
class Stage1Result:
    """Structured output of ``Stage1Extractor`` — the shared payload for renderers.

    The schema is intentionally open (``data: dict``) because each line's
    extractor defines its own JSON shape.  Renderers consume only keys they
    expect; unknown keys are safe to ignore.
    """

    data: dict
    """Raw extracted JSON (line-specific schema)."""

    source_repr: str = ""
    """Human-readable summary of the source for debugging (e.g. first 200 chars of SRT)."""


@dataclass
class ChannelArtifact:
    """A single rendered channel output."""

    filename: str
    """Relative filename within the run directory (e.g. ``'blog.md'``, ``'fb-light.md'``)."""

    content: str
    """Text content to write to disk."""

    channel: str
    """Logical channel name (e.g. ``'blog'``, ``'fb-light'``, ``'ig'``)."""


@dataclass
class ChannelArtifacts:
    """Aggregated output from a single ``engine.run()`` call."""

    run_dir: Path
    """Absolute path to the run directory (``data/repurpose/YYYY-MM-DD-slug/``)."""

    stage1: Stage1Result
    artifacts: list[ChannelArtifact] = field(default_factory=list)
    errors: dict[str, Exception] = field(default_factory=dict)
    """Channels that failed during Stage 2 rendering.  Key = channel name."""


# ---------------------------------------------------------------------------
# Protocols
# ---------------------------------------------------------------------------


@runtime_checkable
class Stage1Extractor(Protocol):
    """Converts raw source material into a structured ``Stage1Result``.

    Each line (Line 1 podcast, Line 2 book notes, Line 3 literature) implements
    its own extractor.  The extractor owns the Stage 1 LLM call(s) and JSON
    schema validation.

    Line 1 reference implementation: ``Line1Extractor`` (Slice 3).
    """

    def extract(self, source_input: str, metadata: EpisodeMetadata) -> Stage1Result:
        """Extract structured narrative materials from ``source_input``.

        Args:
            source_input: Raw source material (e.g. SRT text, book notes).
            metadata: Episode/run metadata for speaker attribution etc.

        Returns:
            Stage1Result with validated JSON data.

        Raises:
            ValueError: If the source is malformed or the LLM output fails
                schema validation after retries.
        """
        ...  # pragma: no cover


@runtime_checkable
class ChannelRenderer(Protocol):
    """Renders one or more channel artifacts from Stage 1 output.

    Implementations: ``BlogRenderer``, ``FBRenderer``, ``IGRenderer`` (Line 1).
    Each renderer owns its Stage 2 LLM call + profile loading + output formatting.

    Returns a list to support multi-variant renderers (e.g. ``FBRenderer``
    yields 4 tonal variants).  Single-output renderers return a 1-element list.
    """

    def render(self, stage1: Stage1Result, metadata: EpisodeMetadata) -> list[ChannelArtifact]:
        """Render one or more channel artifacts.

        Args:
            stage1: Stage 1 structured output.
            metadata: Episode metadata (slug, host, extras).

        Returns:
            List of ChannelArtifact instances (≥1).

        Raises:
            RuntimeError: If rendering fails and no fallback is available.
        """
        ...  # pragma: no cover


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


def _resolve_run_dir(slug: str, date: str) -> Path:
    """Resolve the output directory path for this run (does NOT create it).

    Slug sanitization order:
        1. Replace anything outside [A-Za-z0-9_-] with '-'.
        2. Strip leading/trailing '-' (turn ``"--abc--"`` into ``"abc"``).
        3. Truncate to 60 chars, then rstrip '-' again so a mid-segment cut
           does not leave a trailing dash (e.g. ``"a-b-c"[:5] == "a-b-c"`` but
           ``"a-b-c-"[:5] == "a-b-c"`` after rstrip).
        4. Fall back to ``"episode"`` if the result is empty.

    The directory is NOT created here so that a Stage 1 failure does not leave
    an empty leftover directory. The caller mkdirs before writing artifacts.
    """
    slug_safe = re.sub(r"[^A-Za-z0-9_-]+", "-", slug).strip("-")
    slug_safe = slug_safe[:60].rstrip("-") or "episode"
    return _DATA_ROOT / f"{date}-{slug_safe}"


class RepurposeEngine:
    """Line-agnostic orchestrator for multi-channel content repurposing.

    Wires together a ``Stage1Extractor`` and a dict of ``ChannelRenderer``
    instances, handling:

    - Stage 1 extraction and ``stage1.json`` persistence
    - Parallel Stage 2 fan-out (all renderers run concurrently)
    - Per-renderer error isolation (one failure does not abort others)
    - Artifact writing to the I/O path scheme

    Line 1 usage (podcast)::

        engine = RepurposeEngine(
            extractor=Line1Extractor(),
            renderers={
                "blog": BlogRenderer(),
                "fb":   FBRenderer(),
                "ig":   IGRenderer(),
            },
        )
        result = engine.run(srt_text, EpisodeMetadata(slug="dr-chu", host="張修修"))

    Line 2 usage (future — book notes)::

        engine = RepurposeEngine(
            extractor=BookNotesExtractor(),
            renderers={"blog": BlogRenderer(), "fb": FBRenderer()},
        )

    See module docstring for full I/O path scheme and plug-in pattern.
    """

    def __init__(
        self,
        extractor: Stage1Extractor,
        renderers: dict[str, ChannelRenderer],
        *,
        max_workers: int = 6,
    ) -> None:
        self._extractor = extractor
        self._renderers = dict(renderers)
        self._max_workers = max_workers

    def run(self, source_input: str, metadata: EpisodeMetadata) -> ChannelArtifacts:
        """Execute the full repurpose pipeline.

        Steps:

        1. Resolve run directory (``data/repurpose/YYYY-MM-DD-slug/``).
        2. Run Stage 1 extraction → persist ``stage1.json``.
        3. Fan-out Stage 2 renderers in parallel.
        4. Write each artifact; collect errors without aborting.

        Args:
            source_input: Raw source material forwarded to the extractor.
            metadata: Episode metadata.  ``date`` is auto-set if empty.

        Returns:
            ``ChannelArtifacts`` with ``run_dir``, ``stage1``, ``artifacts``,
            and any ``errors``.
        """
        # Resolve date locally — do NOT mutate caller's metadata.
        run_date = metadata.date or datetime.now(_TAIPEI).strftime("%Y-%m-%d")
        run_dir = _resolve_run_dir(metadata.slug, run_date)
        logger.info(f"repurpose run_dir={run_dir} slug={metadata.slug!r}")

        # ── Stage 1 ──────────────────────────────────────────────────────────
        # Extract first; mkdir AFTER success so a failed extractor does not
        # leave an empty leftover directory on disk.
        stage1 = self._extractor.extract(source_input, metadata)
        run_dir.mkdir(parents=True, exist_ok=True)
        stage1_path = run_dir / STAGE1_FILENAME
        stage1_path.write_text(
            json.dumps(stage1.data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.info(f"stage1 written → {stage1_path}")

        # ── Stage 2 fan-out ───────────────────────────────────────────────────
        # Per-renderer error isolation covers BOTH renderer execution AND
        # artifact disk write — a OSError on one channel must not abort peers.
        result = ChannelArtifacts(run_dir=run_dir, stage1=stage1)
        futures: dict = {}

        with ThreadPoolExecutor(max_workers=self._max_workers) as pool:
            for name, renderer in self._renderers.items():
                fut = pool.submit(renderer.render, stage1, metadata)
                futures[fut] = name

            for fut in as_completed(futures):
                channel_name = futures[fut]
                try:
                    artifacts = fut.result()
                except Exception as exc:
                    logger.error(f"renderer {channel_name!r} failed: {exc}", exc_info=True)
                    result.errors[channel_name] = exc
                    continue
                for artifact in artifacts:
                    path = run_dir / artifact.filename
                    try:
                        path.write_text(artifact.content, encoding="utf-8")
                    except OSError as exc:
                        logger.error(f"write {path} failed: {exc}", exc_info=True)
                        result.errors[channel_name] = exc
                        continue
                    result.artifacts.append(artifact)
                    logger.info(f"artifact written → {path}")

        return result
