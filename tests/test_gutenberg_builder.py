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


class TestBlockNodeV1ChildrenWhitelist:
    """ADR-004 review borderline #3：per-block-type children type constraint."""

    def test_list_accepts_list_item_children(self):
        BlockNodeV1(
            block_type="list",
            children=[BlockNodeV1(block_type="list_item", content="a")],
        )

    def test_list_rejects_paragraph_child(self):
        with pytest.raises(ValidationError, match="cannot contain 'paragraph'"):
            BlockNodeV1(
                block_type="list",
                children=[BlockNodeV1(block_type="paragraph", content="stray")],
            )

    def test_quote_accepts_paragraph_list_and_nested_quote(self):
        BlockNodeV1(
            block_type="quote",
            children=[
                BlockNodeV1(block_type="paragraph", content="p"),
                BlockNodeV1(
                    block_type="list",
                    children=[BlockNodeV1(block_type="list_item", content="i")],
                ),
                BlockNodeV1(
                    block_type="quote",
                    children=[BlockNodeV1(block_type="paragraph", content="inner")],
                ),
            ],
        )

    def test_quote_rejects_image_child(self):
        with pytest.raises(ValidationError, match="cannot contain 'image'"):
            BlockNodeV1(
                block_type="quote",
                children=[BlockNodeV1(block_type="image", attrs={"src": "/x.jpg"})],
            )

    def test_paragraph_rejects_any_children(self):
        with pytest.raises(ValidationError, match="leaf block"):
            BlockNodeV1(
                block_type="paragraph",
                content="x",
                children=[BlockNodeV1(block_type="paragraph", content="y")],
            )

    def test_leaf_blocks_reject_any_children(self):
        """所有 leaf block（無 children 能力）加 child 都要被擋。"""
        for leaf_type in ("heading", "list_item", "image", "code", "separator"):
            with pytest.raises(ValidationError, match="leaf block"):
                BlockNodeV1(
                    block_type=leaf_type,
                    children=[BlockNodeV1(block_type="paragraph", content="stray")],
                )

    def test_empty_children_always_allowed(self):
        """無 children 的 leaf block 正常建構不受影響。"""
        for block_type in ("paragraph", "heading", "list_item", "image", "code", "separator"):
            BlockNodeV1(block_type=block_type, content="x" if block_type != "separator" else None)

    def test_whitelist_covers_all_block_types(self):
        """_ALLOWED_CHILDREN 必須涵蓋 block_type Literal 所有成員，否則 KeyError。"""
        from typing import get_args

        from shared.schemas.publishing import _ALLOWED_CHILDREN

        literal_members = set(get_args(BlockNodeV1.model_fields["block_type"].annotation))
        assert literal_members == set(_ALLOWED_CHILDREN.keys()), (
            "Literal 新增 block_type 時漏更新 _ALLOWED_CHILDREN"
        )


class TestBlockNodeV1ContentChildrenXor:
    """content × children 不得同時出現（leaf 用 content / container 用 children / 或都無）。"""

    def test_content_and_children_both_set_rejected(self):
        with pytest.raises(ValidationError, match="both 'content' and 'children'"):
            BlockNodeV1(
                block_type="list",
                content="stray",
                children=[BlockNodeV1(block_type="list_item", content="a")],
            )

    def test_content_only_allowed(self):
        BlockNodeV1(block_type="paragraph", content="hello")

    def test_children_only_allowed(self):
        BlockNodeV1(
            block_type="list",
            children=[BlockNodeV1(block_type="list_item", content="a")],
        )

    def test_neither_allowed_for_standalone_block(self):
        BlockNodeV1(block_type="separator")

    def test_empty_string_content_with_children_still_rejected(self):
        """content='' 同樣算「有 content」— 嚴格 not-both 語意。"""
        with pytest.raises(ValidationError, match="both 'content' and 'children'"):
            BlockNodeV1(
                block_type="list",
                content="",
                children=[BlockNodeV1(block_type="list_item", content="a")],
            )


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

    def test_ast_depth_iterative_handles_deep_nesting(self):
        """_ast_depth 用顯式 stack 走訪，超過 sys.getrecursionlimit() 不會 RecursionError。

        ADR-005a §4 follow-up：舊遞迴版本對 pathological 輸入（LLM 亂產 10k 層）會
        打爆 Python recursion limit 造成 DoS。iterative 版不吃 Python frame。
        """
        import sys

        from shared.schemas.publishing import _ast_depth

        target_depth = sys.getrecursionlimit() + 200
        # model_construct 繞過 AST 白名單 + XOR validator，建病態深巢狀樹直測 _ast_depth
        node: BlockNodeV1 = BlockNodeV1(block_type="paragraph", content="leaf")
        for _ in range(target_depth):
            node = BlockNodeV1.model_construct(
                block_type="quote",
                attrs={},
                content=None,
                children=[node],
            )
        # 舊遞迴實作在此 raise RecursionError；iterative 正常回傳 target_depth + 1
        assert _ast_depth([node]) == target_depth + 1


class TestParse:
    def test_parse_raises_not_implemented(self):
        with pytest.raises(NotImplementedError, match="Week 2"):
            gutenberg_builder.parse("<!-- wp:paragraph --><p>x</p><!-- /wp:paragraph -->")
