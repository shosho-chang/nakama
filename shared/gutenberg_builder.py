"""Gutenberg block builder / parser — pure functions, no LLM (ADR-005a §3).

`build(ast)` 保證輸出通過 `GutenbergHTMLV1._ast_and_html_consistent` validator。
`parse(html)` 用於 roundtrip 測試 + 既有 WP 文章 migration。

8 種 block type 白名單：paragraph, heading, list, list_item, quote, image, code, separator。
未知 block_type → 直接 raise，不猜測（ADR-005a §3）。
"""

from __future__ import annotations

import html
import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # avoid runtime circular: publishing.py -> builder.py -> publishing.py
    from shared.schemas.publishing import BlockNodeV1, GutenbergHTMLV1


VERSION = "gutenberg_builder_0.1.0"


class UnknownBlockTypeError(ValueError):
    """非白名單 block_type，禁止猜測（ADR-005a §3）。"""


class MalformedBlockError(ValueError):
    """parse 時遇到不配對 / 交錯 / 語法錯的 block comment。"""


# ---------------------------------------------------------------------------
# Build: AST → HTML
# ---------------------------------------------------------------------------


def build(ast: list[BlockNodeV1]) -> GutenbergHTMLV1:
    """Serialize AST to Gutenberg block HTML.

    Uses model_construct() to skip the _ast_and_html_consistent validator — builder
    IS the canonical constructor, running the validator here would cause infinite
    recursion (validator → build → validator). The validator exists to catch
    manual construction mismatches (tests, migrations), not to second-guess builder.

    Raises:
        UnknownBlockTypeError: block_type 不在白名單
    """
    # 動態 import 避免 schema 模組循環依賴
    from shared.schemas.publishing import GutenbergHTMLV1

    raw_html = "\n\n".join(_render_node(node) for node in ast)
    return GutenbergHTMLV1.model_construct(
        schema_version=1,
        ast=ast,
        raw_html=raw_html,
        validator_version=VERSION,
    )


def _render_node(node: BlockNodeV1) -> str:
    renderer = _RENDERERS.get(node.block_type)
    if renderer is None:
        raise UnknownBlockTypeError(f"unknown block_type: {node.block_type!r}")
    return renderer(node)


def _esc(s: str | None) -> str:
    return html.escape(s or "", quote=True)


def _attrs_json(node: BlockNodeV1, whitelist: tuple[str, ...]) -> str:
    """render block comment attrs as JSON, only including whitelisted keys."""
    data = {k: node.attrs[k] for k in whitelist if k in node.attrs}
    if not data:
        return ""
    return " " + json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def _render_paragraph(node: BlockNodeV1) -> str:
    return f"<!-- wp:paragraph -->\n<p>{_esc(node.content)}</p>\n<!-- /wp:paragraph -->"


def _render_heading(node: BlockNodeV1) -> str:
    level = int(node.attrs.get("level", 2))
    if level not in (2, 3, 4):
        raise ValueError(f"heading level must be 2-4 in Phase 1, got {level}")
    attrs = _attrs_json(node, ("level",))
    return (
        f"<!-- wp:heading{attrs} -->\n"
        f'<h{level} class="wp-block-heading">{_esc(node.content)}</h{level}>\n'
        f"<!-- /wp:heading -->"
    )


def _render_list(node: BlockNodeV1) -> str:
    ordered = bool(node.attrs.get("ordered", False))
    tag = "ol" if ordered else "ul"
    attrs = _attrs_json(node, ("ordered",))
    items = "\n".join(_render_node(child) for child in node.children)
    return (
        f"<!-- wp:list{attrs} -->\n"
        f'<{tag} class="wp-block-list">\n{items}\n</{tag}>\n'
        f"<!-- /wp:list -->"
    )


def _render_list_item(node: BlockNodeV1) -> str:
    return f"<!-- wp:list-item -->\n<li>{_esc(node.content)}</li>\n<!-- /wp:list-item -->"


def _render_quote(node: BlockNodeV1) -> str:
    children_html = "\n".join(_render_node(child) for child in node.children)
    return (
        f"<!-- wp:quote -->\n"
        f'<blockquote class="wp-block-quote">\n{children_html}\n</blockquote>\n'
        f"<!-- /wp:quote -->"
    )


def _render_image(node: BlockNodeV1) -> str:
    src = str(node.attrs.get("src", ""))
    alt = str(node.attrs.get("alt", ""))
    attrs = _attrs_json(node, ("id", "sizeSlug"))
    return (
        f"<!-- wp:image{attrs} -->\n"
        f'<figure class="wp-block-image"><img src="{_esc(src)}" alt="{_esc(alt)}"/></figure>\n'
        f"<!-- /wp:image -->"
    )


def _render_code(node: BlockNodeV1) -> str:
    return (
        f"<!-- wp:code -->\n"
        f'<pre class="wp-block-code"><code>{_esc(node.content)}</code></pre>\n'
        f"<!-- /wp:code -->"
    )


def _render_separator(node: BlockNodeV1) -> str:
    return (
        "<!-- wp:separator -->\n"
        '<hr class="wp-block-separator has-alpha-channel-opacity"/>\n'
        "<!-- /wp:separator -->"
    )


_RENDERERS = {
    "paragraph": _render_paragraph,
    "heading": _render_heading,
    "list": _render_list,
    "list_item": _render_list_item,
    "quote": _render_quote,
    "image": _render_image,
    "code": _render_code,
    "separator": _render_separator,
}


# ---------------------------------------------------------------------------
# Parse: HTML → AST
# ---------------------------------------------------------------------------
#
# Phase 1 kickoff scope: build() only. parse() 真實需求在 plan §1.1b 的 192 篇
# WP 文章 round-trip migration（Week 2），需要完整 inline-content extractor +
# 交錯 / 巢狀 edge case handling。屆時一併帶 fixture-based tests 上線。
# 現在 stub 保留 API signature，避免將來改 import path 時破壞呼叫端。


def parse(raw_html: str) -> list[BlockNodeV1]:
    """Parse Gutenberg block HTML back to AST.

    Not implemented in Phase 1 kickoff — needed for 192-post migration in plan §1.1b.
    """
    raise NotImplementedError(
        "gutenberg_builder.parse() is Week 2 scope (plan §1.1b migration round-trip). "
        "Use build() for Phase 1 compose path."
    )
