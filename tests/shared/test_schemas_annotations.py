"""Behavior tests for ``shared.schemas.annotations`` — the v1/v2 schema split
created in Slice 2A.

v1 (existing paper-source shape) stays unchanged. v2 introduces three item
types — Highlight, Annotation, Comment — all CFI-anchored against an EPUB
identified by ``book_id`` + ``book_version_hash``. The contract:

- ``AnnotationSetV1`` keeps the v1 shape (``source_filename`` / ``base``)
  with ``schema_version=1`` (defaultable; missing in legacy files = v1).
- ``AnnotationSetV2`` carries ``schema_version=2``, ``book_id``,
  ``book_version_hash``, ``base="books"``, and items that union the three
  v2 item types.
- All schemas use ``extra="forbid"`` and ``Literal`` discriminators.

The legacy ``Highlight`` / ``Annotation`` / ``AnnotationSet`` names exported
from ``shared.annotation_store`` MUST keep working — call sites in
``thousand_sunny.routers.robin`` import them by those names. The Slice 2A
implementation is expected to alias them to v1 (``Highlight = HighlightV1``).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

ann_schemas = pytest.importorskip(
    "shared.schemas.annotations",
    reason="shared.schemas.annotations is the production module Slice 2A must create",
)

HighlightV1 = ann_schemas.HighlightV1
AnnotationV1 = ann_schemas.AnnotationV1
AnnotationSetV1 = ann_schemas.AnnotationSetV1

HighlightV2 = ann_schemas.HighlightV2
AnnotationV2 = ann_schemas.AnnotationV2
CommentV2 = ann_schemas.CommentV2
AnnotationSetV2 = ann_schemas.AnnotationSetV2


# ---------------------------------------------------------------------------
# v1 — existing paper-source shape, must stay backward compatible
# ---------------------------------------------------------------------------


def test_highlight_v1_round_trip():
    h = HighlightV1(
        text="selected", created_at="2026-01-01T00:00:00Z", modified_at="2026-01-01T00:00:00Z"
    )
    assert h.type == "highlight"
    restored = HighlightV1(**h.model_dump())
    assert restored == h


def test_annotation_v1_round_trip():
    a = AnnotationV1(
        ref="ref text",
        note="my note",
        created_at="2026-01-01T00:00:00Z",
        modified_at="2026-01-01T00:00:00Z",
    )
    assert a.type == "annotation"
    assert AnnotationV1(**a.model_dump()) == a


def test_annotation_set_v1_round_trip():
    s = AnnotationSetV1(
        slug="paper-x",
        source_filename="paper-x.md",
        base="inbox",
        items=[
            HighlightV1(
                text="t", created_at="2026-01-01T00:00:00Z", modified_at="2026-01-01T00:00:00Z"
            ),
        ],
        updated_at="2026-01-01T00:00:00Z",
    )
    assert s.schema_version == 1
    assert AnnotationSetV1(**s.model_dump()) == s


def test_annotation_set_v1_legacy_aliases_still_export_from_store():
    """Existing imports (Highlight / Annotation / AnnotationSet) must keep working."""
    from shared.annotation_store import Annotation, AnnotationSet, Highlight

    h = Highlight(text="x")
    a = Annotation(ref="r", note="n")
    s = AnnotationSet(slug="x", source_filename="x.md", base="inbox", items=[h, a])
    assert len(s.items) == 2


# ---------------------------------------------------------------------------
# v2 — CFI-anchored, three item types
# ---------------------------------------------------------------------------


def _v2_now():
    return "2026-05-05T00:00:00Z"


def test_highlight_v2_carries_cfi_and_book_version_hash():
    h = HighlightV2(
        cfi="epubcfi(/6/4!/4[ch01]/2/2:0)",
        text_excerpt="A line of text",
        book_version_hash="a" * 64,
        created_at=_v2_now(),
        modified_at=_v2_now(),
    )
    assert h.type == "highlight"
    assert h.cfi.startswith("epubcfi(")
    assert HighlightV2(**h.model_dump()) == h


def test_annotation_v2_carries_cfi_text_excerpt_and_note():
    a = AnnotationV2(
        cfi="epubcfi(/6/4!/4[ch01]/2/2:0)",
        text_excerpt="A line",
        note="Why this matters",
        book_version_hash="a" * 64,
        created_at=_v2_now(),
        modified_at=_v2_now(),
    )
    assert a.type == "annotation"
    assert AnnotationV2(**a.model_dump()) == a


def test_comment_v2_carries_chapter_ref_and_body():
    c = CommentV2(
        chapter_ref="ch01.xhtml",
        cfi_anchor=None,
        body="A long reflection on the chapter, multiple paragraphs of prose...",
        book_version_hash="a" * 64,
        created_at=_v2_now(),
        modified_at=_v2_now(),
    )
    assert c.type == "comment"
    assert c.chapter_ref == "ch01.xhtml"
    assert CommentV2(**c.model_dump()) == c


def test_comment_v2_cfi_anchor_optional():
    c = CommentV2(
        chapter_ref="ch01.xhtml",
        cfi_anchor="epubcfi(/6/4!/4[ch01]/2/2:0)",
        body="...",
        book_version_hash="a" * 64,
        created_at=_v2_now(),
        modified_at=_v2_now(),
    )
    assert c.cfi_anchor is not None


# ---------------------------------------------------------------------------
# v2 — AnnotationSet wraps the three types via discriminated union
# ---------------------------------------------------------------------------


def test_annotation_set_v2_dispatches_three_item_types():
    s = AnnotationSetV2(
        slug="how-to-live",
        book_id="how-to-live",
        book_version_hash="a" * 64,
        base="books",
        items=[
            {
                "type": "highlight",
                "cfi": "epubcfi(/6/4!/4/2:0)",
                "text_excerpt": "h",
                "book_version_hash": "a" * 64,
                "created_at": _v2_now(),
                "modified_at": _v2_now(),
            },
            {
                "type": "annotation",
                "cfi": "epubcfi(/6/4!/4/2:0)",
                "text_excerpt": "a",
                "note": "an",
                "book_version_hash": "a" * 64,
                "created_at": _v2_now(),
                "modified_at": _v2_now(),
            },
            {
                "type": "comment",
                "chapter_ref": "ch01.xhtml",
                "cfi_anchor": None,
                "body": "long prose",
                "book_version_hash": "a" * 64,
                "created_at": _v2_now(),
                "modified_at": _v2_now(),
            },
        ],
        updated_at=_v2_now(),
    )
    assert s.schema_version == 2
    assert s.base == "books"
    assert s.items[0].type == "highlight"
    assert s.items[1].type == "annotation"
    assert s.items[2].type == "comment"


def test_annotation_set_v2_round_trip():
    s = AnnotationSetV2(
        slug="how-to-live",
        book_id="how-to-live",
        book_version_hash="a" * 64,
        items=[],
        updated_at=_v2_now(),
    )
    assert AnnotationSetV2(**s.model_dump()) == s


def test_annotation_set_v2_base_must_be_books():
    """v2 sets are book-rooted by definition; reject any other base."""
    with pytest.raises(ValidationError):
        AnnotationSetV2(
            slug="x",
            book_id="x",
            book_version_hash="a" * 64,
            base="inbox",  # wrong — must be "books"
            items=[],
            updated_at=_v2_now(),
        )


# ---------------------------------------------------------------------------
# extra="forbid" — reject unknown fields on every model
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "model_cls,base_kwargs",
    [
        (
            HighlightV1,
            dict(text="t", created_at="2026-01-01T00:00:00Z", modified_at="2026-01-01T00:00:00Z"),
        ),
        (
            AnnotationV1,
            dict(
                ref="r",
                note="n",
                created_at="2026-01-01T00:00:00Z",
                modified_at="2026-01-01T00:00:00Z",
            ),
        ),
        (
            HighlightV2,
            dict(
                cfi="epubcfi(/6/4!/4/2:0)",
                text_excerpt="t",
                book_version_hash="a" * 64,
                created_at="2026-05-05T00:00:00Z",
                modified_at="2026-05-05T00:00:00Z",
            ),
        ),
        (
            AnnotationV2,
            dict(
                cfi="epubcfi(/6/4!/4/2:0)",
                text_excerpt="t",
                note="n",
                book_version_hash="a" * 64,
                created_at="2026-05-05T00:00:00Z",
                modified_at="2026-05-05T00:00:00Z",
            ),
        ),
        (
            CommentV2,
            dict(
                chapter_ref="ch1.xhtml",
                cfi_anchor=None,
                body="b",
                book_version_hash="a" * 64,
                created_at="2026-05-05T00:00:00Z",
                modified_at="2026-05-05T00:00:00Z",
            ),
        ),
    ],
)
def test_models_forbid_extra_fields(model_cls, base_kwargs):
    with pytest.raises(ValidationError):
        model_cls(**base_kwargs, future_field="oops")


# ---------------------------------------------------------------------------
# Wrong type cannot smuggle into a v2 set
# ---------------------------------------------------------------------------


def test_v2_set_rejects_unknown_item_type():
    with pytest.raises(ValidationError):
        AnnotationSetV2(
            slug="x",
            book_id="x",
            book_version_hash="a" * 64,
            items=[{"type": "ufo", "anything": "x"}],
            updated_at=_v2_now(),
        )


def test_v2_set_rejects_v1_item_shape():
    """v1 Highlight (text-only) must not slip into a v2 set — v2 expects cfi + book_version_hash."""
    with pytest.raises(ValidationError):
        AnnotationSetV2(
            slug="x",
            book_id="x",
            book_version_hash="a" * 64,
            items=[{"type": "highlight", "text": "raw v1 shape"}],
            updated_at=_v2_now(),
        )
