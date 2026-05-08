"""Concept surface-form canonicalization for the S8 ingest pipeline (ADR-020).

Maps any surface form of a biochemistry concept to a single canonical slug so
that fragmented pages (e.g. "ATP" vs "Adenosine Triphosphate" vs "atps") all
resolve to the same concept page on disk.

Pipeline:
  1. NFKC unicode normalization
  2. casefold (locale-independent lowercase)
  3. seed-alias dict lookup (full name → preferred acronym, or variant → base)
  4. plural stripping (-s / -es / -ies) with optional dict re-lookup on stem
"""

from __future__ import annotations

import unicodedata

# Each key is a casefolded surface form that should map to the canonical slug
# on the right. The canonical slug is itself casefolded and space-separated
# (spaces are valid in Obsidian wikilink targets; _slug_from_term handles the
# final filesystem encoding).
#
# Extend this dict as new fragmentation cases are discovered during ingest.
_SEED_ALIAS: dict[str, str] = {
    # ATP / Adenosine Triphosphate
    "adenosine triphosphate": "atp",
    # Phospholipid variants
    "phospholipids": "phospholipid",
    # Lactate / Lactic Acid
    "lactic acid": "lactate",
}

# "-ies"→"-y" before generic "-s" strip.  "-es" is intentionally omitted:
# "enzymes"[-2:] == "es" but the correct de-plural is strip-s ("enzyme"),
# not strip-es ("enzym"). A full stemmer would handle this; for our narrow
# biochemistry use-case the two rules below cover every real ingest case.
_PLURAL_RULES: tuple[tuple[str, str], ...] = (
    ("ies", "y"),  # antibodies → antibody
    ("s", ""),  # enzymes → enzyme, atps → atp
)

# Minimum stem length after stripping to avoid false matches ("as" → "a").
_MIN_STEM_LEN = 3


def canonicalize(surface: str) -> str:
    """Return the canonical slug for *surface*.

    The result is a casefolded, NFKC-normalized string suitable for use as a
    concept page slug.  Idempotent: ``canonicalize(canonicalize(x)) == canonicalize(x)``.
    """
    s = unicodedata.normalize("NFKC", surface).casefold().strip()

    if s in _SEED_ALIAS:
        return _SEED_ALIAS[s]

    for suffix, replacement in _PLURAL_RULES:
        if s.endswith(suffix):
            # Skip generic "-s" strip when the word ends in "-ss" — these are
            # singular-only English words (mass / pass / class / glass /
            # process / business / address) that the naive plural rule
            # mis-handles. Without this check, "atomic mass" → "atomic mas"
            # (BSE ch3 ingest 5/8 PM regression).
            if suffix == "s" and s.endswith("ss"):
                continue
            stem = s[: -len(suffix)] + replacement if suffix else s
            if len(stem) < _MIN_STEM_LEN:
                continue
            if stem in _SEED_ALIAS:
                return _SEED_ALIAS[stem]
            return stem

    return s


def report_collisions(terms: list[str]) -> list[tuple[str, str]]:
    """Return pairs of distinct surface forms that map to the same canonical.

    Used by the P0.5 acceptance gate (condition 6) to detect concept
    fragmentation before pages are written to the KB.
    """
    seen: dict[str, str] = {}
    collisions: list[tuple[str, str]] = []
    for term in terms:
        c = canonicalize(term)
        if c in seen:
            collisions.append((seen[c], term))
        else:
            seen[c] = term
    return collisions
