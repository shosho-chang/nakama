"""Obsidian wikilink parsing — the single implementation.

The vault's dual-write convention stores a task's project as a wikilink
(``projects: ["[[專案名]]"]``), and the weekly file stores 三大要事 the same way.
Reading that value back used to be re-implemented per call site, and each copy
broke differently on a name containing ``[`` or ``]``: character-class stripping
(``.lstrip("[")``) ate every leading bracket, while a ``[^\\]|]+`` regex refused
to match at all. One name, three parsers, three different answers — so a project
named ``[Pod] 蘇予昕`` resolved to ``Pod] 蘇予昕`` in one place and to the raw
``[[[Pod] 蘇予昕]]`` in another, matching the real file in neither (修修 2026-09-10).

:func:`strip_wikilink` is now that one implementation. Names carrying brackets are
rejected at creation time (``project_index.normalize_name``) because Obsidian has
no escape syntax for them, but parsing stays tolerant: hand-written vault files
predate the guard and must still resolve.
"""

from __future__ import annotations

import re

# An *embedded* link — used only as the fallback when the value is not itself a
# bare link (e.g. "見 [[專案]] 的說明"). Deliberately excludes brackets from the
# target so it can find a link's real boundaries inside surrounding prose.
_EMBEDDED_RE = re.compile(r"\[\[([^\[\]|]+)(?:\|[^\]]*)?\]\]")


def strip_wikilink(value: object) -> str:
    """The link target of ``value``: ``"[[專案|別名]]"`` → ``"專案"``.

    Handles the shapes the vault actually holds: a bare wikilink, an aliased one,
    a plain string (already a name), and — the case the old parsers got wrong — a
    name that itself contains brackets, ``"[[[Pod] 蘇予昕]]"`` → ``"[Pod] 蘇予昕"``.
    A whole-value link is unwrapped by taking exactly two characters off each end,
    never by stripping a character class. Non-strings and blanks give ``""``.

    The result keeps any ``path/`` prefix and ``#heading`` suffix — callers that
    match against filenames normalise further (``weekly_indexer._link_key``).
    """
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if not text:
        return ""

    inner: str | None = None
    if len(text) >= 4 and text.startswith("[[") and text.endswith("]]"):
        candidate = text[2:-2]
        # A second ``]]`` inside means the value holds MORE than one link
        # ("[[任務A]] 跟 [[任務B]]" — hand-written 三大要事 do this), so the
        # outermost brackets are not one link's boundaries. Fall through to the
        # embedded search and take the first link, as the old parser did.
        if "]]" not in candidate:
            inner = candidate
    if inner is None:
        m = _EMBEDDED_RE.search(text)
        inner = m.group(1) if m else text

    return inner.split("|", 1)[0].strip()
