"""Mandarin text normalization layer (ADR-032 §5b).

Must handle (PR-2):
- Full-width / half-width punctuation unification
- Numeric formats: 一萬一千 / 11000 / 11,000 / 一·一萬 → canonical
- Smart SRT cue joining at sentence boundaries (。？！) vs prosody hint
- Taiwan-standard 「」 brackets enforced; Western/mainland forms rewritten + warned

Stub for PR-1; concrete fns + cn2an dependency in PR-2.
"""

from __future__ import annotations


def normalize_punctuation(text: str) -> str:  # pragma: no cover — PR-2
    raise NotImplementedError("PR-2 — see issue #713")


def normalize_numbers(text: str) -> str:  # pragma: no cover — PR-2
    raise NotImplementedError("PR-2 — see issue #713")


def smart_join_cues(cues):  # pragma: no cover — PR-2
    raise NotImplementedError("PR-2 — see issue #713")


def enforce_taiwan_quotes(text: str) -> str:  # pragma: no cover — PR-2
    raise NotImplementedError("PR-2 — see issue #713")
