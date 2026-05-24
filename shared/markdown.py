"""Server-side markdown → safe HTML for Bridge vault-as-substrate surfaces.

Pipeline: markdown-it-py (CommonMark) + inline wikilink rule → HTML →
bleach allowlist sanitize. Wikilinks resolve via a caller-supplied callable
(`target -> url | None`); unresolved targets render as a `.wikilink-broken`
span so the UI can style them without producing a dead link.
"""

from __future__ import annotations

import re
from typing import Callable, Optional

import bleach
from markdown_it import MarkdownIt

WikilinkResolver = Callable[[str], Optional[str]]

_WIKILINK_RE = re.compile(r"\[\[([^\[\]\|\n]+?)(?:\|([^\[\]\n]+?))?\]\]")

_ALLOWED_TAGS = {
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "p",
    "br",
    "hr",
    "strong",
    "em",
    "s",
    "del",
    "code",
    "pre",
    "ul",
    "ol",
    "li",
    "blockquote",
    "a",
    "span",
    "table",
    "thead",
    "tbody",
    "tr",
    "th",
    "td",
}

_ALLOWED_ATTRS = {
    "a": ["href", "title", "class"],
    "span": ["class"],
    "code": ["class"],
    "th": ["align"],
    "td": ["align"],
}

_ALLOWED_PROTOCOLS = ["http", "https", "mailto"]


def _wikilink_plugin(md: MarkdownIt, resolver: Optional[WikilinkResolver]) -> None:
    def rule(state, silent: bool) -> bool:
        if state.src[state.pos] != "[" or state.src[state.pos : state.pos + 2] != "[[":
            return False
        m = _WIKILINK_RE.match(state.src, state.pos)
        if not m:
            return False
        target = m.group(1).strip()
        display = (m.group(2) or target).strip()

        if not silent:
            url = resolver(target) if resolver else None
            if url is not None:
                token = state.push("link_open", "a", 1)
                token.attrs["href"] = url
                token.attrs["class"] = "wikilink"
                text_token = state.push("text", "", 0)
                text_token.content = display
                state.push("link_close", "a", -1)
            else:
                open_token = state.push("span_open", "span", 1)
                open_token.attrs["class"] = "wikilink-broken"
                text_token = state.push("text", "", 0)
                text_token.content = display
                state.push("span_close", "span", -1)

        state.pos = m.end()
        return True

    md.inline.ruler.before("link", "wikilink", rule)


def render_markdown(
    text: str,
    *,
    wikilink_resolver: Optional[WikilinkResolver] = None,
) -> str:
    md = MarkdownIt("commonmark", {"html": True, "linkify": False, "typographer": False})
    md.enable("table")
    _wikilink_plugin(md, wikilink_resolver)

    raw_html = md.render(text)

    return bleach.clean(
        raw_html,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRS,
        protocols=_ALLOWED_PROTOCOLS,
        strip=True,
    )
