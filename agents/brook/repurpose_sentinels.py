"""Per-channel approval / publish sentinel helpers for repurpose runs.

Single source of truth for the ``.approved.<channel>`` / ``.published.<channel>``
filename convention shared between:

- ``thousand_sunny.routers.repurpose`` — Bridge UI writes sentinels on approve.
- ``agents.brook.repurpose_engine`` — engine reads sentinels before overwriting
  artifacts on re-run (#682).
- ``scripts.run_repurpose`` — ``--force`` clears sentinels before a re-run.

Channel naming
--------------
The canonical channel set is::

    blog
    fb.light / fb.emotional / fb.serious / fb.neutral
    ig

Channel name doubles as the sentinel suffix (``.approved.blog``, ``.approved.fb.light``).
The dot in ``fb.<tonal>`` is intentional — the artifact filename uses a dash
(``fb-light.md``) but the channel id keeps a dot to mirror the approve route
shape (``/approve/fb.light``).

``ApprovalSkipException`` is the sentinel exception engine stores under
``result.errors[channel]`` when a channel is skipped because it was approved.
It is a subclass of ``Exception`` (not raised — recorded), so callers can
``isinstance`` filter against it to distinguish "skipped on purpose" from
"renderer / write failed".
"""

from __future__ import annotations

from pathlib import Path

from agents.brook.repurpose_engine import (
    BLOG_FILENAME,
    FB_TONALS,
    IG_FILENAME,
    fb_filename,
)

# ---------------------------------------------------------------------------
# Channel catalog
# ---------------------------------------------------------------------------

CHANNELS: tuple[str, ...] = ("blog", *(f"fb.{t}" for t in FB_TONALS), "ig")
"""Canonical channel id tuple — shared between router approve route and engine."""

CHANNEL_SET = frozenset(CHANNELS)


# ---------------------------------------------------------------------------
# Filename ↔ channel mapping
# ---------------------------------------------------------------------------


def channel_for_filename(filename: str) -> str | None:
    """Return the channel id that owns ``filename``, or ``None`` if unknown.

    Defensive: returns ``None`` rather than raising for files we do not
    recognise (e.g. ``stage1.json``, ``.approved.blog`` itself, or an
    unrelated leftover) so callers iterating ``run_dir.iterdir()`` do not
    crash on noise.
    """
    if filename == BLOG_FILENAME:
        return "blog"
    if filename == IG_FILENAME:
        return "ig"
    for tonal in FB_TONALS:
        if filename == fb_filename(tonal):
            return f"fb.{tonal}"
    return None


def channel_artifact_path(run_dir: Path, channel: str) -> Path:
    """Map a channel id to its artifact file path inside ``run_dir``."""
    if channel == "blog":
        return run_dir / BLOG_FILENAME
    if channel == "ig":
        return run_dir / IG_FILENAME
    if channel.startswith("fb."):
        return run_dir / fb_filename(channel.removeprefix("fb."))
    raise ValueError(f"unknown channel {channel!r}")


# ---------------------------------------------------------------------------
# Sentinel paths
# ---------------------------------------------------------------------------

_SENTINEL_KINDS = frozenset({"approved", "published"})


def sentinel_path(run_dir: Path, kind: str, channel: str) -> Path:
    """Return the sentinel path ``run_dir/.<kind>.<channel>``.

    Channel + kind are both re-validated here because the values land in a
    filesystem path. Defence-in-depth even though callers typically check first.
    """
    if channel not in CHANNEL_SET:
        raise ValueError(f"unknown channel {channel!r}")
    if kind not in _SENTINEL_KINDS:
        raise ValueError(f"unknown sentinel kind {kind!r}")
    return run_dir / f".{kind}.{channel}"


def channel_approved(run_dir: Path, channel: str) -> bool:
    """True iff ``.approved.<channel>`` exists in ``run_dir``."""
    return sentinel_path(run_dir, "approved", channel).exists()


def clear_approval_sentinels(run_dir: Path) -> list[str]:
    """Delete every ``.approved.<channel>`` sentinel in ``run_dir``.

    Returns the list of channel ids whose sentinels were actually removed.
    Used by ``scripts.run_repurpose --force`` to allow an explicit re-run that
    invalidates prior approvals. ``.published.*`` sentinels are NOT cleared —
    a republish should be an explicit Bridge action.
    """
    cleared: list[str] = []
    for channel in CHANNELS:
        path = sentinel_path(run_dir, "approved", channel)
        if path.exists():
            path.unlink()
            cleared.append(channel)
    return cleared


# ---------------------------------------------------------------------------
# Engine-side skip marker
# ---------------------------------------------------------------------------


class ApprovalSkipException(Exception):
    """Recorded in ``ChannelArtifacts.errors`` when the engine skips an artifact
    because its channel is already approved.

    Not raised — engine writes it as a sentinel value so downstream callers can
    ``isinstance(exc, ApprovalSkipException)`` to distinguish intentional skip
    from a renderer / OS failure. Same shape as the other entries in
    ``result.errors``: keyed by channel id.
    """

    def __init__(self, channel: str, sentinel: Path):
        self.channel = channel
        self.sentinel = sentinel
        super().__init__(
            f"channel {channel!r} already approved (sentinel {sentinel.name} present) — "
            "skipped to preserve reviewed content; pass --force to override"
        )
