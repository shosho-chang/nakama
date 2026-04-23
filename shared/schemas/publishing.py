"""Brook → approval_queue → Usopp publishing contracts.

Source: ADR-005a §2 (Brook draft schema), ADR-005b §9/§10 (Usopp publish schema).

Schema 定義順序（依相依性，Python 直譯器由上至下載入）：
    BlockNodeV1 → GutenbergHTMLV1 → FeaturedImageBriefV1 → DraftComplianceV1 → DraftV1
    PublishComplianceGateV1 → PublishRequestV1 → PublishResultV1

所有 schema 遵守 docs/principles/schemas.md：
- extra="forbid" + frozen=True
- 持久化 schema 必須有 schema_version
- AwareDatetime 取代 str timestamps
- Literal 取代 str enums
"""

from __future__ import annotations

from typing import Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    constr,
    model_validator,
)

# AST 遞迴深度上限，防 LLM 產生極深巢狀造成 RecursionError / DoS
MAX_AST_DEPTH = 6


# ---------------------------------------------------------------------------
# Gutenberg AST（ADR-005a §2）
# ---------------------------------------------------------------------------


# 每種 block_type 允許的 children block_type；空集合 = 不得有 children（leaf）。
# 擋 LLM / 手動構建亂塞（例 `list` 塞 paragraph 進來、paragraph 有 children），
# ADR-004 review borderline #3 的 follow-up。升 block_type Literal 時同步調整此表。
_ALLOWED_CHILDREN: dict[str, frozenset[str]] = {
    "paragraph": frozenset(),
    "heading": frozenset(),
    "list": frozenset({"list_item"}),
    "list_item": frozenset(),
    # quote 可含段落、清單或巢狀 quote（WP blockquote 合法用法）
    "quote": frozenset({"paragraph", "list", "quote"}),
    "image": frozenset(),
    "code": frozenset(),
    "separator": frozenset(),
}


class BlockNodeV1(BaseModel):
    """Gutenberg AST 單一 block 節點。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    # Phase 1 白名單；Phase 2 有明確需求時升 V2 並同步調整 builder/validator + _ALLOWED_CHILDREN
    block_type: Literal[
        "paragraph", "heading", "list", "list_item", "quote", "image", "code", "separator"
    ]
    attrs: dict[str, str | int | bool] = Field(default_factory=dict)
    content: str | None = None
    children: list["BlockNodeV1"] = Field(default_factory=list)

    @model_validator(mode="after")
    def _children_match_block_type(self) -> "BlockNodeV1":
        allowed = _ALLOWED_CHILDREN[self.block_type]
        for child in self.children:
            if child.block_type not in allowed:
                raise ValueError(
                    f"{self.block_type!r} block cannot contain {child.block_type!r} child; "
                    f"allowed={sorted(allowed) if allowed else 'none (leaf block)'}"
                )
        return self

    @model_validator(mode="after")
    def _content_xor_children(self) -> "BlockNodeV1":
        # AST shape invariant：leaf block 用 content，container block 用 children，
        # 同時出現兩者代表 LLM / 手動構建亂塞（renderer 會 silently 忽略其中一個）。
        if self.content is not None and self.children:
            raise ValueError(
                f"{self.block_type!r} block cannot have both 'content' and 'children'; "
                "leaf blocks use content, container blocks use children "
                "(or neither for separator/image)"
            )
        return self


def _ast_depth(nodes: list[BlockNodeV1]) -> int:
    """Iterative max-depth walk — 顯式 stack 追深度，不靠 Python recursion limit。

    避免 pathological 輸入（>1000 層巢狀）打爆 CPython default recursion limit
    造成 RecursionError / DoS（recursion version 走訪一次就得 pop frame 一次）。
    """
    if not nodes:
        return 0
    max_depth = 0
    stack: list[tuple[BlockNodeV1, int]] = [(n, 1) for n in nodes]
    while stack:
        node, depth = stack.pop()
        if depth > max_depth:
            max_depth = depth
        stack.extend((child, depth + 1) for child in node.children)
    return max_depth


class GutenbergHTMLV1(BaseModel):
    """AST + serialized HTML 配對；AST 是 source of truth。

    `raw_html` 必須等於 `gutenberg_builder.build(ast)` 的輸出。
    任何手動構建若兩者不一致，schema 層即 fail fast。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    ast: list[BlockNodeV1]
    raw_html: str
    validator_version: str  # e.g. "gutenberg_builder_0.1.0"

    @model_validator(mode="after")
    def _ast_depth_within_limit(self) -> "GutenbergHTMLV1":
        depth = _ast_depth(self.ast)
        if depth > MAX_AST_DEPTH:
            raise ValueError(f"AST depth {depth} 超過上限 {MAX_AST_DEPTH}（防遞迴 DoS）")
        return self

    @model_validator(mode="after")
    def _ast_and_html_consistent(self) -> "GutenbergHTMLV1":
        # 動態 import 避免 schema 模組循環依賴（gutenberg_builder 反向 import 本檔的 BlockNodeV1）
        from shared import gutenberg_builder

        expected = gutenberg_builder.build(self.ast)
        if expected.raw_html != self.raw_html:
            raise ValueError("raw_html 與 build(ast) 結果不一致，ast 是 source of truth")
        return self


# ---------------------------------------------------------------------------
# Draft 附屬 schema（ADR-005a §2）
# ---------------------------------------------------------------------------


class FeaturedImageBriefV1(BaseModel):
    """Brook 建議的主圖指示，featured_media_id 在 Bridge approve 時由人工填入。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    purpose: Literal["hero", "inline", "social"]
    description: constr(min_length=10, max_length=500)
    style: str
    keywords: list[str]


class DraftComplianceV1(BaseModel):
    """Brook compose 階段的合規狀態快照（regex scan + LLM self-check 共同填寫）。

    與 PublishComplianceGateV1（本檔 §Usopp）不同：
    - DraftComplianceV1（本 schema）：compose 期 snapshot，
      描述「Brook 寫時有沒有避開療效、有沒有加免責」
    - PublishComplianceGateV1：publish gate scan，
      Brook enqueue + Usopp claim 各掃一次（defense in depth）

    `detected_blacklist_hits` 非空時 Bridge HITL 應顯示警告。非例外清單、非豁免清單。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    claims_no_therapeutic_effect: bool
    has_disclaimer: bool
    detected_blacklist_hits: list[str] = Field(
        default_factory=list,
        description="compose 時 regex scan 命中的黑名單詞彙",
    )


# 既有 category / tag slug 格式：小寫英數、連字號、CJK
_SLUG_PATTERN = r"^[a-z0-9一-鿿][a-z0-9\-一-鿿]*$"


class DraftV1(BaseModel):
    """Brook → approval_queue → Usopp 的核心 contract（ADR-005a §2）。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    draft_id: constr(pattern=r"^draft_\d{8}T\d{6}_[0-9a-f]{6}$")
    created_at: AwareDatetime
    agent: Literal["brook"]
    operation_id: constr(pattern=r"^op_[0-9a-f]{8}$")

    # 內容
    title: constr(min_length=5, max_length=120)
    slug_candidates: list[constr(pattern=r"^[a-z0-9-]{3,80}$")] = Field(min_length=1, max_length=3)
    content: GutenbergHTMLV1
    excerpt: constr(min_length=20, max_length=300)

    # 分類
    primary_category: Literal[
        "blog",
        "podcast",
        "book-review",
        "people",
        "neuroscience",
        "sport-science",
        "nutrition-science",
        "weight-loss-science",
        "sleep-science",
        "emotion-science",
        "longevity-science",
        "preventive-healthcare",
        "productivity-science",
    ]
    secondary_categories: list[constr(pattern=_SLUG_PATTERN, max_length=50)] = Field(
        default_factory=list, max_length=2
    )
    # tags 僅驗 slug 格式；既有 497 tag 白名單比對在 compose 層做
    tags: list[constr(pattern=_SLUG_PATTERN, max_length=50)] = Field(
        default_factory=list, max_length=10
    )

    # SEO
    focus_keyword: constr(min_length=2, max_length=60)
    meta_description: constr(min_length=50, max_length=155)

    # 圖片（Phase 1 人工，featured_media_id 在 Bridge approve 時填）
    featured_image_brief: FeaturedImageBriefV1 | None = None

    # Compliance（compose 階段 snapshot）
    compliance: DraftComplianceV1

    # Style profile 來源（版本化，例 "book-review@0.1.0"）
    style_profile_id: constr(pattern=r"^[a-z0-9-]+@\d+\.\d+\.\d+$")

    @model_validator(mode="after")
    def _tags_and_secondary_unique(self) -> "DraftV1":
        if len(self.tags) != len(set(self.tags)):
            raise ValueError("tags 不可重複")
        if len(self.secondary_categories) != len(set(self.secondary_categories)):
            raise ValueError("secondary_categories 不可重複")
        return self


# ---------------------------------------------------------------------------
# Usopp publish schemas（ADR-005b §9 / §10）
# ---------------------------------------------------------------------------


class PublishComplianceGateV1(BaseModel):
    """Publish 前終端 compliance scan 結果（ADR-005b §10）。

    Brook 入隊時先跑一次、Usopp claim 後 publish 前再跑一次（defense in depth）。
    兩次結果不一致視為 fail。

    任一 bool flag 為 True 時：
    - Bridge HITL UI 隱藏一般 approve，改顯示加強 HITL 兩步驟確認
    - Usopp claim 後若 `ApprovalPayload.reviewer_compliance_ack != True`，
      立即 fail 回 approval queue
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    medical_claim: bool = False  # 療效 / 診斷 / 藥物類比詞彙命中
    absolute_assertion: bool = False  # 絕對斷言命中
    matched_terms: list[str] = Field(default_factory=list)


class PublishRequestV1(BaseModel):
    """approval_queue 清算後傳給 Usopp publisher 的執行指令。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    draft: DraftV1
    action: Literal["publish", "schedule", "draft_only"]
    scheduled_at: AwareDatetime | None = None  # 僅 action=schedule
    featured_media_id: int | None = None  # Bridge approve 時指定
    reviewer: str  # 修修 Slack user ID


class PublishResultV1(BaseModel):
    """Usopp publish 執行結果。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    status: Literal["published", "scheduled", "draft_only", "already_published", "failed"]
    post_id: int | None = None
    permalink: str | None = None
    seo_status: Literal["written", "fallback_meta", "skipped"]
    cache_purged: bool
    failure_reason: str | None = None
    operation_id: constr(pattern=r"^op_[0-9a-f]{8}$")
    completed_at: AwareDatetime
