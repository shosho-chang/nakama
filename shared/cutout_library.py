"""Cutout library access for the thumbnail pipeline (ADR-033 D5 + D9).

YouTube uses a pre-built host cutout library at
``Attachments/cutouts/shosho/{emotion}/{n}.png`` — 修修 prepares 6-10 selfies
per emotion once, runs u2net, and the cutouts persist in the vault.

Podcast uses per-episode cutouts at
``Attachments/cutouts/podcast/{ep_slug}/{host,guest}_v{n}.png`` — produced by
the funnel + u2net step. The "active" cutouts for the current episode are
declared in the project frontmatter under ``thumbnail_active_cutouts``.

Emotion resolution honours the bidirectional alias map in
``prompts/thumbnail/emotions.yml`` (ADR-033 D3): callers can pass any of
``{key, zh_tw, alias}`` and get back the canonical English key for filesystem
lookup.
"""

from __future__ import annotations

import random
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from collections.abc import Mapping

_EMOTIONS_YML = Path(__file__).resolve().parent.parent / "prompts" / "thumbnail" / "emotions.yml"


class EmotionLookupError(ValueError):
    """Raised when a free-text emotion term can't be resolved to a canonical key."""


@lru_cache(maxsize=1)
def load_emotions() -> tuple[dict, ...]:
    """Load and cache the emotion manifest.

    Returns a tuple of dicts with keys ``key``, ``zh_tw``, ``aliases``,
    ``description``. Tuple (immutable) so callers can't mutate the cached value.
    """
    raw = yaml.safe_load(_EMOTIONS_YML.read_text(encoding="utf-8"))
    return tuple(raw["emotions"])


def emotion_keys() -> list[str]:
    """Canonical English keys, in declaration order."""
    return [e["key"] for e in load_emotions()]


def resolve_emotion(text: str) -> str:
    """Resolve free-text emotion (English key, zh-Hant, or alias) → canonical key.

    Matching is case-insensitive and trims whitespace. Looks up against
    ``key`` first, then ``zh_tw``, then ``aliases``.

    Raises:
        EmotionLookupError: if the input doesn't match any registered emotion.
            The exception message lists the canonical zh_tw names so the caller
            (e.g. Bridge render endpoint) can show 修修 the valid options.
    """
    needle = text.strip().lower()
    if not needle:
        raise EmotionLookupError("emotion 為空字串")

    for emo in load_emotions():
        if needle == emo["key"].lower():
            return emo["key"]
        if needle == emo["zh_tw"].lower():
            return emo["key"]
        for alias in emo.get("aliases", []) or []:
            if needle == alias.lower():
                return emo["key"]

    valid = " / ".join(e["zh_tw"] for e in load_emotions())
    raise EmotionLookupError(f"無法辨識的 emotion: '{text}'。請使用以下其中一個: {valid}")


def pick_youtube_host(
    emotion_text: str,
    vault_root: Path,
    *,
    rng: random.Random | None = None,
) -> Path:
    """Pick one random cutout for the YouTube route, matching the given emotion.

    Args:
        emotion_text: free text — will be resolved via :func:`resolve_emotion`.
        vault_root: vault path (typically :func:`shared.config.get_vault_path`).
        rng: optional ``random.Random`` for deterministic tests. Default uses
             :mod:`random` module state.

    Returns:
        Absolute path to one PNG in ``Attachments/cutouts/shosho/{emotion}/``.

    Raises:
        EmotionLookupError: if ``emotion_text`` doesn't resolve.
        FileNotFoundError: if the emotion folder doesn't exist or is empty.
            修修 needs to run ``scripts/import_shosho_cutouts.py`` first to
            populate the library.
    """
    emotion = resolve_emotion(emotion_text)
    folder = vault_root / "Attachments" / "cutouts" / "shosho" / emotion
    if not folder.is_dir():
        raise FileNotFoundError(
            f"YouTube host cutout folder missing: {folder}. "
            f"Run scripts/import_shosho_cutouts.py to populate emotion '{emotion}'."
        )
    candidates = sorted(p for p in folder.iterdir() if p.suffix.lower() == ".png")
    if not candidates:
        raise FileNotFoundError(
            f"YouTube host cutout folder empty: {folder}. "
            f"Add at least one transparent PNG for emotion '{emotion}'."
        )
    picker = rng or random
    return picker.choice(candidates)


def pick_podcast_host(
    ep_slug: str,
    emotion_text: str,
    vault_root: Path,
    frontmatter: Mapping,
    *,
    rng: random.Random | None = None,
) -> Path:
    """Pick one host cutout for the Podcast route from the active set.

    The active set lives in ``frontmatter['thumbnail_active_cutouts']['host']``
    (a list of vault-relative paths chosen by 修修 in the funnel-confirm step).
    Emotion matching is best-effort: if any active cutout has the resolved
    emotion in its filename (e.g. ``host_v1_excited.png``), that one wins; if
    none match, fall back to a random active cutout.

    Args:
        ep_slug: episode slug (NFC-normalised). Used to validate the active set.
        emotion_text: free text → resolved via :func:`resolve_emotion`.
        vault_root: vault path for resolving the active cutout paths.
        frontmatter: parsed project frontmatter dict.
        rng: optional Random for deterministic tests.

    Returns:
        Absolute path to one PNG.

    Raises:
        EmotionLookupError: if ``emotion_text`` doesn't resolve.
        FileNotFoundError: if no active cutouts are declared for this episode.
    """
    return _pick_podcast_active(
        kind="host",
        ep_slug=ep_slug,
        emotion_text=emotion_text,
        vault_root=vault_root,
        frontmatter=frontmatter,
        rng=rng,
    )


def pick_podcast_guest(
    ep_slug: str,
    emotion_text: str,
    vault_root: Path,
    frontmatter: Mapping,
    *,
    rng: random.Random | None = None,
) -> Path:
    """Pick one guest cutout for the Podcast route — same contract as
    :func:`pick_podcast_host` but reads ``thumbnail_active_cutouts['guest']``.
    """
    return _pick_podcast_active(
        kind="guest",
        ep_slug=ep_slug,
        emotion_text=emotion_text,
        vault_root=vault_root,
        frontmatter=frontmatter,
        rng=rng,
    )


def _pick_podcast_active(
    *,
    kind: str,
    ep_slug: str,
    emotion_text: str,
    vault_root: Path,
    frontmatter: Mapping,
    rng: random.Random | None,
) -> Path:
    emotion = resolve_emotion(emotion_text)
    active = frontmatter.get("thumbnail_active_cutouts") or {}
    paths = active.get(kind) or []
    if not paths:
        raise FileNotFoundError(
            f"No active {kind} cutouts for podcast episode '{ep_slug}'. "
            f"Run the funnel + confirm step in the Title&Thumbnail tab first."
        )

    resolved = [vault_root / p for p in paths if (vault_root / p).is_file()]
    if not resolved:
        raise FileNotFoundError(
            f"Active {kind} cutout paths declared in frontmatter but none exist on disk "
            f"(ep_slug={ep_slug}). Re-run u2net or pick fresh frames."
        )

    matching = [p for p in resolved if emotion in p.stem.lower()]
    pool = matching or resolved
    picker = rng or random
    return picker.choice(pool)
