# ADR-027: Brook Scope Reduction — Scaffold + Repurpose + SEO

**Date:** 2026-05-17 (v1) / 2026-05-17 (v2 post-panel)
**Status:** Accepted
**Deciders:** shosho-chang, Claude Opus 4.7
**Supersedes:** Compose pipeline portion of [ADR-005a](ADR-005a-brook-gutenberg-pipeline.md)
**Amends:** [ADR-001](ADR-001-agent-role-assignments.md) (Brook role narrowed from Composer to Scaffold + Repurpose + SEO)
**Related:** ADR-012, ADR-014, ADR-017, ADR-021, ADR-024, `CONTENT-PIPELINE.md`

> **v2 audit trail (2026-05-17):** Multi-agent panel review (Claude + Codex GPT-5 + Gemini 2.5 Pro). Codex flagged factual drift (RCP already implemented in Robin; file paths; test counts; `DraftV1.agent` not agent-agnostic). Gemini rejected v1 entirely (transcript-as-atomic category error; desire path; closed-pool overclaim; bilingual blind spot). Owner adjudicated:
> - Transcript IS atomic (owner curates interview agenda and drives conversation — atomic = owner-framed material, not only owner-spoken words). Gemini's "transcript = guest's voice" reject overruled.
> - RCP ownership amendment **reverted** (owner adopted Codex/Gemini "don't move what's already implemented" pragmatism; Robin retains RCP).
> - Closed-pool 3-layer enforcement re-framed as **reminders not enforcement** (red line is owner self-discipline; system reminds, doesn't physically prevent; over-architecting an unenforceable rule is waste).
> - Entry A kept as **context bridge** (no local chat / no draft export; preserves KB+Project context packaging for Claude.ai handoff).
> - Bilingual cross-lingual consideration added as acknowledged constraint.
> - Frontmatter schema nested by source agent (Gemini extensibility point).
> - Factual fixes adopted (paths, counts, ADR-001 amendment, ADR-021 status).
>
> Audits preserved at `docs/research/2026-05-17-codex-adr-027-audit.md` and `docs/research/2026-05-17-gemini-adr-027-audit.md`.

---

## Context

`CONTENT-PIPELINE.md:36` 自始凍結一條紅線：

> Line 2 心得是修修自己的聲音，不可被 LLM 取代。系統角色限於「整合素材到 KB（Stage 3）」+「提供寫作前素材 scaffold（Stage 4 assist）」+「Stage 5 之後的 channel 製作」。Stage 4 assist 可以呈現 annotation、source map、evidence board、questions、outline skeleton、KB links，但**不得產生完成句、段落或第一人稱正文**。

ADR-005a 與 `compose_and_enqueue` production 路徑（PR #78）違反這條紅線 — 它以「topic → LLM 從零生稿 → approval_queue → WP」為架構。對話式 Web UI（`/brook/chat`）是同問題另一面。

Brook 同時 over-loaded（`CONTENT-PIPELINE.md` 觀察 #2）：5 個子領域、十多個模組，新對話 onboarding 困難。

ADR-001 將 Brook 定為 Composer。實作演化後實際範圍已偏離單純的 composer 角色，需要明確收斂。

**紅線本質**：屬於修修的自律邊界，不屬於系統可強制的 invariant。系統的角色是 reminder 與 default path 設計（讓對的路徑摩擦較小、錯的路徑需要顯式繞道），不是物理 enforcement。

## Decision

### 1. Brook 紅線陳述

**Stage 4 atomic content 必須先存在且由人類產生。** Atomic 定義為「修修策展、framing、curating 過的素材」，含但不限於：
- 修修手寫稿（Line 2/3）
- 修修主持的訪談 transcript（Line 1 — 訪綱由修修寫、對話由修修主導，故 transcript 體現修修策展）
- 修修策展的素材組合（封閉素材池）

不算 atomic 的：純他人原文（單篇文章 / 整本書 / 單一專家發言不經修修策展）。

Brook 動作位置：

- **Pre-atomic（Stage 3→4）**：analyst / research assistant，產 outline skeleton、evidence pool、idea clusters、questions、cross-references。**不寫完成句、段落、第一人稱正文。**
- **Post-atomic（Stage 5）**：repurpose engine，吃 human-curated atomic content，改 channel format。可在 closed-pool 補充材料下深化內容；**不可創造原 atomic 不存在的觀點**。

紅線守不守由修修自律。系統責任是：
- 設計上讓「不跨線」的路徑摩擦比「跨線」低
- 在跨線疑慮處顯示提醒信號（無 citation 段標 ⚠️、合理疑慮處 inline 警示）
- **不**用 hard fail / 物理隔離宣稱可防止有意繞線（無法）

### 2. Brook 實際範圍（amend ADR-001）

Brook 從「Composer」收斂為 **Scaffold + Repurpose + SEO Audit**（三個 sub-responsibility，非兩個）：

| Sub-responsibility | Stage | 模組 |
|---|---|---|
| **Scaffold (synthesize)** | 3→4 | `agents/brook/synthesize/`（既有） |
| **Repurpose** | 5 | `agents/brook/repurpose_engine.py` + renderers + extractors |
| **SEO Audit** | 7 | `agents/brook/audit_runner.py` / `seo_block.py` / `seo_narrow.py` |

**Brook 不 own**：
- RCP — **留在 Robin**（`agents/robin/reading_context_package.py` 已實作）。Brook 在 Stage 4 scaffold 流程中可 consume Robin 的 RCP 輸出，但不產生
- Source Promotion / KB ingest（Robin own，per ADR-024）

### 3. RCP ownership 維持 Robin（v1 amendment 取消）

ADR-024 RCP「Robin may produce」既有 implementation（`agents/robin/reading_context_package.py` + `shared/schemas/reading_context_package.py` + `tests/agents/test_reading_context_package.py`）。v1 提議移到 Brook 為 cosmetic restructure，但要 migrate code + tests + schema docstring + `CONTENT-PIPELINE.md` 多處。

採 Codex + Gemini push-back：**RCP producer 維持 Robin**。Robin 對 Source / annotation / KB 既有 ownership 已涵蓋 RCP 的所有 input，moving 至 Brook 反而增加 Brook 對 Robin 內部 schema 的耦合。

Brook scaffold（synthesize）與 Robin RCP 並列為**兩個 Stage 3→4 scaffold producer**：

| Producer | Owner | Input | 適用 line |
|---|---|---|---|
| **synthesize** | Brook | topic + Zoro keywords + trending angles + KB hybrid search | Line 3（topic-driven）|
| **RCP** | Robin | 單一 Reading Source overlay（annotation / notes / digest / source map）| Line 2（book-driven）|
| **混合**（Line 1b） | 兩者皆呼叫 | 訪談 + 訪前讀的書 → 同時跑 synthesize 與 RCP | Line 1b |

未來若兩者要共用 outline drafter 邏輯，抽到 `shared/scaffold/outline_drafter.py`（agent-agnostic library），不需動 ownership。

### 4. Synthesize 加 Zoro trending angles

`prompts/brook/synthesize_outline.md` 加 optional `trending_angles` 區塊：

- 若某 angle 與 evidence pool 強對應，用為 section heading
- 若無對應，忽略；不可為配 angle 編造 evidence
- `OutlineSection` schema 加 optional `trending_match: list[str] = []`
- `BrookSynthesizeStore` 加 `unmatched_trending_angles: list[str]` 警示欄位（給修修看 → 也是 Robin discovery 反向訊號）

### 5. Repurpose 2b 架構（LLM 重活在 Stage 1 extractor）

新增 `agents/brook/line1b_extractor.py`，跑單次 LLM call：

- Input: SRT transcript + closed-pool research_pack KB chunks + 修修 style profile
- Output: typed `Line1bStage1Result`，含 `narrative_segments` / `quotes` / `titles` / `book_context` / `cross_refs` / `brief`
- 三個 ChannelRenderer（blog / fb / ig）共享同一份 brief，確保多 channel 一致性

`Line1bStage1Result` 採 typed pydantic（不沿用 ADR-014 untyped `data: dict`）— 1b 新合約應 fail loudly。ADR-014「untyped」原意是 cross-line schema 不該硬統一，single-line 內 typed 不違反精神。

### 6. Closed-pool 三層 reminders（不是 enforcement）

| 層 | 機制 | 性質 |
|---|---|---|
| Layer 1 retrieval 限制 | `shared/repurpose/closed_pool.py` 包 KB retrieval，預設 `WHERE slug IN (research_pack ∪ {transcript_slug})`，**不**做 transitive backlink traversal（顯式 cut） | reminder — 防意外取到無關 chunk，不防 LLM parametric memory leak |
| Layer 2 prompt 紅線 | system prompt 寫「your knowledge is restricted to these N materials」+「if a point isn't covered, omit it; do not fill from training data」| reminder — LLM 不可靠遵守，但設定 baseline |
| Layer 3 citation 信號 | 每段 output 建議結尾 `[source: slug]` 或 `[transcript@HH:MM]`；無 citation 句子 post-process 標 ⚠️ 給修修審 | reminder — best-effort，不 hard fail |

**全部視為 reminders + audit trail**，不視為 enforcement。修修審稿時看 ⚠️ 標記判斷是否要 push back。「閉迴路 invariant」不存在 — 紅線靠修修自律。

### 7. 素材池來源：全走 Robin ingest 進 KB

訪前研究包（文章、作者的書）走 Robin ingest 進 `KB/Wiki/Sources/`。**不為 Project 開新 KB 資料夾**。

Project frontmatter 用 nested-by-source schema（Gemini extensibility 建議）：

```yaml
---
type: project
line: 1b  # 或 2 / 3
topic: "與 X 作者談 Y 主題"

zoro_inputs:
  keywords: [...]
  trending_angles: [...]

line1b_inputs:           # 或 line2_inputs / line3_inputs
  transcript: "[[interview-2026-05-20-author-x]]"
  research_pack:
    - "[[article-slug-1]]"
    - "[[book-author-x]]"
---
```

未來新 line / 新 agent 加 input 區塊不破壞既有 parser。Source page 端 `mentioned_in` backlink 由 dataview / Robin ingest 維護，僅 human navigation 用。Brook 只讀 frontmatter，**不**做 backlink 反查（避免 transitive leak）。

### 8. Entry A 改 context bridge（不全砍）

`/brook/chat` 路由原本是對話式草稿介面。改造後：

- **保留** Project / KB / style profile / compliance vocab / RCP context 打包邏輯
- **砍** 本地 LLM 對話、SQLite `brook_conversations` / `brook_messages` 表、`export_draft` 邏輯、sliding window
- **新行為**：點 button → 整理 context → 開 Claude.ai with prepared prompt（剪貼簿 / URL hand-off / 或 inline 顯示讓修修複製）

理由（採 Codex pushback）：Claude.ai 沒有本地 KB / Project / style / compliance / RCP 知識。完全砍 chat UI 等於每次手動複製貼上 — 摩擦過大會把修修推回另一條繞線路徑。保留 context 打包是給「對的路徑」降摩擦。

### 9. 雙語 cross-lingual 考量（acknowledged constraint）

Style profile 是繁中。KB 可能含英文 source。2b extractor 處理「英文 research + 中文 transcript → 中文 brief」時內生：
- Translation + synthesis + style transfer 三任務同時
- 訓練資料 priors 對同一概念中英文版本可能微差

不在本 ADR 解決，但**明確標記為 known risk**。實作 1b extractor 時 prompt 要顯式處理（「若 source 為英文，譯入修修語氣的中文，標記翻譯段並保留原英文 quote 在 evidence」）。未來 Line 4 / 跨 source 比例升高時可獨立寫 ADR。

## Considered Options

### Rejected: Entry Point A（對話式 chat UI）保留完整草稿能力

Claude.ai 已提供更好對話 UX。自建 SQLite 對話儲存 + sliding window context 是重新發明輪子，且鼓勵 LLM 連續產正文（跨紅線）。但 context 打包仍是本地工作，故 v2 保留 context bridge 部分。

### Rejected: Entry Point B（`compose_and_enqueue` 自動生稿）

從零生稿跨紅線。HITL approval 是 mitigation 不是解方 — 修修每次都重寫一遍才符合自己聲音，等於沒 leverage。

### Rejected (v2 panel adjudication): Move RCP from Robin to Brook (v1 proposal)

v1 提議基於 ADR-012「對內加工」框架。Panel 揭露：(a) RCP 已實作在 Robin，移動成本不只 ADR 字而是 code+tests+schema migration；(b) Robin 已 own annotation / Source Promotion，RCP 的所有 input 都在 Robin 範圍內；(c) Brook 接 RCP 反增加跨 agent schema 耦合。v2 維持 Robin ownership，Brook scaffold（synthesize）與 Robin RCP 並列為兩個 Stage 3→4 scaffold producer。

### Rejected (v2 panel adjudication): Gemini's full reject + claim-atomic schema mandate

Gemini 提議殺 2b、改 scaffold 唯一輸出 claim-atomic EvidencePackage、Repurpose 限 format-shifting、Entry A 改 Interactive Scaffolder。哲學上嚴謹但：(a) 修修主張紅線靠自律不靠架構 enforce；(b) 過度嚴謹會推修修去 Claude.ai 繞線；(c) transcript-as-guest-voice 框架被修修否決（transcript 體現修修策展與框架）。維持 v1 2b 架構 + 採 Gemini「reminders 性質」措辭收斂。

### Rejected: Stage 5 repurpose 2b LLM 重活分散到三個 renderer

三 channel 各自詮釋「修修語氣」會 drift；多 channel 一致性破壞；token 成本三倍；reminder 紅線要在三處重複。集中在 Stage 1 extractor 一次想清楚 brief 嚴格 better。

### Rejected: Research pack 留在 Project 頁面 attachment（不進 KB）

兩條 retrieval 機制（KB hybrid + inline file read）增加維護負擔；素材無法跨 Project reuse；closed-pool 物理隔離難實作（雖然 v2 把 closed-pool 降級為 reminder，仍以 KB-based 為一致較佳）。

## Consequences

### 砍除清單

| 項目 | 動作 |
|---|---|
| `agents/brook/compose.py` | 整檔砍 |
| SQLite `brook_conversations` / `brook_messages` 表 | drop migration |
| `/brook/chat` 對話式行為（LLM chat / export_draft / sliding window） | 砍 |
| `tests/agents/brook/test_compose_pipeline.py` | 砍 |
| `memory/claude/project_brook_design.md` Phase 2 五項 | 全砍 |
| `memory/claude/project_brook_compose_merged.md` | 標 superseded |
| `memory/claude/project_seo_solution_scope.md`「Brook compose 整合排行潛力草稿」 | 砍那條 |

### 保留清單

| 項目 | 理由 |
|---|---|
| `shared/schemas/publishing.py`（`DraftV1` / `GutenbergHTMLV1` / `BlockNodeV1`）| Slice 10 repurpose → Usopp 復用；`DraftV1.agent` 目前 `Literal["brook"]`，repurpose enqueue 時值不變仍合契約 |
| `shared/gutenberg_builder.py` / `shared/gutenberg_validator.py` | 工具層，repurpose blog renderer 可重用 |
| `shared/compliance` | repurpose 也要擋藥事法（FB/IG/blog 都會踩） |
| `shared/tag_filter.py` | repurpose 也要 tag filter |
| `agents/brook/style_profile_loader.py` | 1b extractor 用 |
| `agents/brook/synthesize/` | 改 import path 進 `scaffold/` 或保留原位（PR-5 決定） |
| `agents/brook/repurpose_engine.py` + renderers + `line1_extractor.py` | 已 ship |
| `agents/brook/audit_runner.py` / `seo_block.py` / `seo_narrow.py` | SEO Audit 是 Brook 第三 sub-responsibility |
| `/brook/chat` route 之 context 打包邏輯 | 改造為 context bridge（見 §8） |
| `agents/robin/reading_context_package.py` + 相關 schema/tests | Robin own，**不動** |

### 新增清單

| 項目 | 屬 PR |
|---|---|
| `agents/brook/line1b_extractor.py` + typed `Line1bStage1Result`（含中英翻譯 prompt 處理）| PR-5 |
| `shared/repurpose/closed_pool.py` retrieval wrapper（明確 cut transitive backlink） | PR-5 |
| `prompts/brook/line1b_extract.md`（reminder 紅線 prompt） | PR-5 |
| `synthesize_outline.md` 加 optional `trending_angles` 區塊 | PR-5 |
| `OutlineSection.trending_match` + `BrookSynthesizeStore.unmatched_trending_angles` | PR-5 |
| `/brook/chat` context bridge 改造（剪貼簿 / hand-off → Claude.ai）| PR-3 改造而非全砍 |
| Obsidian `Brook: Scaffold` button + nested frontmatter schema | PR-6 |

### 遷移 PR 順序

1. **PR-1**（本 ADR）：純文件 — ADR-027 v2 + amend ADR-005a + amend ADR-001 + 更新 `CONTENT-PIPELINE.md`。**不**動 ADR-024。
2. **PR-2**：drain pending drafts — 列 `approval_queue WHERE source_agent='brook' AND status='pending'`，修修審 → approve / discard
3. **PR-3**：`/brook/chat` 改造 — 砍對話式 / export_draft / SQLite 表；保留 context 打包改 context bridge
4. **PR-4**：砍 `agents/brook/compose.py` + `test_compose_pipeline.py` + 對應 memory 整理
5. **PR-5**：新增 1b extractor + closed-pool wrapper + trending angles + 雙語 prompt 處理
6. **PR-6**：Obsidian `Brook: Scaffold` button + nested frontmatter schema doc

PR-5 / PR-6 可平行開。

### ADR-001 amendment（new）

ADR-001 將 Brook 定為 Composer。本 ADR 收斂 Brook 實際範圍為 **Scaffold + Repurpose + SEO Audit**。在 ADR-001 Brook 段落補 amendment 註記：「2026-05-17 ADR-027 narrowed Brook from Composer to Scaffold (synthesize) + Repurpose + SEO Audit. RCP scaffold remains with Robin (not Brook).」

### ADR-005a amendment

Status 已改 `Partially Superseded by ADR-027`。Context 補一行：「2026-05-17 ADR-027 砍除 compose pipeline；schema 與 builder / validator 工具層保留供 repurpose blog → Usopp handoff 復用。注意 `DraftV1.agent: Literal['brook']` 維持原值，因 repurpose 仍由 Brook enqueue。」

### CONTENT-PIPELINE.md update

- Line 3 Stage 4 cell 改為「⬜ synthesize outline → 修修自寫（可用 Claude.ai 對話協助），LLM 不代寫正文（ADR-027）」
- Agents × Stages Brook 列改為「**Scaffold + Repurpose + SEO Audit**（ADR-027）」
- Brook Stage 3 cell 加「synthesize（ADR-021）」；**不**標 RCP（Robin own）
- Brook Stage 5 cell 維持原內容 + 加「🚧 Line 1b 訪談+research_pack 2b mode（ADR-027）」
- Brook Stage 7 cell 維持 SEO

### ADR-024：不改

v1 amendment 整段 revert。原 ADR-024 對 RCP「Robin may produce」+「Brook must not bypass RCP to compose Line 2 atomic content」均維持原文。本 ADR 補強為「Robin produces RCP; Brook consumes RCP in Stage 4 scaffold flow when applicable」— 寫在本 ADR 不寫進 ADR-024。

### Open follow-ups（不在本 ADR 範圍）

- **Obsidian CLI integration research** — 獨立 research doc，可能影響 Brook button 實作方式
- **Slice 10 repurpose → Usopp WP** — 確認 schema 復用是否順暢
- **Line 2 / Line 3 atomic content 落地後的 2a extractor 設計** — 待修修開始實際產 atomic 後再凍結 schema
- **Bilingual cross-lingual handling** 若實作 1b 後痛點浮現，獨立寫 ADR
- **Brook scope 第三度收斂** — SEO 是否真該屬 Brook 待 SEO 中控台落地後再檢討（觀察 #2）

## References

- `CONTENT-PIPELINE.md:36` — 原始 Stage 4 紅線
- ADR-001 — Brook = Composer（本 ADR amend 為 Scaffold + Repurpose + SEO Audit）
- ADR-005a — Brook Gutenberg Pipeline（compose 部分 superseded）
- ADR-012 — Zoro vs Brook 向外/對內邊界
- ADR-014 — RepurposeEngine plug-in interface（本 ADR 在此架構內擴 1b）
- ADR-017 — Annotation-KB integration
- ADR-021 — Brook synthesize (Status: Proposed)
- ADR-024 — Source Promotion + RCP（**不**動，本 ADR v1 amendment 取消）
- `docs/research/2026-05-17-codex-adr-027-audit.md` — Codex 審計
- `docs/research/2026-05-17-gemini-adr-027-audit.md` — Gemini 審計
- `agents/brook/synthesize/` — 已 ship synthesize 實作
- `agents/robin/reading_context_package.py` — 已 ship RCP 實作（Robin own）
- `agents/brook/repurpose_engine.py` — 已 ship repurpose 實作
- `prompts/brook/synthesize_outline.md` — 現行 outline drafter prompt
