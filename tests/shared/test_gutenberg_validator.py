"""Tests for shared/gutenberg_validator.py (ADR-005a §4)."""

from __future__ import annotations

from shared.gutenberg_builder import build
from shared.gutenberg_validator import (
    ALLOWED_COMMENT_BLOCK_TYPES,
    VERSION,
    validate,
)
from shared.schemas.publishing import BlockNodeV1

# ---------------------------------------------------------------------------
# Positive: builder 實際輸出 round-trip through validator
# ---------------------------------------------------------------------------


def test_paragraph_from_builder_passes():
    html = build([BlockNodeV1(block_type="paragraph", content="hello world")]).raw_html
    result = validate(html)
    assert result.valid, f"unexpected errors: {result.errors}"
    assert result.errors == []


def test_heading_with_level_attrs_passes():
    html = build(
        [BlockNodeV1(block_type="heading", content="h2 title", attrs={"level": 2})]
    ).raw_html
    result = validate(html)
    assert result.valid, f"errors: {result.errors}"


def test_list_with_items_passes():
    html = build(
        [
            BlockNodeV1(
                block_type="list",
                children=[
                    BlockNodeV1(block_type="list_item", content="a"),
                    BlockNodeV1(block_type="list_item", content="b"),
                ],
            )
        ]
    ).raw_html
    result = validate(html)
    assert result.valid, f"errors: {result.errors}"


def test_quote_containing_paragraph_passes():
    html = build(
        [
            BlockNodeV1(
                block_type="quote",
                children=[BlockNodeV1(block_type="paragraph", content="quoted")],
            )
        ]
    ).raw_html
    result = validate(html)
    assert result.valid, f"errors: {result.errors}"


def test_image_passes():
    html = build(
        [
            BlockNodeV1(
                block_type="image",
                attrs={"src": "https://ex.com/a.png", "alt": "demo"},
            )
        ]
    ).raw_html
    result = validate(html)
    assert result.valid, f"errors: {result.errors}"


def test_separator_passes():
    html = build([BlockNodeV1(block_type="separator")]).raw_html
    result = validate(html)
    assert result.valid, f"errors: {result.errors}"


def test_code_passes():
    html = build([BlockNodeV1(block_type="code", content="x = 1")]).raw_html
    result = validate(html)
    assert result.valid, f"errors: {result.errors}"


# ---------------------------------------------------------------------------
# Negative: 白名單
# ---------------------------------------------------------------------------


def test_unknown_block_type_rejected():
    html = "<!-- wp:rogue -->\n<div>x</div>\n<!-- /wp:rogue -->"
    result = validate(html)
    assert not result.valid
    codes = {e.code for e in result.errors}
    assert "comment_unknown_type" in codes


def test_whitelist_derived_from_schema():
    """白名單必須涵蓋所有 BlockNodeV1 block_type（以 dash form）。"""
    assert "paragraph" in ALLOWED_COMMENT_BLOCK_TYPES
    assert "list-item" in ALLOWED_COMMENT_BLOCK_TYPES
    assert "separator" in ALLOWED_COMMENT_BLOCK_TYPES
    # AST underscore 形式不該直接出現
    assert "list_item" not in ALLOWED_COMMENT_BLOCK_TYPES


# ---------------------------------------------------------------------------
# Negative: comment pairing
# ---------------------------------------------------------------------------


def test_unpaired_open_comment_rejected():
    html = "<!-- wp:paragraph -->\n<p>x</p>"  # missing close
    result = validate(html)
    assert not result.valid
    assert any(e.code == "comment_unpaired" for e in result.errors)


def test_unpaired_close_comment_rejected():
    html = "<p>x</p>\n<!-- /wp:paragraph -->"
    result = validate(html)
    assert not result.valid
    assert any(e.code == "comment_unpaired" for e in result.errors)


def test_crossed_blocks_rejected():
    html = (
        "<!-- wp:paragraph -->\n<!-- wp:heading -->\n<!-- /wp:paragraph -->\n<!-- /wp:heading -->"
    )
    result = validate(html)
    assert not result.valid
    assert any(e.code == "comment_crossed" for e in result.errors)


# ---------------------------------------------------------------------------
# Negative: attr JSON
# ---------------------------------------------------------------------------


def test_invalid_attr_json_rejected():
    html = '<!-- wp:heading {"level":} -->\n<h2>x</h2>\n<!-- /wp:heading -->'
    result = validate(html)
    assert not result.valid
    assert any(e.code == "attr_json_invalid" for e in result.errors)


def test_valid_nested_json_attrs_passes():
    """Phase 1 builder 不產巢狀 JSON，但 validator 要能容忍
    — 防 LLM-generated raw_html 用 balanced-brace JSON 時誤報。"""
    html = '<!-- wp:paragraph {"meta":{"k":1}} -->\n<p>x</p>\n<!-- /wp:paragraph -->'
    result = validate(html)
    assert result.valid, f"errors: {result.errors}"


# ---------------------------------------------------------------------------
# Negative: paragraph cleanliness
# ---------------------------------------------------------------------------


def test_paragraph_with_block_tag_rejected():
    html = "<!-- wp:paragraph -->\n<p>text <h2>heading</h2> more</p>\n<!-- /wp:paragraph -->"
    result = validate(html)
    assert not result.valid
    assert any(e.code == "paragraph_contains_block_tag" for e in result.errors)


def test_paragraph_with_nested_block_comment_rejected():
    html = (
        "<!-- wp:paragraph -->\n"
        "<p>text <!-- wp:heading -->x<!-- /wp:heading --> more</p>\n"
        "<!-- /wp:paragraph -->"
    )
    result = validate(html)
    assert not result.valid
    assert any(e.code == "paragraph_contains_block_comment" for e in result.errors)


def test_escaped_block_tag_in_paragraph_is_safe():
    """`&lt;div&gt;` 在 `<p>` 內是合法 escaped content，不該觸發 paragraph tag 錯誤。"""
    html = (
        "<!-- wp:paragraph -->\n<p>HTML tags like &lt;div&gt; are fine</p>\n<!-- /wp:paragraph -->"
    )
    result = validate(html)
    assert result.valid, f"errors: {result.errors}"


# ---------------------------------------------------------------------------
# Meta
# ---------------------------------------------------------------------------


def test_validator_version_exposed_in_result():
    result = validate("")
    assert result.validator_version == VERSION
    assert result.valid  # 空字串沒有任何 comment / p，依定義無錯


def test_multiple_errors_accumulate():
    html = (
        "<!-- wp:rogue -->\n"  # unknown type (rejected, not on stack)
        "<!-- wp:paragraph {bad json} -->\n"  # attr_json_invalid
        "<p>x <h2>y</h2></p>\n"  # paragraph_contains_block_tag
        "<!-- /wp:paragraph -->"
    )
    result = validate(html)
    assert not result.valid
    codes = {e.code for e in result.errors}
    assert {"comment_unknown_type", "attr_json_invalid", "paragraph_contains_block_tag"} <= codes


def test_non_wp_html_comments_ignored():
    """一般 HTML comment（非 wp: 開頭）不該被 validator 當錯誤處理。"""
    html = (
        "<!-- this is a regular html comment -->\n"
        "<!-- wp:paragraph -->\n"
        "<p>x</p>\n"
        "<!-- /wp:paragraph -->"
    )
    result = validate(html)
    assert result.valid, f"errors: {result.errors}"
