"""YouTube ingestion helpers (ADR-035 PR1c-i).

Thin wrapper around ``yt-dlp`` for the Robin Watchlist ingestion route. Two
operations:

1. :func:`extract_video_id` — pull the 11-char id out of any YouTube URL
   form (``watch?v=``, ``youtu.be/``, ``shorts/``, ``embed/``, ``v/``). No
   network call.
2. :func:`fetch_metadata` — ``yt-dlp --dump-json --skip-download`` to grab
   title / channel / duration / available auto-caption languages.
3. :func:`fetch_caption` — ``yt-dlp --write-auto-sub --sub-lang en,zh-Hant,zh-CN``
   ``--sub-format vtt --skip-download`` into a caller-provided output dir,
   returns the path to the chosen VTT file (preferring ``en`` per ADR-035
   §D2 / 修修 reads ~98% English content).

Exceptions:

- :class:`InvalidYouTubeURL` — URL doesn't yield a video_id.
- :class:`NoCaptionAvailable` — yt-dlp succeeded but no auto-caption file
  for any of the requested languages landed in the output dir.
- :class:`YtDlpError` — yt-dlp exited non-zero (network, geo-block, age
  gate, private video). Original stderr is preserved on ``.stderr``.

All functions are I/O-bound and should be called via
``asyncio.to_thread`` from FastAPI handlers.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from shared.log import get_logger

_logger = get_logger("nakama.shared.youtube_ingest")

# YouTube id alphabet (mirrors ``shared.reading_source_registry._VALID_YOUTUBE_ID``)
# pinned to length 11 here only because we're extracting from URLs — the
# resolver intentionally accepts shorter ids for forward compat. URL forms
# always carry exactly-11-char ids.
_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")

# Patterns that cover the common YouTube URL surfaces. Ordered most-specific
# first; first match wins.
_URL_PATTERNS = [
    re.compile(r"(?:youtube\.com|youtube-nocookie\.com)/watch\?(?:[^#]*&)?v=([A-Za-z0-9_-]{11})"),
    re.compile(r"youtu\.be/([A-Za-z0-9_-]{11})"),
    re.compile(r"youtube\.com/shorts/([A-Za-z0-9_-]{11})"),
    re.compile(r"youtube\.com/embed/([A-Za-z0-9_-]{11})"),
    re.compile(r"youtube\.com/v/([A-Za-z0-9_-]{11})"),
    re.compile(r"youtube\.com/live/([A-Za-z0-9_-]{11})"),
]

# Caption language priority per ADR-035 §D2. ``en`` first because 修修's
# self-assessment is ~98% English source consumption.
CAPTION_LANG_PRIORITY: tuple[str, ...] = ("en", "zh-Hant", "zh-CN")


class InvalidYouTubeURL(ValueError):
    """Raised when a URL doesn't contain an extractable YouTube video id."""


class NoCaptionAvailable(RuntimeError):
    """Raised when no auto-caption track is available for any of the
    project's priority languages (en / zh-Hant / zh-CN).

    Surfaced to the user as a 400 in the ingestion route so they can either
    pick a different video or wait for Phase 2 Local Whisper.
    """


class YtDlpError(RuntimeError):
    """Raised when the ``yt-dlp`` subprocess exits non-zero (network,
    geo-block, age gate, private video, removed video). ``stderr`` carries
    the original yt-dlp error message for diagnostics."""

    def __init__(self, message: str, *, stderr: str = "") -> None:
        super().__init__(message)
        self.stderr = stderr


@dataclass(frozen=True)
class YouTubeMetadata:
    """Trimmed subset of yt-dlp's ``--dump-json`` output. Only the fields
    the cast form needs to prefill + the manifest needs to persist."""

    video_id: str
    title: str
    channel: str
    duration_s: int
    url: str
    """Canonical ``https://youtube.com/watch?v={video_id}`` (rewritten — we
    don't pass the user's original URL through, which may carry ``&t=``
    timestamps or playlist ids we don't want in the manifest)."""

    available_auto_captions: list[str]
    """BCP-47-ish lang tags for which yt-dlp reports an auto-caption track.
    Used by the route to decide whether to attempt :func:`fetch_caption`
    or 400 with :class:`NoCaptionAvailable` up front."""


def extract_video_id(url: str) -> str:
    """Return the 11-char YouTube video id contained in ``url``.

    Accepts the common surfaces: ``watch?v=``, ``youtu.be/``, ``shorts/``,
    ``embed/``, ``v/``, ``live/``. Also accepts a bare 11-char id (so the
    user can paste just the id, not a URL).

    Raises :class:`InvalidYouTubeURL` for anything else.
    """
    if not url or not isinstance(url, str):
        raise InvalidYouTubeURL("URL is empty")

    candidate = url.strip()
    # Bare id paste.
    if _VIDEO_ID_RE.fullmatch(candidate):
        return candidate

    for pattern in _URL_PATTERNS:
        match = pattern.search(candidate)
        if match:
            return match.group(1)

    raise InvalidYouTubeURL(f"could not extract YouTube video id from URL: {url!r}")


def _run_yt_dlp(args: list[str], *, timeout: int = 90) -> subprocess.CompletedProcess[str]:
    """Invoke the ``yt-dlp`` Python CLI module. Using ``-m yt_dlp`` (not the
    bare ``yt-dlp`` executable) ensures we hit the version we pin in
    ``pyproject.toml`` rather than whatever happens to be on ``PATH``."""
    import sys

    cmd = [sys.executable, "-m", "yt_dlp", *args]
    return subprocess.run(  # noqa: S603  # cmd vector built from constant + caller-controlled args
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout,
        check=False,
    )


def fetch_metadata(url: str) -> YouTubeMetadata:
    """``yt-dlp --dump-json --skip-download`` then normalise to
    :class:`YouTubeMetadata`.

    Always re-extracts ``video_id`` from the URL (rather than trusting the
    JSON ``id`` field) so a typo'd URL fails fast at parse time, not at
    manifest-write time.
    """
    video_id = extract_video_id(url)
    canonical_url = f"https://youtube.com/watch?v={video_id}"

    result = _run_yt_dlp(["--dump-json", "--skip-download", canonical_url])
    if result.returncode != 0:
        raise YtDlpError(
            f"yt-dlp metadata fetch failed for {video_id!r}",
            stderr=result.stderr.strip(),
        )

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise YtDlpError(f"yt-dlp returned non-JSON stdout for {video_id!r}") from exc

    title = str(payload.get("title") or "").strip() or video_id
    channel = str(payload.get("channel") or payload.get("uploader") or "").strip()
    duration_s = int(payload.get("duration") or 0)
    auto_caps = payload.get("automatic_captions") or {}
    available = sorted(str(k) for k in auto_caps.keys()) if isinstance(auto_caps, dict) else []

    return YouTubeMetadata(
        video_id=video_id,
        title=title,
        channel=channel,
        duration_s=duration_s,
        url=canonical_url,
        available_auto_captions=available,
    )


def _pick_caption_lang(available: list[str]) -> str | None:
    """Return the first language from :data:`CAPTION_LANG_PRIORITY` that
    yt-dlp reports as available, or ``None`` if none match.

    Match is exact on the priority tag, but also accepts the ``-orig``
    suffix yt-dlp sometimes emits (``en-orig``) and the prefix family
    match (``zh-Hant-zh-TW`` etc.).
    """
    available_set = set(available)
    for pref in CAPTION_LANG_PRIORITY:
        if pref in available_set:
            return pref
        # yt-dlp variants: ``en-orig``, ``zh-Hant-zh-TW``
        for cand in available:
            if cand == pref or cand.startswith(f"{pref}-") or cand.startswith(f"{pref}."):
                return pref
    return None


def fetch_caption(video_id: str, output_dir: Path) -> tuple[Path, str]:
    """Download auto-caption VTT into ``output_dir``.

    Returns ``(vtt_path, lang)`` where ``lang`` is the
    :data:`CAPTION_LANG_PRIORITY` tag that was actually written.

    Raises :class:`NoCaptionAvailable` if yt-dlp finishes without dropping
    a VTT for any of the priority languages (covers the case where the
    metadata reported a track that turned out to be empty / unavailable).
    Raises :class:`YtDlpError` for subprocess failures.
    """
    if not _VIDEO_ID_RE.fullmatch(video_id):
        # Defence-in-depth — the caller has already validated, but keep
        # this guard so ``video_id`` cannot be smuggled into the output
        # template path through a route that forgot to validate.
        raise InvalidYouTubeURL(f"refusing to fetch caption for unsafe video_id: {video_id!r}")

    output_dir.mkdir(parents=True, exist_ok=True)
    canonical_url = f"https://youtube.com/watch?v={video_id}"
    lang_arg = ",".join(CAPTION_LANG_PRIORITY)

    # ``-o`` template: ``<video_id>`` so yt-dlp writes ``<video_id>.<lang>.vtt``
    # next to our chosen dir. ``--no-warnings`` keeps stderr signal/noise high.
    result = _run_yt_dlp(
        [
            "--write-auto-sub",
            "--sub-lang",
            lang_arg,
            "--sub-format",
            "vtt",
            "--skip-download",
            "--no-warnings",
            "-o",
            str(output_dir / f"{video_id}.%(ext)s"),
            canonical_url,
        ]
    )
    if result.returncode != 0:
        raise YtDlpError(
            f"yt-dlp caption fetch failed for {video_id!r}",
            stderr=result.stderr.strip(),
        )

    # yt-dlp writes ``<video_id>.<lang>.vtt`` — find the one matching our
    # priority order. Use the dir scan rather than parsing stdout so the
    # logic survives yt-dlp output format changes.
    candidates: dict[str, Path] = {}
    for entry in output_dir.iterdir():
        if not entry.is_file() or entry.suffix.lower() != ".vtt":
            continue
        # ``<video_id>.<lang>.vtt`` → split on the trailing ``.vtt`` then
        # peel ``<video_id>.`` prefix.
        stem = entry.stem  # ``<video_id>.<lang>``
        prefix = f"{video_id}."
        if not stem.startswith(prefix):
            continue
        lang_tag = stem[len(prefix) :]
        candidates[lang_tag] = entry

    if not candidates:
        raise NoCaptionAvailable(
            f"no auto-caption available for {video_id!r} in any of {CAPTION_LANG_PRIORITY}"
        )

    chosen_lang = _pick_caption_lang(list(candidates.keys()))
    if chosen_lang is None:
        # yt-dlp dropped a vtt but in an off-priority language — treat as
        # unavailable per ADR-035 §D2 (we only consume en / zh-Hant / zh-CN).
        raise NoCaptionAvailable(
            f"auto-caption present for {video_id!r} but not in {CAPTION_LANG_PRIORITY}"
        )

    # Pick the actual file whose tag matches (or starts with) chosen_lang.
    chosen_path: Path | None = candidates.get(chosen_lang)
    if chosen_path is None:
        for tag, path in candidates.items():
            if (
                tag == chosen_lang
                or tag.startswith(f"{chosen_lang}-")
                or tag.startswith(f"{chosen_lang}.")
            ):
                chosen_path = path
                break

    if chosen_path is None:
        raise NoCaptionAvailable(
            f"auto-caption resolution failed for {video_id!r} (chose lang={chosen_lang!r})"
        )

    _logger.info(
        "youtube caption fetched",
        extra={
            "category": "youtube_ingest_caption_fetched",
            "video_id": video_id,
            "lang": chosen_lang,
            "size_bytes": chosen_path.stat().st_size,
        },
    )
    return chosen_path, chosen_lang
