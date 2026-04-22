"""Tests for shared.gutenberg_builder (ADR-005a §3)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from shared import gutenberg_builder
from shared.schemas.publishing import BlockNodeV1, GutenbergHTMLV1


class TestBuildBlockTypes:
    def test_paragraph(self):
        ast = [BlockNodeV1(block_type="paragraph", content="Hello world")]
        out = gutenberg_builder.build(ast)
        assert "<!-- wp:paragraph -->" in out.raw_html
        assert "<p>Hello world</p>" in out.raw_html
        assert "<!-- /wp:paragraph -->" in out.raw_html

    def test_heading_escapes_and_level(self):
        ast = [BlockNodeV1(block_type="heading", attrs={"level": 3}, content="X <b>Y</b>")]
        out = gutenberg_builder.build(ast)
        assert '<!-- wp:heading {"level":3} -->' in out.raw_html
        assert '<h3 class="wp-block-heading">X &lt;b&gt;Y&lt;/b&gt;</h3>' in out.raw_html

    def test_heading_level_must_be_2_to_4(self):
        ast = [BlockNodeV1(block_type="heading", attrs={"level": 5}, content="X")]
        with pytest.raises(ValueError, match="level must be 2-4"):
            gutenberg_builder.build(ast)

    def test_ordered_list_with_items(self):
        items = [
            BlockNodeV1(block_type="list_item", content="a"),
            BlockNodeV1(block_type="list_item", content="b"),
        ]
        ast = [BlockNodeV1(block_type="list", attrs={"ordered": True}, children=items)]
        out = gutenberg_builder.build(ast)
        assert '<!-- wp:list {"ordered":true} -->' in out.raw_html
        assert '<ol class="wp-block-list">' in out.raw_html
        assert "<li>a</li>" in out.raw_html
        assert "<li>b</li>" in out.raw_html

    def test_unordered_list_default(self):
        ast = [BlockNodeV1(block_type="list", children=[])]
        out = gutenberg_builder.build(ast)
        # ordered defaults to False → ul, no attrs JSON
        assert "<!-- wp:list -->" in out.raw_html
        assert '<ul class="wp-block-list">' in out.raw_html

    def test_image_with_src_alt(self):
        ast = [
            BlockNodeV1(
                block_type="image",
                attrs={"src": "/img/x.jpg", "alt": "a photo", "id": 42, "sizeSlug": "large"},
            )
        ]
        out = gutenberg_builder.build(ast)
        assert '"id":42' in out.raw_html
        assert '"sizeSlug":"large"' in out.raw_html
        assert 'src="/img/x.jpg"' in out.raw_html
        assert 'alt="a photo"' in out.raw_html

    def test_code_escapes_html(self):
        ast = [BlockNodeV1(block_type="code", content="<script>x</script>")]
        out = gutenberg_builder.build(ast)
        assert "&lt;script&gt;" in out.raw_html
        assert "<script>" not in out.raw_html

    def test_separator(self):
        ast = [BlockNodeV1(block_type="separator")]
        out = gutenberg_builder.build(ast)
        assert '<hr class="wp-block-separator has-alpha-channel-opacity"/>' in out.raw_html

    def test_quote_wraps_children(self):
        children = [BlockNodeV1(block_type="paragraph", content="quoted")]
        ast = [BlockNodeV1(block_type="quote", children=children)]
        out = gutenberg_builder.build(ast)
        assert '<blockquote class="wp-block-quote">' in out.raw_html
        assert "<p>quoted</p>" in out.raw_html


class TestBlockNodeV1Schema:
    def test_unknown_block_type_rejected(self):
        with pytest.raises(ValidationError):
            BlockNodeV1(block_type="columns", content="x")  # type: ignore[arg-type]

    def test_extra_field_rejected(self):
        with pytest.raises(ValidationError):
            BlockNodeV1(block_type="paragraph", content="x", unknown_field="y")  # type: ignore[call-arg]


class TestGutenbergHTMLV1Validator:
    def test_builder_output_valid(self):
        """GutenbergHTMLV1 constructed with build()'s output must pass validator."""
        ast = [BlockNodeV1(block_type="paragraph", content="hi")]
        built = gutenberg_builder.build(ast)
        # Reconstruct via the validated path — should NOT raise
        GutenbergHTMLV1(
            ast=ast,
            raw_html=built.raw_html,
            validator_version=gutenberg_builder.VERSION,
        )

    def test_mismatched_raw_html_rejected(self):
        ast = [BlockNodeV1(block_type="paragraph", content="real")]
        with pytest.raises(ValidationError):
            GutenbergHTMLV1(ast=ast, raw_html="<p>tampered</p>", validator_version="x")

    def test_ast_depth_limit(self):
        """Nested children deeper than MAX_AST_DEPTH=6 should reject."""
        # Build a 7-deep chain: list → list_item → paragraph (wouldn't nest, but let's test quote)
        # Simpler: 7 nested quotes
        node: BlockNodeV1 = BlockNodeV1(block_type="paragraph", content="leaf")
        for _ in range(7):
            node = BlockNodeV1(block_type="quote", children=[node])
        with pytest.raises(ValidationError, match="AST depth"):
            # Build() uses model_construct which skips validation; to test depth
            # we must construct via validated path
            built = gutenberg_builder.build([node])
            GutenbergHTMLV1(
                ast=[node],
                raw_html=built.raw_html,
                validator_version=gutenberg_builder.VERSION,
            )


class TestParse:
    def test_parse_raises_not_implemented(self):
        with pytest.raises(NotImplementedError, match="Week 2"):
            gutenberg_builder.parse("<!-- wp:paragraph --><p>x</p><!-- /wp:paragraph -->")
