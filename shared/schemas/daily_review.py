"""每日回顧（Daily Review）輸出 schema — N522 → N523 的契約.

Centaur Zettelkasten 規格 v0.2 §5（每日回顧規格）+ Prompt 規格 v0.1 P-1 / P-2。
``agents.robin.daily_review.run_daily_review`` 產出 :class:`DailyReviewBundle`，
N523 的 Web UI 純消費此 JSON——schema 穩定，欄位變動須 bump ``schema_version``。

schema_version 演進：
- v1（N522/N523）：candidates / fleeting / sweep 三區 + typed-edge chips。
- v2（N527 卡片畫布）：``CandidateCard`` 加 ``related_pool``（中圈 FTS pool）/
  ``related_mocs``（外圈 P-2 擴判 MOC）。**向後相容**：兩欄預設空 list，舊 bundle
  （無此欄）可被 v2 schema 直接讀回；N523 線性 UI 只讀既有欄位，不受影響。
  N528 追加 ``TypedEdgeChip.status``（高關聯卡 status badge）——additive-optional 標量、
  預設空字串，舊 bundle 可直接讀回，故不 bump schema_version（非 enum 擴充、非結構欄）。

設計守則（與 N520/N521 一致的 closed-set 協定）：
- 純 pydantic value-object，無 I/O、無 LLM、無 vault 依賴；可在測試中自由 construct。
- 每個 ``Literal`` enum 為 ``schema_version=1`` 凍結；新增成員須 (a) bump
  ``schema_version`` (b) 更新本 docstring (c) 同步 N523 consumer。靜默擴充禁止。
- **internal_rationale 不入此 schema**：P-2 的 typed-edge 判斷理由是 debug-only，
  規格 v0.1 P-2 規則 4 明訂「不展示給人——理由欄留白給 Shosho」。故 chip 只帶
  方向 + 目標卡，不帶 LLM 理由。``why`` 欄是 P-1 的觸發訊號（展示用，引使用者
  自己的 note 原話），與 P-2 的 internal_rationale 是兩回事。

三個區塊（對映規格 v0.2 §5 掃描範圍 ①②③）：
- ``candidates``  ← ① KB/Annotations 昨日 delta，P-1 篩選 + 建議卡名
- ``fleeting``    ← ② KB/Fleeting status:open
- ``sweep``       ← ③ 每週清掃日才填（stale seedling / 孤兒卡 / 過期歸檔）
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# typed-edge 方向。P-2 三類 + 「reverse」標記（既有卡 → 候選卡才成立時）。
EdgeType = Literal["support", "refute", "extend"]
EdgeDirection = Literal["forward", "reverse"]

# 清掃項類型。
SweepKind = Literal["stale_seedling", "orphan_card", "expired_defer"]


class SourceRef(BaseModel):
    """候選卡的溯源錨點——一條 annotation/highlight/reflection 的定位資訊。

    ``anchor`` 是渲染後的穩定錨（``^cfi-...`` / ``^p-N`` / ``t=...``）；UI 用
    ``literature_path`` + ``anchor`` 組回 Literature/Reader 的深連結。``quote`` /
    ``note`` 原文照錄（P-1 規則：不改字），供卡片預填與「為什麼是這條」核對。
    """

    model_config = ConfigDict(extra="forbid")

    anchor: str  # ^cfi-6-26-106 / ^p-3 / t=750
    literature_path: str  # KB/Literature/{slug}
    quote: str = ""  # 原文引文（不改字）
    note: str = ""  # 使用者 note（不改字；純 highlight 為空）


class TypedEdgeChip(BaseModel):
    """一個 typed-edge 建議 chip（P-2 輸出）——分方向，理由留白給人。

    紅線（規格 v0.1 §1 鐵律 3 + P-2 規則 4）：AI 提供的是「建議 chips」，理由欄
    是人的工作。故本 schema **不帶** rationale——UI 渲染 chip（方向 + 目標卡名），
    人採用後自己在永久卡 body 寫 ``支持:: [[...]] — 理由``。

    ``status`` 是目標既有卡的 frontmatter status（seedling / evergreen / …）；無則空字串。
    N528 高關聯卡比照中圈在卡面顯示 status badge。
    """

    model_config = ConfigDict(extra="forbid")

    edge_type: EdgeType
    direction: EdgeDirection = "forward"
    target_card: str  # 既有卡的 KB path，e.g. KB/Permanent/好系統讓你不需要意志力
    target_title: str = ""  # 顯示名（path stem fallback）
    status: str = ""  # 目標既有卡 frontmatter status；未知為空


class RelatedCard(BaseModel):
    """卡片畫布「中圈」一張卡——FTS5/BM25 字面命中、**未經 P-2 判斷**（卡片畫布規格 v1 C3）。

    強度＝既有訊號分層（C3：零新 LLM 成本、零分數欄位）。高圈＝:class:`TypedEdgeChip`
    （P-2 判定有真關係）；中圈即本 model——只帶 ``bm25_rank`` 供前端排序，**不是** 0–1
    相關分數。N528 UI 把這層渲染成「中關聯 · 字面相關」直欄帶。

    ``status`` 是既有卡的 frontmatter status（seedling / evergreen / …）；無則空字串。
    ``bm25_rank`` 0-based（0 = 該候選 FTS5 命中最前），與高圈互斥（已進該候選
    typed-edge 的卡會在 build 時排除，避免高/中圈重複）。
    """

    model_config = ConfigDict(extra="forbid")

    card_path: str  # 既有卡 KB path，e.g. KB/Permanent/好系統讓你不需要意志力
    title: str = ""  # 顯示名（path stem fallback）
    status: str = ""  # 既有卡 frontmatter status；未知為空
    bm25_rank: int = 0  # FTS5/BM25 命中序位（0 = 最前）；排序用，非相關分數


class RelatedMoc(BaseModel):
    """卡片畫布「外圈」一個 MOC 疊卡——P-2 擴判與候選相關（卡片畫布規格 v1 C8 / C13）。

    C8：MOC 疊卡只列 Robin 判定相關的（✦ 徽章），其餘收進「MOC 盒」——故本 list 是
    P-2 擴判過濾後的子集（上限 2，寧缺勿濫），不是全量 MOC。疊卡成員內容由前端
    lazy load（``moc_members`` API），不入此 bundle（規模策略 C12：永不全攤）。

    ``card_count`` 是該 MOC 已歸位的成員卡數（marker section 外的人寫分組連結數），
    供 UI 標示疊卡厚度。
    """

    model_config = ConfigDict(extra="forbid")

    moc_path: str  # KB/MOCs/{topic}
    name: str = ""  # MOC 顯示名（frontmatter title / H1 / stem fallback）
    card_count: int = 0  # 已歸位成員卡數（疊卡厚度指示）


class CandidateCard(BaseModel):
    """一條永久卡候選（P-1 輸出 + P-2 chips + 卡片畫布三層相關資料）。

    ``suggested_title`` 是 P-1 給的宣告句（主張，非主題）；``why`` 引觸發訊號；
    ``source_refs`` 是錨點清單（多條 annotation 合併為一條候選時列全部）。
    ``edges`` 按方向分組，每組上限 3（P-2 規則：寧缺勿濫）。``priority`` 由 P-1
    排序決定（0 = 置頂），UI 照序渲染。

    卡片畫布三層相關資料（規格 v1 §3，schema_version 2 新增；強度＝分層 C3）：
    - ``edges``（高圈）— P-2 判定有真 typed-edge 關係，既有欄位不變。
    - ``related_pool``（中圈）— FTS5/BM25 命中、未經 P-2，排除已進 ``edges`` 的卡。
    - ``related_mocs``（外圈）— P-2 擴判與候選相關的 MOC（上限 2）。
    舊 bundle（無此二欄）以預設空 list 讀回，向後相容（N523 線性 UI 不受影響）。
    """

    model_config = ConfigDict(extra="forbid")

    candidate_id: str  # 穩定 id（slug + 首錨點 hash），UI dedup / action 用
    suggested_title: str  # 宣告句（可改）
    why: str  # 一句話，引觸發訊號
    source_refs: list[SourceRef] = Field(default_factory=list)
    edges: list[TypedEdgeChip] = Field(default_factory=list)
    related_pool: list[RelatedCard] = Field(default_factory=list)  # 中圈（FTS pool）
    related_mocs: list[RelatedMoc] = Field(default_factory=list)  # 外圈（P-2 擴判 MOC）
    priority: int = 0  # 0 = 置頂；P-1 排序序位
    strong_signal: bool = False  # 是否含強評價訊號（「必須重複三次」等）→ 置頂依據


class FleetingItem(BaseModel):
    """一條待處理的 Fleeting Note（規格 v0.2 §4，status:open）。

    AI 只讀不改字（§4 寫入權限：人 + Nami）；每日回顧把它端到使用者面前，使用者
    三選一（開卡 / 併入既有卡 / 丟掉）後 N523 才翻 status。
    """

    model_config = ConfigDict(extra="forbid")

    path: str  # KB/Fleeting/{timestamp}-{...}.md
    created: str  # ISO-ish，原檔 frontmatter created
    via: str = "slack"  # slack | mobile | obsidian
    text: str  # 原話一字不動


class SweepItem(BaseModel):
    """一條每週清掃項（規格 v0.2 §5 ③ / §2 每週清掃）。

    純程式碼偵測（孤兒/stale/過期），非 LLM。``reason`` 是機械說明（「放 42 天未升級」
    / 「無任何 in/out 連結」），UI 直接顯示。
    """

    model_config = ConfigDict(extra="forbid")

    kind: SweepKind
    path: str  # 受影響檔案的 KB path（過期 defer 為原候選錨點 literature_path）
    title: str = ""
    reason: str = ""
    age_days: int | None = None  # stale_seedling / expired_defer 帶天數


class DailyReviewBundle(BaseModel):
    """每日回顧一次 run 的完整輸出——N523 的消費契約。

    ``generated_at`` / ``review_date`` 給 UI 標頭；``weekly_sweep`` 旗標讓 UI 知道
    今天是否含清掃區（每週清掃日才 True，平日 ``sweep`` 為空且旗標 False）。
    ``warnings`` 收集非致命問題（某 slug parse 失敗、KB 檢索失敗等），讓 job 不
    中斷但留痕。
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[2] = 2
    generated_at: str  # ISO timestamp（job 執行時刻）
    review_date: str  # YYYY-MM-DD（被回顧的「昨日」之隔天，即今天）
    weekly_sweep: bool = False

    candidates: list[CandidateCard] = Field(default_factory=list)
    fleeting: list[FleetingItem] = Field(default_factory=list)
    sweep: list[SweepItem] = Field(default_factory=list)

    warnings: list[str] = Field(default_factory=list)
