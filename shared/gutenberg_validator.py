"""Gutenberg block HTML validator (ADR-005a §4).

Schema 層（`GutenbergHTMLV1._ast_and_html_consistent`）已驗 `build(ast) == raw_html`；
本模組補上「只給 HTML 字串」的守門路徑：
  - Usopp `validated` state 在 publish 前對 WP payload 再掃一次（defense in depth）
  - Week 2 migration 吃進 192 篇 WP 既有文章時，先過 validator 才進 builder.parse

4 項 Phase 1 檢查（ADR-005a §4）：
  1. Block type 白名單（BlockNodeV1 Literal 衍生）
  2. Comment parity + 不交錯（stack-based）
  3. Attr JSON 合法
  4. `<p>` 乾淨度（不含 block-level 標籤 / 不巢 wp: 區塊註解）

Deferred to Week 2（gutenberg_builder.parse() 落地後）：
  5. Round-trip check：`build(parse(html)).raw_html == html`
  6. 從 HTML parse 出 AST 深度（目前僅在 schema 層對 AST 物件驗證）
"""

from __future__ import annotations

import json
import re
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field

from shared.schemas.publishing import BlockNodeV1

VERSION = "gutenberg_validator_0.1.0"

# 從 schema Literal 衍生 → 自動同步白名單升級，不需手動維護兩份清單。
# builder 輸出的 comment 名稱用 dash（`wp:list-item`），AST Literal 用 underscore（`list_item`）。
_AST_BLOCK_TYPES: tuple[str, ...] = get_args(BlockNodeV1.model_fields["block_type"].annotation)
ALLOWED_COMMENT_BLOCK_TYPES: frozenset[str] = frozenset(
    t.replace("_", "-") for t in _AST_BLOCK_TYPES
)

# `<p>` 內不可出現的 block-level HTML 標籤；命中 = 段落結構被破壞。
_BLOCK_LEVEL_TAGS: frozenset[str] = frozenset(
    {
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "div",
        "section",
        "article",
        "aside",
        "ul",
        "ol",
        "li",
        "blockquote",
        "figure",
        "hr",
        "pre",
        "table",
    }
)


ErrorCode = Literal[
    "comment_unknown_type",
    "comment_unpaired",
    "comment_crossed",
    "attr_json_invalid",
    "paragraph_contains_block_tag",
    "paragraph_contains_block_comment",
]


class GutenbergValidationError(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    code: ErrorCode
    message: str
    locator: str | None = None


class ValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    valid: bool
    errors: list[GutenbergValidationError] = Field(default_factory=list)
    validator_version: str = VERSION


# ---------------------------------------------------------------------------
# Comment parsing
# ---------------------------------------------------------------------------
#
# 兩段式：外層抓出任一 HTML comment（`<!-- ... -->`），內層識別 `(/?)wp:name [json]`。
# 好處：JSON 層用 json.loads 的 balanced brace 解析能力，避免 regex 對巢狀 `{}` 的坑
# （若用 `\{.*?\}` 非貪婪，巢狀 JSON 會 truncate 成 invalid）。

_COMMENT_OUTER_RE = re.compile(r"<!--\s*(.*?)\s*-->", re.DOTALL)
_COMMENT_BODY_RE = re.compile(
    r"^(?P<close>/)?wp:(?P<name>[a-z][a-z0-9-]*)(?:\s+(?P<json>.+))?$", re.DOTALL
)


def validate(html: str) -> ValidationResult:
    """Validate Gutenberg block HTML. Non-raising — errors in result list."""
    errors: list[GutenbergValidationError] = []
    errors.extend(_check_comments(html))
    errors.extend(_check_paragraph_cleanliness(html))
    return ValidationResult(valid=not errors, errors=errors)


def _check_comments(html: str) -> list[GutenbergValidationError]:
    errors: list[GutenbergValidationError] = []
    stack: list[str] = []

    for m in _COMMENT_OUTER_RE.finditer(html):
        inner = m.group(1).strip()
        pos = m.start()
        body_m = _COMMENT_BODY_RE.match(inner)
        if not body_m:
            continue  # 非 wp: 開頭的一般 HTML comment，validator 不管

        is_close = bool(body_m.group("close"))
        name = body_m.group("name")
        raw_json = body_m.group("json")

        if name not in ALLOWED_COMMENT_BLOCK_TYPES:
            errors.append(
                GutenbergValidationError(
                    code="comment_unknown_type",
                    message=(
                        f"unknown block_type {name!r}; whitelist="
                        f"{sorted(ALLOWED_COMMENT_BLOCK_TYPES)}"
                    ),
                    locator=_excerpt(html, pos),
                )
            )
            # 未知 type 不進 stack，保持 pairing 檢查對合法 block 的語意
            continue

        if not is_close and raw_json:
            try:
                json.loads(raw_json)
            except json.JSONDecodeError as e:
                errors.append(
                    GutenbergValidationError(
                        code="attr_json_invalid",
                        message=f"{name}: {e.msg} (col {e.colno})",
                        locator=_excerpt(html, pos),
                    )
                )

        if is_close:
            if not stack:
                errors.append(
                    GutenbergValidationError(
                        code="comment_unpaired",
                        message=f"close /wp:{name} without matching open",
                        locator=_excerpt(html, pos),
                    )
                )
            elif stack[-1] == name:
                stack.pop()
            elif name in stack:
                # Crossed / mis-nested: target 在 stack 較深處但頂端是別的 block。
                # 不 pop — 保留 stack 讓後續 close 仍能配對正確目標。
                # 反例：old code 對 <b><i>…</b></i> 的 close-b 會誤 pop 掉 i，導致後續
                # close-i 又報一次 crossed；find-and-preserve 只報一次且訊息更精確。
                expected = stack[-1]
                errors.append(
                    GutenbergValidationError(
                        code="comment_crossed",
                        message=(
                            f"close /wp:{name} while /wp:{expected} expected "
                            "(blocks crossed / mis-nested)"
                        ),
                        locator=_excerpt(html, pos),
                    )
                )
            else:
                # name 根本不在 stack 裡 → unpaired close（不是 crossed）
                errors.append(
                    GutenbergValidationError(
                        code="comment_unpaired",
                        message=f"close /wp:{name} without matching open",
                        locator=_excerpt(html, pos),
                    )
                )
        else:
            stack.append(name)

    for name in stack:
        errors.append(
            GutenbergValidationError(
                code="comment_unpaired",
                message=f"open wp:{name} without matching close",
                locator=None,
            )
        )

    return errors


# ---------------------------------------------------------------------------
# Paragraph cleanliness
# ---------------------------------------------------------------------------

_PARAGRAPH_RE = re.compile(r"<p(?:\s[^>]*)?>(?P<body>.*?)</p>", re.DOTALL | re.IGNORECASE)
_BLOCK_TAG_RE = re.compile(
    r"<(?P<tag>" + "|".join(sorted(_BLOCK_LEVEL_TAGS)) + r")(?:\s|>|/)",
    re.IGNORECASE,
)
_BLOCK_COMMENT_RE = re.compile(r"<!--\s*/?wp:", re.IGNORECASE)


def _check_paragraph_cleanliness(html: str) -> list[GutenbergValidationError]:
    errors: list[GutenbergValidationError] = []
    for m in _PARAGRAPH_RE.finditer(html):
        body = m.group("body")
        pos = m.start()

        tag_m = _BLOCK_TAG_RE.search(body)
        if tag_m:
            errors.append(
                GutenbergValidationError(
                    code="paragraph_contains_block_tag",
                    message=f"<p> contains block-level <{tag_m.group('tag').lower()}>",
                    locator=_excerpt(html, pos),
                )
            )

        if _BLOCK_COMMENT_RE.search(body):
            errors.append(
                GutenbergValidationError(
                    code="paragraph_contains_block_comment",
                    message="<p> contains a wp: block comment (blocks must not nest in paragraphs)",
                    locator=_excerpt(html, pos),
                )
            )
    return errors


def _excerpt(html: str, pos: int, span: int = 60) -> str:
    start = max(0, pos - 10)
    end = min(len(html), pos + span)
    snippet = html[start:end].replace("\n", "\\n")
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(html) else ""
    return f"{prefix}{snippet}{suffix}"
