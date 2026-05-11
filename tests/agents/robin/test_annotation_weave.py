"""Unit tests for agents.robin.annotation_weave.weave (Slice 3 #391).

Table-driven coverage per acceptance criteria:
- single annotation on a paragraph
- multiple annotations on same paragraph
- annotation on multilingual segment (Chinese ref in > blockquote)
- ref not found → warning logged, callout skipped
- highlight-only (no AnnotationV1) → body unchanged
- unicode ref
- ref containing markdown-special chars
"""

from __future__ import annotations

import pytest

from shared.schemas.annotations import AnnotationV1, HighlightV1


@pytest.fixture(autouse=True)
def _patch_weave_module():
    """Force reimport so annotation_weave picks up the fixture env."""
    import importlib

    import agents.robin.annotation_weave as m

    importlib.reload(m)


def _weave(body, items):
    from agents.robin.annotation_weave import weave

    return weave(body, items)


# ── helpers ───────────────────────────────────────────────────────────────────


def _ann(ref: str, note: str) -> AnnotationV1:
    return AnnotationV1(ref=ref, note=note)


def _hl(text: str) -> HighlightV1:
    return HighlightV1(text=text)


# ── table-driven tests ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "body, items, must_contain, must_not_contain",
    [
        # 1. Single annotation on a paragraph — callout appears after matched block
        (
            "Para one text here.\n\nPara two text here.",
            [_ann("Para one", "My note on para one")],
            [
                "Para one text here.",
                "> [!annotation]",
                "> My note on para one",
                "Para two text here.",
            ],
            [],
        ),
        # 2. Multiple annotations on the same paragraph — both callouts appended in order
        (
            "Shared paragraph content.\n\nOther para.",
            [
                _ann("Shared paragraph", "First note"),
                _ann("paragraph content", "Second note"),
            ],
            [
                "Shared paragraph content.",
                "> [!annotation]",
                "> First note",
                "> Second note",
            ],
            [],
        ),
        # 3. Annotation on a Chinese blockquote line (multilingual segment)
        (
            "English paragraph.\n\n> 中文翻譯段落\n\nNext English.",
            [_ann("中文翻譯", "這是標注")],
            ["> [!annotation]", "> 這是標注"],
            [],
        ),
        # 4. Ref not found → no callout inserted, body unchanged
        (
            "Para one.\n\nPara two.",
            [_ann("NonExistentRefXYZ", "This note should be skipped")],
            ["Para one.", "Para two."],
            ["[!annotation]", "This note should be skipped"],
        ),
        # 5. Highlight-only items → body completely unchanged (no callout)
        (
            "Para with ==highlight==.\n\nSecond para.",
            [_hl("highlight")],
            ["Para with ==highlight==.", "Second para."],
            ["[!annotation]"],
        ),
        # 6. Unicode ref (CJK characters as ref)
        (
            "睡眠品質對健康的影響是多方面的。\n\nAnother paragraph.",
            [_ann("睡眠品質", "Quality sleep matters")],
            ["> [!annotation]", "> Quality sleep matters"],
            [],
        ),
        # 7. Ref containing markdown-special chars — plain substring match, no regex issues
        (
            "This has **bold** and [link](url) text.\n\nOther.",
            [_ann("**bold** and [link]", "Markdown chars in ref")],
            ["> [!annotation]", "> Markdown chars in ref"],
            [],
        ),
    ],
    ids=[
        "single_annotation",
        "multiple_annotations_same_paragraph",
        "annotation_on_multilingual_segment",
        "ref_not_found",
        "highlight_only_no_annotation",
        "unicode_ref",
        "ref_containing_markdown_special_chars",
    ],
)
def test_weave(body, items, must_contain, must_not_contain):
    result = _weave(body, items)
    for expected in must_contain:
        assert expected in result, f"Expected {expected!r} in result:\n{result}"
    for not_expected in must_not_contain:
        assert not_expected not in result, f"Did NOT expect {not_expected!r} in result:\n{result}"


def test_weave_ref_not_found_logs_warning():
    """Ref not found → warning is emitted via the log_fn parameter."""
    from agents.robin.annotation_weave import weave

    warnings = []
    weave(
        "Some body text.",
        [_ann("MissingRef", "note")],
        log_fn=lambda msg, *args, **kw: warnings.append(msg % args if args else msg),
    )
    assert warnings, "Expected a warning when ref is not found"
    assert any("not found" in w or "MissingRef" in w for w in warnings)


def test_weave_multiple_annotations_different_paragraphs():
    """Each annotation is placed after its own matching paragraph."""
    from agents.robin.annotation_weave import weave

    body = "First para.\n\nSecond para.\n\nThird para."
    items = [_ann("First para", "note A"), _ann("Second para", "note B")]
    result = weave(body, items)

    # note A should appear before 'Second para.' and note B after
    idx_first = result.index("First para.")
    idx_note_a = result.index("> note A")
    idx_second = result.index("Second para.")
    idx_note_b = result.index("> note B")

    assert idx_first < idx_note_a < idx_second < idx_note_b


def test_weave_highlight_mixed_with_annotation():
    """HighlightV1 items are silently skipped; AnnotationV1 still processed."""
    from agents.robin.annotation_weave import weave

    body = "Contains ==highlight==.\n\nOther para."
    items = [_hl("highlight"), _ann("Other para", "My annotation")]
    result = weave(body, items)

    assert "==highlight==" in result  # preserved
    assert "> [!annotation]" in result
    assert "> My annotation" in result


def test_weave_multiline_note():
    """Multi-line note produces multi-line blockquote."""
    from agents.robin.annotation_weave import weave

    body = "Target paragraph.\n\nOther."
    note = "Line one\nLine two"
    result = weave(body, [_ann("Target paragraph", note)])
    assert "> Line one" in result
    assert "> Line two" in result


def test_weave_empty_body_no_crash():
    from agents.robin.annotation_weave import weave

    result = weave("", [_ann("anything", "note")])
    # Just verify no exception
    assert isinstance(result, str)


def test_weave_no_items_returns_body_unchanged():
    from agents.robin.annotation_weave import weave

    body = "Some text here.\n\nMore text."
    result = weave(body, [])
    assert result == body
