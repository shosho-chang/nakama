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
import os
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
    ``pyproject.toml`` rather than whatever happens to be on ``PATH``.

    ``--js-runtimes node`` is injected unconditionally: yt-dlp ≥2025.x
    requires a JS runtime for YouTube extraction and only enables Deno by
    default, but Node.js is always present in this environment. Without
    this flag every YouTube call emits a WARNING and may silently fail.
    Note: the runtime name is ``node`` (not ``nodejs``) per yt-dlp's enum.

    If the environment variable ``YTDLP_COOKIES_PATH`` is set and the file
    exists, ``--cookies <path>`` is injected so yt-dlp can authenticate as a
    logged-in user. This bypasses YouTube's "Sign in to confirm you're not a
    bot" block that datacenter IPs routinely hit.

    The cookie file is *copied to a throwaway temp file* before being handed
    to yt-dlp, because yt-dlp writes the (rotated) cookie jar back to the
    file it is given on every run. Pointing it at the user's exported file
    directly degrades that file over repeated calls — session tokens get
    rotated to values YouTube eventually rejects, manifesting as the
    "Sign in to confirm you're not a bot" error. The source file the user
    exported must stay pristine; only the disposable copy is ever mutated.
    """
    import shutil
    import sys
    import tempfile

    cookies_flags: list[str] = []
    tmp_cookies: str | None = None
    cookies_path = os.environ.get("YTDLP_COOKIES_PATH", "").strip()
    if cookies_path and Path(cookies_path).is_file():
        fd, tmp_cookies = tempfile.mkstemp(suffix=".txt", prefix="ytcookies-")
        os.close(fd)
        shutil.copyfile(cookies_path, tmp_cookies)
        cookies_flags = ["--cookies", tmp_cookies]

    cmd = [sys.executable, "-m", "yt_dlp", "--js-runtimes", "node", *cookies_flags, *args]
    try:
        return subprocess.run(  # noqa: S603  # cmd vector built from constant + caller-controlled args
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
            check=False,
        )
    finally:
        if tmp_cookies:
            try:
                os.unlink(tmp_cookies)
            except OSError:
                pass


def _iso8601_duration_to_seconds(duration: str) -> int:
    """PT1H2M3S → total seconds. Returns 0 for unrecognised input."""
    m = re.fullmatch(r"P(?:(\d+)D)?T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration or "")
    if not m:
        return 0
    d, h, mi, s = (int(g or 0) for g in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + s


def fetch_metadata(url: str) -> YouTubeMetadata:
    """Fetch video metadata via YouTube Data API v3.

    Requires ``YOUTUBE_API_KEY`` in the environment. Uses the official API
    instead of ``yt-dlp --dump-json`` to avoid YouTube's datacenter-IP
    bot-detection ("Sign in to confirm you're not a bot"). Public video
    metadata is freely available via the Data API without any OAuth.

    ``available_auto_captions`` is always ``[]`` — the Data API does not
    expose auto-generated caption tracks. The caption download step handles
    :class:`NoCaptionAvailable` directly.
    """
    import urllib.error
    import urllib.parse
    import urllib.request

    video_id = extract_video_id(url)

    api_key = os.environ.get("YOUTUBE_API_KEY", "").strip()
    if not api_key:
        raise YtDlpError(
            "YOUTUBE_API_KEY not set",
            stderr="Add YOUTUBE_API_KEY to .env — free key from console.cloud.google.com",
        )

    params = urllib.parse.urlencode(
        {"id": video_id, "part": "snippet,contentDetails", "key": api_key}
    )
    api_url = f"https://www.googleapis.com/youtube/v3/videos?{params}"

    try:
        with urllib.request.urlopen(api_url, timeout=15) as resp:  # noqa: S310
            payload = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")[:400]
        raise YtDlpError(
            f"YouTube Data API HTTP {exc.code} for {video_id!r}",
            stderr=body,
        ) from exc
    except Exception as exc:
        raise YtDlpError(
            f"YouTube Data API request failed for {video_id!r}",
            stderr=str(exc),
        ) from exc

    items = payload.get("items", [])
    if not items:
        raise YtDlpError(
            f"影片 {video_id!r} 不存在或為私人影片",
            stderr=f"API returned empty items; response keys: {list(payload)}",
        )

    snippet = items[0].get("snippet", {})
    content = items[0].get("contentDetails", {})

    return YouTubeMetadata(
        video_id=video_id,
        title=str(snippet.get("title") or "").strip() or video_id,
        channel=str(snippet.get("channelTitle") or "").strip(),
        duration_s=_iso8601_duration_to_seconds(content.get("duration", "")),
        url=f"https://youtube.com/watch?v={video_id}",
        available_auto_captions=[],
    )


def _pick_caption_lang(available: list[str]) -> str | None:
    """Return the actual yt-dlp lang tag matching the first priority family
    that has an available track, or ``None`` if none match.

    Exact priority-tag match wins (``"en"`` if available). Otherwise falls
    back to the first variant in the priority family (``"en-orig"``,
    ``"en-US"``, ``"zh-Hant-zh-TW"``). The returned tag is the actual yt-dlp
    track id, NOT the priority constant — so ``manifest.primary_lang``
    matches the on-disk VTT track name verbatim (code-review fix on PR
    #771: was previously returning ``pref``, which mismatched yt-dlp's
    track id on re-uploads where auto-caption is ``en-orig``).
    """
    for pref in CAPTION_LANG_PRIORITY:
        if pref in available:
            return pref
        for cand in available:
            if cand.startswith(f"{pref}-") or cand.startswith(f"{pref}."):
                return cand
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
    # ``--ignore-errors``: when ``--sub-lang en,zh-Hant,zh-CN`` requests
    # multiple tracks and YouTube rate-limits one of them with HTTP 429,
    # we still want the tracks that did download to land on disk. Without
    # this flag a single 429 on (e.g.) ``zh-Hant`` taints the whole
    # subprocess and we'd discard a perfectly good ``en`` VTT.
    # ``--retry-sleep extractor:5``: back off 5s on extractor errors
    # (covers the 429 case yt-dlp surfaces from the subtitle endpoint).
    result = _run_yt_dlp(
        [
            "--write-auto-sub",
            "--sub-lang",
            lang_arg,
            "--sub-format",
            "vtt",
            "--skip-download",
            "--no-warnings",
            "--ignore-errors",
            "--retry-sleep",
            "extractor:5",
            "-o",
            str(output_dir / f"{video_id}.%(ext)s"),
            canonical_url,
        ]
    )
    # With ``--ignore-errors`` yt-dlp may exit non-zero even when at least
    # one track wrote. The dir scan below is now the source of truth —
    # only fall back to YtDlpError when the dir is empty AND subprocess failed.
    if result.returncode != 0 and not any(output_dir.glob(f"{video_id}.*.vtt")):
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

    # ``chosen_lang`` is the actual track id (post-PR #771 review fix), so
    # the candidates dict — keyed by that same track id from the disk scan
    # — resolves directly. NoCaptionAvailable here would indicate a logic
    # bug, not user-facing state.
    chosen_path = candidates.get(chosen_lang)
    if chosen_path is None:  # pragma: no cover — would be a logic bug, not env
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
