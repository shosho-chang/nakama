"""Tests for relaxed concept slug validator (Patch 2, 2026-05-08).

Concept slug must accept Obsidian wikilink-shaped strings with spaces / `+` /
parens / apostrophes, while still blocking path traversal and Windows-illegal
chars. ``book_id`` validation must remain strict (no behavior change).
"""

from __future__ import annotations

import pytest

from shared.kb_writer import _validate_slug


# ---------- concept slug: relaxed (allow Obsidian wikilink chars) ----------


@pytest.mark.parametrize(
    "slug",
    [
        "NADPH oxidase",
        "Na+-K+ pump",
        "glycogen phosphorylase",
        "vitamin D3",
        "creatine supplementation",
        "Ergogenic Aid",
        "ATP",
        "肌肉肝醣",
        "Branched-chain amino acid (BCAA)",
        "Krebs' cycle",
    ],
)
def test_concept_slug_accepts_wikilink_shapes(slug: str):
    _validate_slug(slug, kind="concept slug")  # no raise


@pytest.mark.parametrize(
    "slug",
    [
        "bad/slug",
        "bad\\slug",
        "bad:slug",
        'bad"slug',
        "bad<slug",
        "bad>slug",
        "bad|slug",
        "bad*slug",
        "bad?slug",
        "../escape",
        "ok/../bad",
        " leading space",
        "trailing space ",
        "",
        ".hidden",
    ],
)
def test_concept_slug_rejects_unsafe(slug: str):
    with pytest.raises(ValueError):
        _validate_slug(slug, kind="concept slug")


# ---------- book_id: strict (unchanged behavior) ----------


@pytest.mark.parametrize(
    "book_id",
    [
        "biochemistry-for-sport-and-exercise-maclaren",
        "sport-nutrition-jeukendrup-4e",
        "book_2024",
    ],
)
def test_book_id_accepts_safe(book_id: str):
    _validate_slug(book_id, kind="book_id")


@pytest.mark.parametrize(
    "book_id",
    [
        "book with space",  # spaces still rejected for book_id
        "book/path",
        "../escape",
        "book+plus",
        "book(paren)",
    ],
)
def test_book_id_rejects_relaxed_chars(book_id: str):
    with pytest.raises(ValueError):
        _validate_slug(book_id, kind="book_id")
