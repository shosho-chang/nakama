# ADR-011: Textbook Ingest v2 — Karpathy aggregator + Vision describe + 共用 kb_writer

**Date:** 2026-04-26
**Status:** Accepted
**Supersedes:** [ADR-010](ADR-010-textbook-ingest.md) (textbook-ingest v1)

---

## 1. Context

[ADR-010](ADR-010-textbook-ingest.md) 解決了「整本書怎麼進 KB」的 workflow gap — Claude Code skill + Opus 4.7 1M context、章為單位、跨書共享 Concept/Entity 池。Phase 1 MVP 把 *Biochemistry for Sport and Exercise Metabolism* (MacLaren & Morton, Wiley 2024) Ch.1 ingest 進 vault 後，揭露 v1 設計留下三個 **觀念級破洞**：

1. **Concept page 不是 aggregator** — Robin pipeline (`agents/robin/ingest.py:472-510` `_update_wiki_page`) 把 update 寫成 body 末尾 `## 更新（{date}）` block，內容是 LLM 生成的 imperative todo（「應新增 X、應補充 Y」），**從未** merge 進 concept body 主體；觀察 `肌酸代謝.md` 末尾 10 條 `## 更新（2026-04-13）` 全是 todo 句、page 主體永遠停留在第一次 ingest 版本。違反 Karpathy gist「cross-source personal wiki」哲學（[karpathy gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)）。
2. **圖表全 drop** — EPUB primary path 用 BeautifulSoup `get_text()` 把 `<img>` `<table>` `<math>` 攤平，章節 source 文字密度遠低於原書；醫學/運動科學教科書的圖表本來就是 first-class 載體（biochem 路徑圖、解剖圖），drop 等於丟最有 retrieval 價值的內容。
3. **無 conflict detection** — `_get_concept_plan` (`agents/robin/ingest.py:298-339`) 只把 `KB/Wiki/Concepts/` + `Entities/` 的 stem 列表注入 prompt，**從未** 讓 LLM 看到既有 page body — 同義異名 false negative + 內容衝突無法偵測 + update 訊號丟失三連發。例：ch1 教科書原文寫 PCr 主導 1-10 秒、既有 `磷酸肌酸系統.md` 寫 10-15 秒；兩個說法很可能 measure 不同 endpoint 但 pipeline 沒偵測，agent retrieval 拿到 page 會自信地說 10-15 秒。

此外發現兩個 schema / infra 破洞：

4. **schema 雙軌** — textbook-ingest skill 規範 `mentioned_in:` schema、Robin production code 寫 `source_refs:` schema — 兩套並存且互不相容；同一頁可能同時被兩條 ingest path 寫入產生不一致。
5. **既有 broken pages** — `ATP再合成.md` / `肌酸代謝.md` frontmatter 已壞（mid-page `---` + raw text），是 `_update_wiki_page` 對 unicode 長 filename 的 yaml.dump 處理錯誤造成的歷史 bug（`shared/obsidian_writer.py` 缺 `width=10**9`）。

完整 audit（12 findings）與 sequencing 見 [docs/plans/2026-04-26-ingest-v2-redesign-plan.md](../plans/2026-04-26-ingest-v2-redesign-plan.md)。

**援引原則**：

- Schema：[`docs/principles/schemas.md`](../principles/schemas.md) §3 schema_version、§4 extra="forbid"、§7 Literal 取代 enums
- Reliability：[`docs/principles/reliability.md`](../principles/reliability.md) §1 idempotency（重 ingest 同章同 source 不會 double-merge）、§11 schema 遷移可向前/向後相容
- Observability：[`docs/principles/observability.md`](../principles/observability.md) §1 structured log（每次 ingest 寫 operation_id + concept slug + action）
- Karpathy KB Wiki 哲學：[karpathy gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)
- Concept aggregator 設計哲學：[memory/claude/feedback_kb_concept_aggregator_principle.md](../../memory/claude/feedback_kb_concept_aggregator_principle.md)

---

## 2. Principle Restatement

修修在 2026-04-26 session 凍結以下四條原則，**所有 ingest pipeline 動工都必須對齊**。後續 §3 五個 sub-decision 都是這四條原則的直接展開。

| # | 原則 | 內涵 | 違反時的具體後果 |
|---|---|---|---|
| **P1** | **Karpathy aggregator** | Concept page 是 cross-source evidence aggregator，不是 oracle、不是 changelog；新 source 進來要把資訊真正 merge 進 concept body 主體（Definition / Core Principles / Practical Applications），有衝突另闢 `## 文獻分歧 / Discussion` 結構化記錄 | concept body 永遠停在第一次 ingest 版本；後續 source 全變補丁；retrieval 拿到的不是「累積的最新觀點」而是「初版加 changelog 噪音」 |
| **P2** | **LLM-readable deep extract** | Ingest 用最強 model（Opus 4.7）+ 不省 token；不為人類閱讀友善而摺扣，給 agent 消化用，retrieval-time 由下游 agent 做風格與抽象層級調整 | 章節 source 字數受限 → 細節（公式、實驗條件、機轉）丟失 → retrieval 拿不到精確答案，只能拿到「教科書的維基百科版本」 |
| **P3** | **圖表 first-class** | 圖片必須 export + Vision LLM domain-aware describe + table preserve as markdown table + math 公式 LaTeX 化；不可 drop 任何 visual / structured 內容 | biochem 路徑圖、解剖圖、實驗數據表全消失；對視覺載體重的學科（解剖、生化、神經）等於 ingest 一半 |
| **P4** | **Conflict detection mandatory** | aliases-based dedup（同義異名）+ 強制讀既有 page body 做 cross-source diff + `## 文獻分歧 / Discussion` aggregation；新 source 對同一 concept 提出與既有相異觀點時必須結構化記錄、不可靜默 append | 同義異名 false negative（「糖解作用」與「糖酵解」各建一頁）+ 跨 source 矛盾被噪音吞掉 + agent retrieval 自信給出片面觀點 |

**Token / Wall-time reality check**（用 Opus 4.7 thorough 模式）：

- 單章 input（含 Vision pass）~30-50k token
- 單章 output（chapter source thorough）~30-80k token
- 全書 11 章 ~500k-1M token / 60-90 分鐘
- Max 200 monthly quota：一本中型教科書 ≈ 一週 quota 高 burn day（合理投資 — 一次 ingest 影響後續多次 retrieval；ingest 是 one-shot deep work、retrieval 是 many-shot lightweight，職責分離）

---

## 3. Decision

### 3.1 Concept page schema v2（取代 [ADR-010 D-2](ADR-010-textbook-ingest.md#d2-frontmatter-schema-凍結) Concept 段）

#### 3.1.1 Frontmatter 強制欄位

```yaml
---
schema_version: 2
title: 肌酸代謝
type: concept
domain: bioenergetics            # 主領域，from allowlist
aliases:                          # ★ P4：同義詞清單，dedup 用
  - creatine metabolism
  - 磷酸肌酸代謝
  - PCr metabolism
mentioned_in:                     # ★ P1：aggregator backlink wikilink list
  - "[[Sources/Books/biochemistry-sport-exercise-2024/ch1]]"
  - "[[Sources/pubmed-12345-creatine-supplementation]]"
  - "[[Sources/popular-health-creatine-myths]]"
source_refs:                      # 過渡相容：留給 Robin v1 schema 寫的 path list
  - "Sources/Books/biochemistry-sport-exercise-2024/ch1.md"
  - "Sources/pubmed-12345-creatine-supplementation.md"
discussion_topics:                # ★ P4：conflict 警示燈，agent retrieval 看到此欄位知道要讀 Discussion section
  - PCr 主導窗口時長範圍（1-10s vs 10-15s 跨 source 差異）
  - 肌酸補充劑量爭議（每日 5g 維持 vs 20g 載入）
confidence: 0.85
tags:
  - "#concept"
  - "#energy-system"
created: 2026-04-25
updated: 2026-04-26
---
```

**欄位語義鎖定**：

| Field | Type | 必填 | 用途 |
|---|---|---|---|
| `schema_version` | `Literal[2]` | yes | Loader dispatch；v1 page 第一次 update 自動 lazy migrate |
| `title` | `str` | yes | 顯示用 |
| `type` | `Literal["concept"]` | yes | 區分 concept / entity / source |
| `domain` | `str` | yes | 主領域 from allowlist（bioenergetics / cardiovascular / neurology / ...） |
| `aliases` | `list[str]` | yes (可空) | dedup key — 任何新 ingest 的 candidate slug 先查所有現有 page 的 aliases |
| `mentioned_in` | `list[wikilink]` | yes | aggregator backlink — 每次新 source ingest 命中此 concept 時 append（idempotent） |
| `source_refs` | `list[path]` | optional | 過渡欄位，Robin v1 schema 留下；v2 寫入時也同步維護方便 lazy migrate |
| `discussion_topics` | `list[str]` | optional | 列出 page body 內 `## 文獻分歧 / Discussion` 的 topic headline；agent retrieval 看到此欄位代表需要讀完 Discussion 才能下結論 |
| `confidence` | `float [0,1]` | optional | Robin 既有，保留 |
| `tags` | `list[str]` | optional | Obsidian tag |
| `created` / `updated` | `date` | yes | 追蹤頁面生命週期 |

**Pydantic schema 落地位置**：`shared/schemas/kb.py`（新檔），含 `ConceptPageV2 / ConceptPageV1 / migrate_v1_to_v2`。`extra="forbid"` 強制（per `schemas.md` §4），未知欄位第一秒就炸。

#### 3.1.2 Body schema

固定 H2 順序（缺的 section 用 `_(尚無內容)_` 占位，**不可省略 heading**，方便 LLM diff-merge 定位）：

```markdown
# 肌酸代謝

## Definition
（簡短一段定義，from 主流文獻共識）

## Core Principles
- 機制 1
- 機制 2
- ...

## Sub-concepts
- [[磷酸肌酸系統]] — 短時程（1-10s）能量緩衝
- [[肌酸激酶系統]] — PCr ↔ ATP 轉換催化
- ...

## Field-level Controversies
（領域共識爭議 — 該領域知名 issue，eg. 「肌酸補充劑量爭議」；與 KB 內部分歧分開）

## 文獻分歧 / Discussion
> ★ P4：KB 內部 cross-source 分歧；只在實際偵測到衝突時填入

### Topic 1: PCr 主導窗口時長範圍
- **[[Sources/Books/biochemistry-sport-exercise-2024/ch1]]**：1-10 秒
- **[[Sources/popular-health-creatine-myths]]**：10-15 秒
- **可能原因**：教科書 ch1 measure 的是 ATP 80% depletion，popular-health source 引用 ATP 50% depletion；endpoint 不同。
- **共識點**：100% 同意 PCr 是高強度爆發開頭階段的主能量。
- **不確定區**：5-10 秒區間 PCr 占比 vs 糖解占比的精確分配。

## Practical Applications
- 訓練處方：...
- 飲食補充：...

## Related Concepts
- [[ATP再合成]]
- [[糖解作用]]

## Sources
- [[Sources/Books/biochemistry-sport-exercise-2024/ch1]]
- [[Sources/pubmed-12345-creatine-supplementation]]
```

#### 3.1.3 Update logic 凍結

| Scenario | v1 行為 | v2 行為 |
|---|---|---|
| 命中既有 concept、無衝突 | append `## 更新（{date}）` block，內容是 LLM imperative todo | LLM 讀既有 body + 新 source extract → diff-merge into Definition / Core Principles / Sub-concepts / Practical Applications；append `mentioned_in:` 新 wikilink |
| 命中既有 concept、有衝突 | append `## 更新` block 跟非衝突合在一起 | 寫進 `## 文獻分歧 / Discussion` 結構化 section（含 source wikilink + 數字差異 + 可能原因 + 共識點/不確定區）；同步 append `discussion_topics:` frontmatter |
| 新 concept 候選但同義異名 | 各建一頁（同 concept 有多 page） | 先用 `aliases:` dedup 查既有 page；命中則走「命中既有 concept」path、把候選 slug 加入 `aliases:` |
| Robin v1 schema page 第一次被 v2 update | n/a | lazy migrate：`schema_version: 1` → `2`；`source_refs:` 既有 path 翻譯成 `mentioned_in:` wikilink；body 末尾既有 `## 更新（date）` block 一次性 LLM diff-merge into main body（手動 review 後 commit） |

**禁止 list（v2 強制）**：

- ❌ body 末尾 append `## 更新（date）` 純 changelog block（任何形式，包括 `## 更新內容`、`## 變更紀錄`）
- ❌ 同義異名 slug 各建一頁（如「糖解作用」與「糖酵解」分開存在）
- ❌ LLM 寫 imperative todo（「應新增 X、應補充 Y」）當作 update — 這只是 nag、不是 merge

#### 3.1.4 Broken page 修復

- A-11 migration script `scripts/migrate_broken_concept_frontmatter.py` 由 Step 1 hygiene PR 處理（branch `fix/config-and-broken-pages`，獨立交付）
- v2 schema 落地後額外 backfill：把已修好的 page 全 lazy migrate 一輪，確保 `schema_version: 2` 全 vault 一致

---

### 3.2 Chapter Source page schema v2（擴充 [ADR-010 D-2](ADR-010-textbook-ingest.md#d2-frontmatter-schema-凍結) Chapter Source 段）

#### 3.2.1 Frontmatter

保留 v1 全欄位，新增 `figures:` list：

```yaml
---
type: book_chapter
source_type: book
content_nature: textbook
schema_version: 2                 # 標記新 schema
lang: en
book_id: biochemistry-sport-exercise-2024
chapter_index: 1
chapter_title: "Energy Sources for Muscular Activity"
section_anchors:
  - "1.1 Introduction"
  - "1.2 Phosphagen System"
  - "1.3 Glycolytic System"
  - "1.4 Oxidative System"
page_range: "4-16"
figures:                          # ★ P3：圖片 first-class
  - ref: fig-1-1
    path: "Attachments/Books/biochemistry-sport-exercise-2024/ch1/fig-1-1.png"
    caption: "Schematic of ATP-PCr energy system kinetics during high-intensity exercise"
    llm_description: |
      Vision-generated description (domain-aware, biochem context):
      X-axis: time (0-30 seconds), Y-axis: relative ATP concentration (%).
      Three overlapping curves: PCr-derived ATP (peaks 0-3s, decays by 10s),
      glycolytic ATP (rises 5-30s), oxidative ATP (slow rise after 15s).
      Annotated transition point at ~10s where PCr depletion becomes rate-limiting.
    tied_to_section: "1.2 Phosphagen System"
  - ref: tab-1-1
    path: "Attachments/Books/biochemistry-sport-exercise-2024/ch1/tab-1-1.md"  # markdown table file
    caption: "ATP yield per substrate across energy systems"
    tied_to_section: "1.4 Oxidative System"
ingested_at: 2026-04-26
ingested_by: "claude-code-opus-4.7"
---
```

**Figures list 語義**：

| Field | Type | 必填 | 說明 |
|---|---|---|---|
| `ref` | `str` (slug) | yes | 章內唯一 ID（fig-{chapter}-{n} / tab-{chapter}-{n} / eq-{chapter}-{n}） |
| `path` | `str` (vault relative) | yes | `Attachments/Books/{book_id}/ch{n}/` 下的圖檔或 md table |
| `caption` | `str` | yes | 原書圖說（若有），無則 LLM 從 alt-text + 周圍段落推斷 |
| `llm_description` | `str` | yes (圖) / no (表/公式) | Vision LLM 產出的 domain-aware 描述（見 §3.4） |
| `tied_to_section` | `str` | yes | 哪個 section_anchor 引用此圖；retrieval 反查用 |

#### 3.2.2 Body schema

每節 deep extract（不限字數，per P2）+ verbatim quote（保留原書關鍵句 1-2 句佐證）+ section concept map：

```markdown
# Ch.1 Energy Sources for Muscular Activity

## 1.1 Introduction
（不限字數的 deep extract — 機制細節、實驗條件、原書數字、引用文獻全保留）

> 原書 verbatim：「Skeletal muscle requires a continuous supply of ATP for mechanical work...」(p.4)

### Section concept map
- 主軸：ATP demand vs supply asymmetry
- 引入：PCr / glycolysis / oxidative 三大系統
- 連結：[[ATP再合成]] [[能量連續體]]

## 1.2 Phosphagen System
（同上結構，含 Figure 1.1 inline 引用：見 frontmatter `figures.fig-1-1`）

...
```

**禁止 list**：

- ❌ chapter-summary prompt 寫「每節 300-500 字」（v1 prompt 違反 P2 — 不限字數，給 agent 消化用，不是給人類讀的摺頁）
- ❌ 整章 truncate at 30000 chars（A-10：`IngestPipeline._truncate_at_boundary` 一律截斷違反 P2）

---

### 3.3 Ingest pipeline v2 — 5-step（取代 [ADR-010 D-5](ADR-010-textbook-ingest.md#d5-skill-內部-workflow) skill workflow）

每章 ingest 改為 5 個 step，每個 step 有明確 input / output / 落點：

#### Step 1: Read chapter（含圖片 export）

| | |
|---|---|
| **Input** | `book_id` + `chapter_index` + 原檔 path（EPUB / PDF） |
| **Output** | (a) `chapter_text: str`（含 placeholder 占位符 `<<FIG:fig-1-1>>` / `<<TAB:tab-1-1>>` / `<<EQ:eq-1-1>>`）<br>(b) `Attachments/Books/{book_id}/ch{n}/fig-N.{ext}`（圖檔）<br>(c) `Attachments/Books/{book_id}/ch{n}/tab-N.md`（markdown table 檔，table 用） |
| **Code** | `.claude/skills/textbook-ingest/scripts/parse_book.py` 改寫 `_epub_html_to_text` (line 302-319) — BeautifulSoup walker 對 `<img>` `<table>` `<math>` 改寫成占位符 + dump assets，不再 `get_text()` 攤平 |
| **PDF path** | 改用 `pymupdf4llm.to_markdown(with_tables=True)`（A-9）；圖片用 `fitz.Document.extract_image()` |

#### Step 2: Vision describe per figure

| | |
|---|---|
| **Input** | (a) `figure_path: Path`（單張圖）<br>(b) `book_subtype: str`（從 Book Entity frontmatter，eg. `textbook_pro`）<br>(c) `surrounding_text: str`（圖前後 ±500 字 context） |
| **Output** | `llm_description: str`（domain-aware，含座標軸 / 數字 / annotation / 與 surrounding_text 的關係） |
| **Domain prompt**（從 book_subtype 推斷） | `textbook_pro` + biochem book → 「You are a biochemistry expert annotating a textbook figure for a downstream knowledge base. Describe axes, units, curves, key inflection points, and how the figure illustrates the concept discussed in the surrounding text. Use precise scientific terminology.」<br>`textbook_pro` + anatomy book → 「You are an anatomy expert...」（同 pattern） |
| **Failure mode** | Vision 失敗 / 無法辨識 → fallback `[FIGURE: alt-text only — manual annotation required]` placeholder + log warning |
| **Vision LLM 選型** | <!-- 待修修拍板 — Opus 4.7 vs Sonnet 4.6；plan §8 Q4 --> |

#### Step 3: Deep extract chapter source page

| | |
|---|---|
| **Input** | (a) `chapter_text: str`（含已被 Vision describe 的圖片占位符替換成 inline description）<br>(b) `chapter_metadata: dict`（chapter_index / chapter_title / section_anchors / page_range）<br>(c) `book_metadata: dict`（book_id / title / authors / lang） |
| **Output** | `chapter_source_md: str`（完整 chapter source page md，含 frontmatter + body）按 §3.2 schema |
| **Prompt** | `.claude/skills/textbook-ingest/prompts/chapter-summary.md` 重寫 — 拿掉「每節 300-500 字」字數上限（A-4）+ 強制 verbatim quote + 強制 Section concept map |
| **Write target** | `KB/Wiki/Sources/Books/{book_id}/ch{n}.md` via `kb_writer.write_source_page()` |

#### Step 4: Concept extract with conflict detection

| | |
|---|---|
| **Input** | (a) `chapter_source_md: str`（Step 3 產出）<br>(b) `existing_concepts_map: dict[str, ConceptPageV2]`（vault 全掃，slug → 完整 page；含 aliases + 完整 body） |
| **Output** | `concept_actions: list[ConceptAction]`（每個候選 concept 一個 action）按以下 4 種 |
| **4 種 action** | `create` — 新 concept，無同名 / 無同義名命中<br>`update_merge` — 命中既有 concept，內容無衝突 → 走 LLM diff-merge into main body<br>`update_conflict` — 命中既有 concept，內容衝突 → 寫進 `## 文獻分歧 / Discussion`<br>`noop` — 命中既有 concept，且新 source 完全沒提供新資訊 |
| **Prompt** | `agents/robin/prompts/extract_concepts.md` 重寫 — 同時注入 candidate slug + **每個既有 page 的 aliases + 完整 body**（A-2）；prompt 顯式說明「對每個候選 concept 你必須輸出 action ∈ {create, update_merge, update_conflict, noop}」 |
| **Dedup key** | aliases-based — 候選 slug 先用 lowercase + zh-TW 正規化比對所有 existing page 的 aliases；命中即視為同 concept |
| **Conflict 判定** | LLM 比對既有 body 與新 extract 的「事實聲明」（數字、時間範圍、機轉敘述）；定量差異 ≥ 20% 或定性矛盾 → `update_conflict` |

#### Step 5: Wiki page write via kb_writer

| | |
|---|---|
| **Input** | `concept_actions: list[ConceptAction]`（Step 4 產出） |
| **Output** | Vault 寫入完成；每個 action 對應一筆 structured log entry（`operation_id` + `slug` + `action` + `pre_hash` + `post_hash`） |
| **Code** | `shared/kb_writer.py`（新檔）`upsert_concept_page(slug, action, ...)`；按 action 分派 |
| **`create`** | 走 §3.1.2 body template，frontmatter 填入候選資料；append `mentioned_in:` 新 source wikilink |
| **`update_merge`** | LLM 讀既有 body + 新 source extract → 產出 diff-merged body；overwrite page；append `mentioned_in:`；更新 `updated:` |
| **`update_conflict`** | 讀既有 `## 文獻分歧 / Discussion` section（無則 create）→ append 新 topic block（含 source wikilink + 數字差異 + 可能原因 + 共識點/不確定區）；append `discussion_topics:` frontmatter；同步 append `mentioned_in:` |
| **`noop`** | 仍 append `mentioned_in:`（標記此 source 看過此 concept）；不改 body；不改 `updated:` |
| **Backup** | 每次 `update_merge` / `update_conflict` 前先寫 `.bak` 到 `data/kb_backup/{slug}-{utc-ts}.md`，retain 24h（防 LLM diff 把既有內容洗掉） |
| **Idempotency** | 同一 (source_link, slug) pair 重 ingest → `mentioned_in:` 不 double-append（per `reliability.md` §1） |

---

### 3.4 圖片 / Table / 公式處理規格（取代 [ADR-010 D-5](ADR-010-textbook-ingest.md#d5-skill-內部-workflow) PDF/EPUB 段）

#### 3.4.1 EPUB 路徑（primary）

`.claude/skills/textbook-ingest/scripts/parse_book.py` 的 `_epub_html_to_text` 改寫為 walker pattern：

| HTML element | 處理 | 落點 |
|---|---|---|
| `<img src="...">` | 1. 抽 src + alt-text<br>2. 從 EPUB zip 讀 binary → 寫 `Attachments/Books/{book_id}/ch{n}/fig-{N}.{ext}`<br>3. text 內替換成 `<<FIG:fig-{chapter}-{N}>>` 占位符 | `Attachments/.../*.{png,jpg,svg,gif,webp}` |
| `<table>` | 1. recursive walker 取 `<thead>` / `<tbody>` / `<tr>` / `<th>` / `<td>` → markdown table 格式<br>2. 寫 `Attachments/Books/{book_id}/ch{n}/tab-{N}.md`<br>3. text 內替換成 `<<TAB:tab-{chapter}-{N}>>` 占位符 | `Attachments/.../tab-{N}.md` |
| `<math>` (MathML) | 1. 用 `mathml2latex` 轉 LaTeX（或 fallback：保留 MathML 原 XML）<br>2. text 內 inline 嵌入 `$$...$$`（不 export，公式短） | inline in chapter source body |
| `<svg>` (vector) | 同 `<img>` 處理（保留 vector，Vision 也能 process） | `Attachments/.../fig-{N}.svg` |
| `<figcaption>` | 與相鄰 `<img>` 配對；填入 `figures[].caption` | inline |
| 其他 (`<p>`, `<h1-6>`, `<ul>`, `<blockquote>`) | 沿用既有 markdown convert 邏輯 | inline |

**新增依賴**：

- `mathml2latex >= 0.0.5`（PyPI；MathML → LaTeX converter）
- 既有：`ebooklib >= 0.18` / `beautifulsoup4 >= 4.12`

#### 3.4.2 PDF 路徑（fallback）

| 元素 | 處理 |
|---|---|
| Text + table | `pymupdf4llm.to_markdown(doc, write_images=False, with_tables=True)`（A-9：原本用 raw `get_text()` 丟 table） |
| 圖片 | `for page in doc: for img in page.get_images(): doc.extract_image(img.xref)` → 寫 `Attachments/.../fig-{N}.{ext}` + 占位符 |
| 公式 | PDF 內公式多半已 rasterized 進圖片 → 走 `<img>` 路徑 → Vision describe（提示「此圖為公式，請 transcribe 為 LaTeX」） |

#### 3.4.3 Vision describe prompt skeleton

`prompts/vision-describe.md`（新檔）：

```markdown
You are a {{domain}} expert annotating a {{book_subtype}} figure for downstream LLM consumption.

Figure context (from book "{{book_title}}", chapter "{{chapter_title}}"):
- Section: {{tied_to_section}}
- Original caption: {{caption_or_alt_text}}
- Surrounding text (±500 chars):
  {{surrounding_text}}

Describe the figure with these elements (in this order):
1. Figure type (line plot, scatter, schematic diagram, anatomical illustration, flowchart, ...)
2. Axes / units / scale (if applicable) — be precise with numbers
3. Key data points / curves / annotations
4. How the figure illustrates the concept discussed in surrounding text
5. Any precise scientific terminology, anatomical labels, equation transcription needed

Output: 3-8 sentences of dense scientific description. Do NOT add disclaimers or "I cannot see..." — if the image is unclear, transcribe what you can identify.
```

**Domain mapping**（從 `book_subtype` + `domain` frontmatter 推斷）：

| book_subtype | domain hint | Vision system role |
|---|---|---|
| `textbook_pro` | bioenergetics / biochem | biochemistry expert |
| `textbook_pro` | anatomy | anatomy expert |
| `textbook_pro` | neurology | neurology expert |
| `textbook_exam` | (any) | medical exam tutor |
| `popular_health` | (any) | science journalist |
| `clinical_protocol` | (any) | clinical guideline writer |

**Vision LLM 選型**：<!-- 待修修拍板 — Opus 4.7（品質高、token 貴 5x）vs Sonnet 4.6（token 便宜、教科書 figure 描述應夠用）；plan §8 Q4 -->

---

### 3.5 共用 kb_writer module（取代 [ADR-010 D-5](ADR-010-textbook-ingest.md#d5-skill-內部-workflow) 各 skill 各自寫入）

**目標**：textbook-ingest skill、kb-ingest skill、Robin agent 三條路徑收斂到 `shared/kb_writer.py` 共用底層；杜絕 schema 雙軌（A-1 / A-8）。

#### 3.5.1 介面（function signatures）

```python
# shared/kb_writer.py

from pathlib import Path
from typing import Literal
from shared.schemas.kb import (
    ConceptPageV2,
    ChapterSourcePageV2,
    BookEntityV2,
    ConceptAction,
    MigrationReport,
)

# --- Read ---

def read_concept_for_diff(slug: str) -> ConceptPageV2 | None:
    """讀既有 concept page；回 None 若不存在。
    用於 §3.3 Step 4 把既有 body 注入 extract_concepts prompt。
    自動 lazy migrate v1 → v2（in-memory，不寫回）。
    """

def list_existing_concepts() -> dict[str, ConceptPageV2]:
    """掃 KB/Wiki/Concepts/ 全部 page → slug → page；含 aliases dedup index。
    用於 §3.3 Step 4 input。
    """

# --- Write (concept) ---

def upsert_concept_page(
    slug: str,
    action: Literal["create", "update_merge", "update_conflict", "noop"],
    source_link: str,                       # wikilink form, e.g. "[[Sources/...]]"
    frontmatter_patch: dict | None = None,  # action=create 時用
    body_patch: str | None = None,          # action=create / update_merge 時用
    conflict: dict | None = None,           # action=update_conflict 時用：{topic, existing, new, possible_reason, consensus, uncertainty}
) -> Path:
    """統一 concept page 寫入入口。
    - action=create: 走 §3.1.2 template；frontmatter_patch + body_patch 填入
    - action=update_merge: 讀既有 body → LLM diff-merge with body_patch → overwrite
    - action=update_conflict: 寫 `## 文獻分歧 / Discussion` + append discussion_topics
    - action=noop: 僅 append mentioned_in
    每次 update 寫 .bak 到 data/kb_backup/。
    Idempotent：(slug, source_link) 重複呼叫不會 double-append mentioned_in。
    """

def update_mentioned_in(page_path: Path, source_link: str) -> bool:
    """Append source_link 到 mentioned_in: list（idempotent）。
    回 True 若實際 append、False 若已存在。
    """

def aggregate_conflict(
    page_path: Path,
    topic: str,
    source_link: str,
    existing_claim: str,
    new_claim: str,
    possible_reason: str | None = None,
    consensus: str | None = None,
    uncertainty: str | None = None,
) -> None:
    """在 page 的 `## 文獻分歧 / Discussion` section 下 append 一個 topic block；
    同步 append topic 到 frontmatter discussion_topics: list。
    """

# --- Write (source / entity) ---

def write_source_page(
    book_id: str,
    chapter_index: int,
    source_md: str,
    figures: list[dict] | None = None,
) -> Path:
    """寫 KB/Wiki/Sources/Books/{book_id}/ch{n}.md (chapter source page v2)。
    figures 寫入 frontmatter figures: list。
    """

def upsert_book_entity(book_id: str, metadata: dict, status: Literal["partial", "complete"]) -> Path:
    """寫 / 更新 KB/Wiki/Entities/Books/{book_id}.md。
    chapters_ingested counter 自動 increment。
    """

# --- Migration ---

def migrate_v1_to_v2(slug: str, dry_run: bool = False) -> MigrationReport:
    """單頁 v1 → v2 lazy migrate。
    - schema_version: 1 → 2
    - source_refs: path list → 同步翻譯成 mentioned_in: wikilink list（兩欄並存）
    - body 末尾 `## 更新（date）` block 一次性 LLM diff-merge into main body
    - 移除 `## 更新` section
    dry_run=True 不寫入，回 MigrationReport 顯示預期 diff。
    """

def backfill_all_v1_pages(dry_run: bool = False) -> list[MigrationReport]:
    """掃 vault 全 v1 page → 一次性 migrate。
    Phase 1：建議手動 review 每筆 dry_run report 後再正式跑。
    """
```

#### 3.5.2 落點

| Caller | 之前寫入路徑 | v2 改用 |
|---|---|---|
| `agents/robin/ingest.py:472-510` `_update_wiki_page` | 直接 `obsidian_writer.write_page()` + body append `## 更新` | `kb_writer.upsert_concept_page(action=...)` — Robin 端只負責呼叫 |
| `.claude/skills/textbook-ingest/scripts/*.py` | 直接 `obsidian_writer.write_page()` 寫 concept | 同上 |
| `.claude/skills/kb-ingest/scripts/*.py` | 同上 | 同上 |

**`obsidian_writer.write_page()` 仍保留** — 給非 KB 寫入（journal、project file、其他 vault 結構）用；KB Wiki 寫入一律走 `kb_writer`。

#### 3.5.3 Schema 落地

`shared/schemas/kb.py`（新檔）：

```python
from datetime import date
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, AwareDatetime

class ConceptPageV2(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[2] = 2
    title: str
    type: Literal["concept"]
    domain: str
    aliases: list[str] = Field(default_factory=list)
    mentioned_in: list[str] = Field(default_factory=list)  # wikilink form
    source_refs: list[str] = Field(default_factory=list)   # 過渡相容
    discussion_topics: list[str] = Field(default_factory=list)
    confidence: float | None = None
    tags: list[str] = Field(default_factory=list)
    created: date
    updated: date

class ChapterSourcePageV2(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[2] = 2
    type: Literal["book_chapter"]
    source_type: Literal["book"]
    content_nature: Literal["textbook"]
    lang: str
    book_id: str
    chapter_index: int
    chapter_title: str
    section_anchors: list[str]
    page_range: str
    figures: list["FigureRef"] = Field(default_factory=list)
    ingested_at: date
    ingested_by: str

class FigureRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    ref: str  # constr(pattern=r"^(fig|tab|eq)-\d+-\d+$") in production
    path: str
    caption: str
    llm_description: str | None = None
    tied_to_section: str

class ConceptAction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    slug: str
    action: Literal["create", "update_merge", "update_conflict", "noop"]
    candidate_aliases: list[str] = Field(default_factory=list)
    extracted_body: str | None = None      # action=create / update_merge
    conflict: "ConflictBlock | None" = None  # action=update_conflict

class ConflictBlock(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    topic: str
    existing_claim: str
    new_claim: str
    possible_reason: str | None = None
    consensus: str | None = None
    uncertainty: str | None = None

class MigrationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slug: str
    from_version: int
    to_version: int
    dry_run: bool
    changes: list[str]  # human-readable diff lines
    skipped_reason: str | None = None
```

---

## 4. Migration（v1 → v2）

#### 4.1 Sequencing

1. **Step 1（hygiene PR，已 in-flight）** — `fix/config-and-broken-pages` branch：A-5 `config.py` env 順序 + A-11 broken page migration script + 一次性掃 vault 修好。本 ADR scope 不含此 PR；Step 1 merge 後 Step 3 才動工。
2. **Step 2（本 ADR + 修修拍板的 4 questions）** — 本 ADR 與 [docs/plans/2026-04-26-ingest-v2-decisions.md](../plans/2026-04-26-ingest-v2-decisions.md)（修修勾選）。
3. **Step 3（implementation，1-2 週）** — 按本 ADR §3 落地 `shared/kb_writer.py` + `shared/schemas/kb.py` + 重寫 `extract_concepts.md` + 改 `parse_book.py` + 拿掉 `chapter-summary.md` 字數上限。

#### 4.2 既有 v1 schema page 處理

| Page 類別 | v1 → v2 處理 |
|---|---|
| broken concept page（A-11，2 頁：`ATP再合成.md` `肌酸代謝.md`） | Step 1 hygiene PR 修好 frontmatter；Step 3 上線後 lazy migrate v1 → v2 schema |
| Robin v1 schema concept page（既有 `source_refs:` schema） | Step 3 上線後 lazy migrate：第一次被 `update_*` action 命中時自動跑 `migrate_v1_to_v2()`；body 末尾既有 `## 更新（date）` block 一次性 LLM diff-merge into main body |
| ch1 已 ingest 的 4 新 concept（`能量連續體` / `糖解作用` / `有氧能量系統` / `無氧能量系統`） | Step 3 上線後重 ingest ch1 一次（覆寫）— 走完整 v2 schema |
| ch1 update 的 6 既有 concept（`磷酸肌酸系統` / `ATP再合成` / `肌酸激酶系統` / `磷酸肌酸能量穿梭` / `肌酸代謝` / `運動營養學`） | <!-- 待修修拍板 — 重做（用 v2 schema 重新 merge into main body）vs 維持 v1 schema 直到全本 v2 backfill；plan §8 Q2 --> |
| chapter source page (`Sources/Books/biochemistry-sport-exercise-2024/ch1.md`) | Step 3 上線後重 ingest ch1（含 figures + Vision describe） |
| Book entity (`Entities/Books/biochemistry-sport-exercise-2024.md`) | 保留；status 維持 `partial`、`chapters_ingested` 隨 ch2-ch11 ingest 累計 |

#### 4.3 lazy migrate trigger

- `kb_writer.read_concept_for_diff(slug)` 內部偵測 `schema_version: 1` 或 missing → in-memory migrate（不寫回）
- `kb_writer.upsert_concept_page(slug, action!="noop", ...)` 內部偵測 v1 → 寫回 v2 schema（含 LLM 一次性 merge 既有 `## 更新` block；merge 結果先寫 `.bak`）
- 全 vault 一次性 migrate：`python -m shared.kb_writer backfill_all_v1_pages --dry-run` → review → 拿掉 `--dry-run` 跑

#### 4.4 兼容期

過渡期內（Step 3 上線後 ~一季）允許 v1 / v2 page 共存：

- Reader path（`read_concept_for_diff`）統一回 v2 model
- Writer path（`upsert_concept_page`）一律寫 v2 schema
- v1 page 在第一次被 update 時 lazy migrate；無人觸碰的 v1 page 永久保留也無妨（Robin / kb-search 都 lazy migrate）
- 一季後跑一次 `backfill_all_v1_pages()` 收尾、移除 v1 schema 支援代碼

---

## 5. Risks & Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| 圖片 ingest 的 vault 容量膨脹 | Obsidian Sync 帶寬 / VPS disk 壓力 | (a) `Attachments/Books/` 容量監控（Franky weekly digest）；(b) 過大圖片下采樣（max 1920px 長邊、`pillow` resample）；(c) Phase 2 考慮 lazy fetch（vault 只存 thumbnail + 原檔在 R2） |
| Vision describe 失敗 / 不認識專業圖 | figure description 缺失 → retrieval 拿不到該圖內容 | (a) fallback `[FIGURE: alt-text only — manual annotation required]` placeholder + log warning；(b) `figures[].llm_description` 欄位允許 `null`，retrieval 看到 null 知道要降級；(c) 修修事後可手動補 description |
| `update_merge` LLM diff 把既有內容洗掉 | concept page 內容退化 / 丟失 | (a) 每次 update 前寫 `.bak` 到 `data/kb_backup/{slug}-{utc-ts}.md`，retain 24h；(b) round-trip test：write → read → update → read，frontmatter / body schema 不損失；(c) `update_merge` prompt 顯式要求「保留既有 body 內所有事實聲明，僅在新 source 補充新觀點時 merge in」 |
| Aliases-based dedup false positive（誤把不同 concept 當同一個） | 兩 concept 內容被混合進一頁 | (a) aliases 只接受 LLM 在 extract 時明確標記的同義詞；(b) 第一次 dedup 命中時要求 LLM 二次確認「這兩個 slug 真的指同一 concept 嗎」；(c) 修修事後可手動 split page |
| 過渡期 v1 / v2 schema 並存導致 reader 崩潰 | retrieval 拿錯資料 / Bridge UI 渲染壞 | (a) `read_concept_for_diff` 內部 schema_version dispatch；(b) Pydantic `extra="forbid"` 一發現未知欄位立刻 raise（不靜默吞）；(c) Phase 1 dry-run migrate report 全 review 後才正式 backfill |
| Vision LLM token cost 比預期高 | Max 200 quota 一週燒光 | (a) 一章 ingest 前估算 figure 數 × Vision token / figure → 顯示 cost projection 給修修確認；(b) `figures[].llm_description` 已存在則 skip Vision call（idempotent）；(c) Vision 選型可後續調（Opus → Sonnet）依實測效果 |
| `mathml2latex` / `pymupdf4llm` 對某些書 parse 失敗 | 該書無法 ingest | (a) 在 Step 1 parse 階段顯示給修修確認 figures / tables / equations 數量是否合理；(b) Manual override：`--toc-yaml` + `--figures-yaml` 修修手寫；(c) 失敗章單獨 hold 不阻塞其他章 |
| Schema migration 把 production memory 弄壞 | Robin / Chopper retrieval 暫時失效 | (a) `--dry-run` 全 vault report 必先 review；(b) backup 寫 R2 一份再跑；(c) migration script 跑前 freeze ingest（避免 race） |

**SPOF 識別**（per `reliability.md` §4）：

| SPOF | 影響 | 緩解 |
|---|---|---|
| `shared/kb_writer.py` 寫入失敗 | 所有 ingest 路徑停擺 | 落 DLQ（per `reliability.md` §8）+ Franky alert；修修手動重試 |
| Vision LLM 服務（Anthropic）長時間不可用 | textbook-ingest 不能跑 | Phase 1：等服務恢復；Phase 2 multi-provider 才有 fallback（Gemini Vision） |
| Obsidian Sync 故障 | vault 寫入後 VPS 看不到 | 既有風險（與 ADR-006b 同）；不在本 ADR scope |

---

## 6. Acceptance Criteria（v2 MVP）

按交付順序排，每項都 binary 可驗證：

- [ ] **Schema 落地**
  - [ ] `shared/schemas/kb.py` 含 `ConceptPageV2 / ChapterSourcePageV2 / FigureRef / ConceptAction / ConflictBlock / MigrationReport / migrate_v1_to_v2`
  - [ ] 全 schema `extra="forbid"` + `frozen=True`
  - [ ] schema unit test 通過 round-trip：`v2.model_dump_yaml() → yaml.load → ConceptPageV2.model_validate()` 不損失欄位
- [ ] **kb_writer module**
  - [ ] `shared/kb_writer.py` 暴露 §3.5.1 全 7 個 function
  - [ ] Round-trip test：write → read → update_merge → read，frontmatter / body schema 不損失（用 `磷酸肌酸系統.md` 為 fixture）
  - [ ] Idempotency test：同一 (slug, source_link) 連 ingest 3 次，`mentioned_in:` 不 double-append
  - [ ] `.bak` 機制 test：每次 `update_merge` / `update_conflict` 確認寫了 `data/kb_backup/{slug}-{ts}.md`
- [ ] **Pipeline 重接線**
  - [ ] `agents/robin/ingest.py:472-510` `_update_wiki_page` 改為呼叫 `kb_writer.upsert_concept_page()`；舊的 `## 更新` body append 邏輯刪除
  - [ ] `agents/robin/prompts/extract_concepts.md` 注入 candidate slug + 每個既有 page 的 aliases + 完整 body；prompt 顯式要求 4 種 action 之一
  - [ ] `.claude/skills/textbook-ingest/prompts/chapter-summary.md` 拿掉「每節 300-500 字」字數上限
  - [ ] `.claude/skills/textbook-ingest/scripts/parse_book.py:302-319` `_epub_html_to_text` 改寫 walker，處理 `<img>` `<table>` `<math>`
  - [ ] PDF 路徑改用 `pymupdf4llm.to_markdown(with_tables=True)`
- [ ] **重 ingest ch1（驗證 round-trip）**
  - [ ] 同一本 *Biochemistry for Sport and Exercise Metabolism* Ch.1 重 ingest 一輪
  - [ ] chapter source page (`Sources/Books/biochemistry-sport-exercise-2024/ch1.md`) frontmatter 含 `figures:` list、ch1 內所有 `<img>` `<table>` `<math>` 都有對應 entry
  - [ ] 每張圖在 `Attachments/Books/biochemistry-sport-exercise-2024/ch1/` 下；每張圖有 Vision-generated `llm_description`
  - [ ] 至少一個既有 concept page（推薦 `肌酸代謝`）展示 `## 文獻分歧 / Discussion` section，含 PCr 主導窗口時長範圍的跨 source diff（教科書 1-10s vs 既有 10-15s）
  - [ ] 4 個 ch1 新 concept (`能量連續體` / `糖解作用` / `有氧能量系統` / `無氧能量系統`) page 全 v2 schema
  - [ ] 6 個既有 concept page 全 v2 schema、body 末尾無 `## 更新（date）` block
- [ ] **Skill 一致性**
  - [ ] textbook-ingest skill 與 kb-ingest skill 寫出來的 concept page schema 100% 一致（同 `ConceptPageV2` model）
  - [ ] 兩條 path 寫同一 concept 不會產生 duplicate page
- [ ] **Observability**
  - [ ] 每筆 `upsert_concept_page` 寫 structured log（per `observability.md` §1）含 `operation_id` / `slug` / `action` / `pre_hash` / `post_hash`
  - [ ] Bridge `/bridge/robin` 加一塊「最近 50 筆 KB write」list（Phase 2 nice-to-have，可延）
- [ ] **Retrieval 不退化**
  - [ ] kb-search skill 對 ch1 範圍問題（eg.「PCr 主導時間多長？」）能拿到 `肌酸代謝` page 並命中 `## 文獻分歧` section（不是只拿 1-10s 或只拿 10-15s 的單一說法）

---

## 7. Out of Scope（明確不在 v2 內）

以下事項本 ADR 不 cover，Phase 2/v3 處理：

- **Multi-provider ingest**（Anthropic / OpenAI / Google AI 切換）— 維持 [ADR-010 Phase 2 §B2](ADR-010-textbook-ingest.md#b2-multi-provider-subscription-model-選擇) 計劃
- **Web UI 上傳介面**（Bridge Hub 加 textbook-ingest 入口）— 維持 [ADR-010 Phase 2 §B1](ADR-010-textbook-ingest.md#b1-網頁-ui-介面) 計劃
- **中文教科書 OCR**（簡 / 繁體掃描 PDF）— 敦促 v3 處理；目前修修書全 EPUB 走 primary path
- **Embedding / vector store**（不引入；維持 ADR-010 D-4 LLM ranking via kb-search）
- **動態 retrieval / re-ranking**（kb-search skill 已 production，不動）
- **Schema registry**（per `schemas.md` §10，schema 數量 <20 不上）
- **Vision 結果的 human-in-the-loop review UI**（Phase 2；目前修修事後可手動編輯 `figures[].llm_description`）

---

## 8. References

### v1 與設計文件

- [ADR-010: Textbook Ingest v1](ADR-010-textbook-ingest.md) — 此 ADR superseded
- [docs/plans/2026-04-26-ingest-v2-redesign-plan.md](../plans/2026-04-26-ingest-v2-redesign-plan.md) — 4 原則 + 12 audit findings + sequencing
- [docs/plans/2026-04-25-textbook-ingest-design.md](../plans/2026-04-25-textbook-ingest-design.md) — v1 設計過程
- [docs/plans/2026-04-25-textbook-ingest-decisions.md](../plans/2026-04-25-textbook-ingest-decisions.md) — v1 拍板過程

### 援引原則

- [docs/principles/schemas.md](../principles/schemas.md) — schema_version / extra="forbid" / Literal
- [docs/principles/reliability.md](../principles/reliability.md) — idempotency / DLQ / schema migration
- [docs/principles/observability.md](../principles/observability.md) — structured log / operation_id

### 哲學依據

- [Karpathy KB gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) — cross-source personal wiki / aggregator + open-question tracker
- [memory/claude/feedback_kb_concept_aggregator_principle.md](../../memory/claude/feedback_kb_concept_aggregator_principle.md) — concept page = aggregator 設計哲學
- [memory/claude/project_textbook_ingest_v2_design.md](../../memory/claude/project_textbook_ingest_v2_design.md) — v2 4 原則凍結 + 當前狀態
- [memory/claude/project_robin_aggregator_gap.md](../../memory/claude/project_robin_aggregator_gap.md) — Robin update 不是 aggregator + 已知 broken pages

### 即將動到的 critical 檔案（給 Step 3 implementer）

- `agents/robin/ingest.py:472-510` — `_update_wiki_page` 重寫為呼叫 `kb_writer.upsert_concept_page()`
- `agents/robin/ingest.py:298-339` — `_get_concept_plan` 加讀既有 body
- `agents/robin/prompts/extract_concepts.md` — 加 conflict detection
- `.claude/skills/textbook-ingest/scripts/parse_book.py:302-319` — `_epub_html_to_text` walker 改寫
- `.claude/skills/textbook-ingest/prompts/chapter-summary.md` — 拿掉字數上限
- `shared/obsidian_writer.py` — 加 `update_page` helper（kb_writer 內部用）；既有 `write_page` 已加 `width=10**9`（Step 1 hygiene PR）
- `shared/config.py:30-51` — env 順序 bug 已由 Step 1 hygiene PR 修
