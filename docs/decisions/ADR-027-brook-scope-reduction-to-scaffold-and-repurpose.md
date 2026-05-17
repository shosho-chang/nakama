# ADR-027: Brook Scope Reduction — Scaffold + Repurpose Only

**Date:** 2026-05-17
**Status:** Accepted
**Deciders:** shosho-chang, Claude Opus 4.7
**Supersedes:** Compose pipeline portion of [ADR-005a](ADR-005a-brook-gutenberg-pipeline.md)
**Amends:** [ADR-024](ADR-024-source-promotion-and-reading-context-package.md) (RCP ownership)
**Related:** ADR-001, ADR-012, ADR-014, ADR-021, `CONTENT-PIPELINE.md`

---

## Context

`CONTENT-PIPELINE.md:36` 自始凍結一條設計紅線：

> Line 2 心得是修修自己的聲音，不可被 LLM 取代。系統角色限於「整合素材到 KB（Stage 3）」+「提供寫作前素材 scaffold（Stage 4 assist）」+「Stage 5 之後的 channel 製作」。Stage 4 assist 可以呈現 annotation、source map、evidence board、questions、outline skeleton、KB links，但**不得產生完成句、段落或第一人稱正文**。

ADR-005a 與後續的 `compose_and_enqueue` production 路徑（PR #78, 2026-04-23）違反這條紅線：它以「topic → LLM 從零生成整篇 draft → approval_queue → WP」為架構目標。雖然 HITL 是 mitigation，但 LLM 從零生稿這個動作本身就跨線。對話式 Web UI（`/brook/chat`）也是同一條紅線問題的另一面 — 鼓勵 LLM 連續產出大段正文。

同時，Brook 已 over-loaded（`CONTENT-PIPELINE.md` 觀察 #2）：5 個子領域、13+ 模組，新對話 onboarding 困難，token context 逼近上限。

ADR-024（2026-05-09）建立了 Reading Context Package 概念但 ownership 留 open（「Robin may produce」非強制），同時對 Brook 寫了負面規則「Brook must not bypass the Reading Context Package boundary to compose Line 2 atomic content」。RCP 與 Brook synthesize（ADR-021）功能上是 sibling — 都產 outline skeleton + evidence — 但歸屬分散。

2026-05-17 grill session 收斂出新的 Brook 範圍與紅線陳述。

## Decision

### 1. 收斂後的 Brook 紅線

**Stage 4 atomic content 必須先存在且由人類產生。** Brook 只能在兩個位置動作：

- **Pre-atomic（Stage 3→4 邊界）**：作為 analyst / research assistant，產出 scaffold（outline skeleton、evidence pool、annotation digest、idea clusters、questions、cross-references）給修修寫稿時參考。不可寫完成句、段落、第一人稱正文。
- **Post-atomic（Stage 5）**：作為 repurpose engine，吃 human-authored atomic content（手寫心得 / Line 3 加工稿 / 訪談逐字稿），改寫成不同 channel 格式。可在 closed-pool 補充材料下深化內容，但**不可創造原 atomic 不存在的觀點**。

### 2. 兩條合法 sub-pipeline

| Sub-pipeline | 對應 stage | Input | Output |
|---|---|---|---|
| **Scaffold** | Stage 3→4 | (a) 一個 Reading Source slug（→ RCP mode）<br>(b) topic + Zoro keywords + trending angles（→ synthesize mode）<br>(c) 兩者皆有（Line 1b mixed mode） | 寫作素材包：outline skeleton + evidence + （RCP mode 含）annotation digest / questions / idea clusters |
| **Repurpose** | Stage 5 | atomic content（必要）+ 可選 closed-pool 補充材料 | blog / FB tonals × 4 / IG cards / video script |

Repurpose 進一步分兩個 sub-mode：

- **2a 純改格式**：input 純 atomic（Line 2 手寫心得 / Line 3 加工稿 / Line 1a 純逐字稿），改 channel format，不引入新素材
- **2b 訪談 + 研究包合成**：input = 訪談逐字稿（atomic）+ 修修策展的封閉素材池（research_pack），用修修語氣寫長 form blog，可參考 closed-pool 補洞但不可動用訓練資料

### 3. RCP ownership 改歸 Brook（amend ADR-024）

ADR-024 對 RCP producer 採開放語氣（「Robin may produce」）。本 ADR 寫死：

- **RCP producer = Brook**（與 synthesize 一致 — 兩者都是 Stage 3→4 scaffold，統一 owner 才能共用 evidence-pool 層、避免重複實作）
- **Robin** 仍 own Source Promotion domain logic（source quality / concept extraction / Promotion Manifest / KB commit），與「向外吸收」（ADR-012）對齊
- **Brook** own 所有 Stage 3→4 scaffold（RCP + synthesize + 未來的 idea clusters / evidence board）

### 4. Scaffold 子架構：統一 evidence 層 + 分開 outline drafter

```
agents/brook/scaffold/
├── _evidence.py        # 統一證據蒐集（依 input 走不同 retrieval）
├── _rcp_outline.py     # RCP mode drafter（annotation-aware）
├── _synth_outline.py   # synthesize mode drafter（topic + trending-aware）
└── __init__.py         # 統一 entry: scaffold(input) → 自動分流
```

Obsidian Project 頁面一個 `Brook: Scaffold` button，按 frontmatter 欄位自動分流：

- 有 `reading_source: [slug]` → RCP mode
- 有 `topic + keywords` → synthesize mode
- 兩者皆有（Line 1b）→ 兩個 drafter 都跑，合進同一份 output

EvidencePoolItem schema 統一，annotation-weight 欄位 optional 加在 RCP-specific 路徑。

### 5. Zoro trending angles 進 synthesize

`prompts/brook/synthesize_outline.md` 加 optional `trending_angles` 區塊。LLM 行為：

- 若某 angle 與 evidence pool 有強對應，用為 section heading
- 若無對應，忽略；不可為配 angle 編造 evidence
- `OutlineSection` schema 加 optional `trending_match: list[str] = []`
- Synthesize store 加 `unmatched_trending_angles: list[str]` 警示欄位（給修修看 → Robin discovery 反向訊號，補 ADR-001 觀察 #1 的 SEO→Zoro feedback loop）

### 6. Repurpose 2b 架構（LLM 重活在 Stage 1 extractor）

新增 `agents/brook/line1b_extractor.py`，跑單次 LLM call：

- Input: SRT transcript + research_pack KB chunks + 修修 style profile
- Output: typed `Line1bStage1Result`，含 `narrative_segments` / `quotes` / `titles` / `book_context` / `cross_refs` / `brief`
- 三個 ChannelRenderer（blog / fb / ig）共享同一份 brief，確保多 channel 講同一件事的不同 length / tone

**Closed-pool 三層 enforcement**：

| 層 | 機制 |
|---|---|
| Layer 1 物理隔離 | `shared/repurpose/closed_pool.py` 包 KB retrieval，強制 `WHERE slug IN (research_pack ∪ {transcript_slug})` |
| Layer 2 prompt 紅線 | system prompt 寫死「your knowledge is restricted to these N materials; if a point isn't covered, omit it; do not fill from training data」|
| Layer 3 citation 強制 | 每段 output 結尾必須 `[source: slug]` 或 `[transcript@HH:MM]`；無 citation 句子 post-process 標 ⚠️ 給修修審 |

`Line1bStage1Result` 採 typed pydantic（不沿用 ADR-014 untyped `data: dict`）— 1b 新合約應 fail loudly，避免 renderer 靜默退化成 Line 1a 行為使 closed-pool 白擋。ADR-014「untyped」原意是 cross-line schema 不該硬統一，single-line 內 typed 不違反精神。

### 7. 素材池來源：全走 Robin ingest 進 KB

訪前研究包（文章、作者的書）必須走 Robin ingest 進 `KB/Wiki/Sources/`。**不為 Project 開新 KB 資料夾**。

Project frontmatter 是 canonical white list：

```yaml
---
type: project
line: 1b  # 或 2 / 3
topic: "與 X 作者談 Y 主題"
keywords: [...]            # Zoro button 寫入
trending_angles: [...]     # Zoro button 寫入
research_pack:             # 修修策展，closed-pool 來源
  - "[[article-slug-1]]"
  - "[[book-author-x]]"
transcript: "[[interview-2026-05-20-author-x]]"
---
```

Source page 端 `mentioned_in` backlink 由 dataview / Robin ingest 自動維護，僅為 human navigation；Brook code 只讀 Project frontmatter `research_pack`。

## Considered Options

### Rejected: 維持 Entry Point A（chat UI）

Claude.ai 已提供更好的對話 UX。自建 Web UI + SQLite 對話儲存 + sliding window context 是重新發明輪子，且鼓勵 LLM 連續產正文（跨紅線）。

### Rejected: 維持 Entry Point B（compose_and_enqueue 自動生稿）

從零生稿跨紅線。HITL approval 是 mitigation 不是解方 — 修修每次都要重寫一遍才符合自己聲音，那不如自己寫。

### Rejected: RCP 留在 Robin（維持 ADR-024 原暗示方向）

RCP 與 synthesize 都是 Stage 3→4 scaffold，sibling 概念分散在兩個 agent 違反 ADR-012「向外/對內」框架。未來 outline drafter 共用層難以實作。

### Rejected: Stage 5 repurpose 2b 把 LLM 重活分散到三個 renderer（選項 B）

三個 channel 各自詮釋「修修語氣」會 drift；多 channel 一致性破壞；token 成本三倍；closed-pool 紅線要在三處守。集中在 Stage 1 extractor 一次想清楚 brief 是嚴格 better。

### Rejected: Research pack 留在 Project 頁面 attachment（不進 KB）

兩條 retrieval 機制（KB hybrid + inline file read）增加維護負擔；素材無法跨 Project reuse；closed-pool 物理隔離難實作。

## Consequences

### 砍除清單

| 項目 | 動作 |
|---|---|
| `agents/brook/compose.py` | 整檔砍 |
| SQLite `brook_conversations` / `brook_messages` 表 | drop migration |
| `/brook/chat` thousand_sunny route + HTML | 砍 |
| PR #78 `test_compose_pipeline` 17 tests | 砍 |
| `memory/claude/project_brook_design.md` Phase 2 五項 | 全砍 |
| `memory/claude/project_brook_compose_merged.md` | 標 superseded |
| `memory/claude/project_seo_solution_scope.md`「Brook compose 整合排行潛力草稿」 | 砍那條 |

### 保留清單

| 項目 | 理由 |
|---|---|
| `shared/schemas/publishing.py`（DraftV1 / GutenbergHTMLV1 / BlockNodeV1）| Slice 10 repurpose → Usopp 復用，schema agent-agnostic |
| `shared/gutenberg_builder.py` / `gutenberg_validator.py` | 工具層，repurpose blog renderer 可重用 |
| `agents/brook/compliance_scan.py` | repurpose 也要擋藥事法 |
| `agents/brook/style_profile_loader.py` | 1b extractor 用 |
| `agents/brook/tag_filter.py` | repurpose 也要 tag filter |
| `agents/brook/synthesize/` | 改名歸 `scaffold/synthesize`（或直接 in-place 整合） |
| `agents/brook/repurpose_engine.py` + renderers + `line1_extractor.py` | 已 ship |
| `agents/brook/audit_runner.py` / `seo_block.py` / `seo_narrow.py` | SEO punted；獨立檢討 |

### 新增清單

| 項目 | 屬 PR |
|---|---|
| `agents/brook/scaffold/` 目錄結構 + 統一 evidence 層 | PR-5 |
| `agents/brook/line1b_extractor.py` + typed `Line1bStage1Result` | PR-5 |
| `shared/repurpose/closed_pool.py` retrieval wrapper | PR-5 |
| `prompts/brook/line1b_extract.md`（closed-pool 紅線 prompt） | PR-5 |
| `synthesize_outline.md` 加 `trending_angles` 區塊 | PR-5 |
| `OutlineSection.trending_match` + `BrookSynthesizeStore.unmatched_trending_angles` | PR-5 |
| Obsidian `Brook: Scaffold` button（DataviewJS / thousand_sunny route TBD） | PR-6 |
| Project frontmatter schema doc | PR-6 |

### 遷移 PR 順序

1. **PR-1**（本 ADR）：純文件 — ADR-027 + amend ADR-005a + amend ADR-024 + 更新 `CONTENT-PIPELINE.md` Line 3 Stage 4 描述
2. **PR-2**：drain pending drafts — 列 `approval_queue WHERE source_agent='brook' AND status='pending'`，修修審 → approve / discard
3. **PR-3**：砍 chat UI + SQLite 表 + migration script
4. **PR-4**：砍 `compose.py` + 17 個 compose tests + 對應 memory 整理
5. **PR-5**：新增 scaffold 子架構 + 1b extractor + closed-pool wrapper + trending angles
6. **PR-6**：Obsidian Brook button + frontmatter schema

PR-5 / PR-6 可平行開。

### ADR-005a amendment

Status 改 `Partially Superseded by ADR-027`。Context 段補一行：「2026-05-17 ADR-027 砍除 compose pipeline；本 ADR 定義的 schema 與 builder / validator 工具層保留供 repurpose blog → Usopp handoff 復用。」

### ADR-024 amendment

「Consequences」段「Robin/shared owns Source Promotion domain logic」一行保留，但加註：「RCP producer ownership 由 ADR-027 明定歸 Brook。Robin 只負責 source 進 KB 與 Source Promotion，不負責 RCP。」

### CONTENT-PIPELINE.md update

Line 3 Stage 4 cell 從「⬜ kb-synthesize skill → LLM 輔助稿」改成「⬜ synthesize outline → 修修自寫（可用 Claude.ai 對話協助），LLM 不代寫正文」。Agents × Stages 矩陣 Brook 列 Stage 4 描述同步更新（移除「Line 3 compose（LLM 輔助稿）」字樣）。

### Open follow-ups（不在本 ADR 範圍）

- **Obsidian CLI integration research** — 獨立 research doc，可能影響 Brook button 實作方式
- **SEO 中控台 ownership** — ADR-012 已歸 Brook，本 ADR 不動；未來若 Brook 仍 over-loaded 可再拆
- **Slice 10 repurpose → Usopp WP** — 確認 schema 復用是否順暢
- **Line 2 / Line 3 atomic content 落地後的 2a extractor 設計** — 待修修開始實際產 atomic 後再凍結 schema

## References

- `CONTENT-PIPELINE.md:36` — 原始 Stage 4 紅線
- ADR-001 — Brook = Composer role（本 ADR 收斂 Composer 的實質範圍）
- ADR-005a — Brook Gutenberg Pipeline（compose 部分 superseded）
- ADR-012 — Zoro vs Brook 向外/對內邊界（本 ADR 強化此框架）
- ADR-014 — RepurposeEngine plug-in interface（本 ADR 在此架構內擴 1b）
- ADR-021 — Brook synthesize（本 ADR 將其納入 scaffold 子架構）
- ADR-024 — Source Promotion + RCP（本 ADR amend RCP ownership）
- `agents/brook/synthesize/` — 已 ship 的 synthesize 實作
- `agents/brook/repurpose_engine.py` — 已 ship 的 repurpose 實作
- `prompts/brook/synthesize_outline.md` — 現行 outline drafter prompt
