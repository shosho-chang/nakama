# Robin 雙腦架構 + Ingest Skill 設計

**Status**: 📋 PROPOSAL 2026-06-17 — 設計定稿草案，未動程式。落地走 Claude Code worktree + PR（見 §6 P9 task prompt）。
**緣起**: 接續 [2026-06-17-agent-skillification-map.md](./2026-06-17-agent-skillification-map.md) 的 skill 盤點。修修澄清了一個關鍵架構意圖：**Robin 維護的 Wiki（他的理解）要跟我的 Permanent Note（我的理解）獨立**，他知道我的品味與我讀過的全文，但我不必知道他腦子裡的結構。本文件把這個「雙腦不對稱」架構定下來，並完整設計第一個要落地的 skill：`ingest`（route C 文章）。
**對照方法論**: vault `Inbox/web/Skill 製作最佳實踐 — 官方文件 vs 萬字長文 對照.md`
**程式碼依據**: `agents/robin/ingest.py`、`shared/literature_writer.py`、Centaur 規格 v0.2（`docs/plans/centaur-zettelkasten/`）

---

## 1. 雙腦不對稱架構（先框住，再談 skill）

### 1.1 兩個獨立的腦

|       | Robin 的腦                                  | 我（修修）的腦                          |
| ----- | ----------------------------------------- | -------------------------------- |
| 載體    | `KB/Wiki/`（Sources / Concepts / Entities） | `KB/Permanent/`（永久卡 + 卡間連結）      |
| 建構者   | Robin（從**全文** ingest）                     | 修修本人手寫                           |
| 證據來源  | 只能溯源 Raw（全文）+ Annotations（我的劃線）           | 我自己的判斷                           |
| 對方可否寫 | 我可讀可不讀；不必維護                               | **AI 絕不寫正文/status**（紅線 1）；只回填記帳欄 |

### 1.2 不對稱的方向（這是整個設計的靈魂）

- **我餵他**：全文 + 我的劃線/annotation + 我的品味 + 每次 ingest 的 accept/defer/exclude 判斷。
- **他餵我**：洞見、推薦讀物、以及「這張卡可能對應哪個 Concept」的**建議**（defer 標記）。
- **他向我看齊，我不必向他看齊**：Phase 5 規定 AI 只能在 Concept 頁加 defer 建議，**不得代我在 Permanent 側建立連結**（Centaur 規格 v0.2 §8）。所以他永遠不污染我的結構，我也不必去看他腦中那團 Wiki。

### 1.3 一個要鎖死的決定：ingest 全文，不要只 ingest 劃線

**Robin 必須 ingest 整份文件，劃線只是疊在上面的「強調訊號」。** 理由：若他只 ingest 我劃線處，他的 Wiki 就只是我注意力的鏡子，**永遠無法告訴我「我漏看的東西」**——那「給我意想不到的洞見」就破功了。

- 現況（已正確）：`ingest.py:_generate_summary(content=...)` 吃整份原文 body（註解明寫 ADR-011 P2「不省 token、deep extract」、pass-through 不截斷）。Source Summary 與 Concept 抽取都從全文走；Phase 1 的 Literature Note 才是從 annotation render（`_render_literature` → `KB/Literature/{slug}`）。
- **小缺口（待補的增強）**：目前 concept 抽取只吃「全文摘要 + 我打字的 guidance」，**還沒把 annotation set 當 emphasis signal 自動餵進去**（劃線目前只反向用在 Literature 的「🔗 KB 相關」FTS5）。把「我的劃線」當強調訊號餵進 concept 抽取，是符合本架構的小增強（見 §5 設計）。

---

## 2. Skill 家族（雙腦各自長出的 skill）

分兩族：「Robin 自己的腦」（他的自治）與「服務我的橋」（他向我看齊）。社群 `AgriciDaniel/claude-obsidian` 把「他的腦」那族骨架幾乎都給了；「橋」那族吃**我的品味**，是 Nakama 原創護城河。

| Skill                        | 做什麼                                  | 屬於                   | 社群可借                              | 優先             |
| ---------------------------- | ------------------------------------ | -------------------- | --------------------------------- | -------------- |
| **`ingest`**                 | 全文 + 劃線 → Robin Wiki                 | 他的腦                  | `wiki-ingest` 骨架                  | **P0（本文件設計）**  |
| **`kb-lint`**                | 孤兒/矛盾/過時/重複，維護他的腦                    | 他的腦                  | `wiki-lint`                       | P1             |
| **`kb-query`**               | 問 Wiki、回帶引用答案                        | 他的腦                  | `wiki-query`（已有 kb-search 雛形）     | P1             |
| **`gap-scout`**              | 掃自己 Wiki 缺漏 → 上網/PubMed 找新文獻補洞       | 他的腦                  | `autoresearch` 幾乎直用 + Zoro PubMed | P1             |
| **`insight`**                | 主動挖「我沒想到的連結」推給我                      | **橋**                | 原創（吃我的品味）                         | P2             |
| **`recommend`**              | 依我品味 + 他的缺漏 + 新發現，排我下一步讀什麼           | **橋**                | 原創                                | P2             |
| **`daily-review` 開卡 assist** | 建議卡名 + typed-edge 候選，我寫永久卡           | **橋**（我的腦，他只 assist） | Centaur 已有                        | P1             |
| **`taste-profile`**          | 從劃線 + 永久卡 + 每次判斷建我的品味模型，餵給上面所有 skill | 引擎                   | 原創（= taste loop 的 style 層）        | P1（跨 skill 基建） |

**複利飛輪**：`gap-scout` + `recommend` 把 Zoro（Scout）接上 Robin（KB）——他發現自己知識有洞 → 自動找文獻 → 經我品味過濾 → 推薦給我讀 → 我讀完劃線 → `ingest` 回他的腦。每一圈都讓他更懂我。

> 本文件只完整設計 `ingest`；其餘照同骨架後補。

---

## 3. `ingest` skill 設計（route C 文章，thin wrapper）

### 3.1 架構決定

- **Thin wrapper 包現有 `IngestPipeline`**（`agents/robin/ingest.py`）。skill 不重寫 pipeline，只當「對話觸發 + HITL gate + taste 訊號擷取」的薄層；確定性（錨點、idempotent render、index 更新、紅線 tripwire）全留 Python。對齊 `seo-audit-post`（「You do NOT re-implement the pipeline. You shell out…」）。
- **一個 skill、source_kind 分支**（官方 domain-organization Pattern 2）：`SKILL.md` 當 router，`references/route-c-article.md` / `route-b-book.md` / `route-e-video.md` 各放分支，共用 Phase 2 引擎。先做 route C。
- **四件套 + taste loop**：`GOTCHAS.md` / `examples/` / `style/` / `evals/`。

### 3.2 目錄

```
.claude/skills/ingest/
├── SKILL.md                      # router + route C 主流程（< 300 行）
├── GOTCHAS.md                    # 紅線 + route C 專屬坑（每條附 why）
├── references/
│   ├── route-c-article.md        # 文章/論文分支（^p-N 錨、單次 ingest）
│   ├── route-b-book.md           # （後補）章分組 + re-ingest + digest
│   └── route-e-video.md          # （後補）時間軸 + 講者
├── examples/                     # 修修 accept 的高品質抽取（few-shot）
├── style/
│   └── taste-profile.md          # 命名/邊界/取捨偏好（taste loop style 層）
└── evals/
    └── evals.json                # should-trigger / should-not-trigger
```

### 3.3 SKILL.md 草稿

```markdown
---
name: ingest
description: >
  Ingest a finished reading into Robin's KB wiki. Renders the reader's annotation
  set into a human-readable Literature Note (KB/Literature/{slug}), then compiles
  Source → Entity → Concept wiki pages from the FULL source text (Karpathy loop,
  order-locked) and updates index/log. The annotation set is an EMPHASIS signal
  layered on the full text — never a content filter. Use when the user finishes an
  article/paper and presses Ingest or says 「ingest 這篇」「把這篇讀完的入庫」
  「/ingest <slug>」「這篇 KB 化」「幫我把這篇收進知識庫」. Do NOT use for: 開永久卡或
  寫永久卡之間的連結（走每日回顧，AI 不可代筆）; KB 查詢/找答案（用 kb-search）;
  書或影片逐字稿（route B/E 未上線，先擋並告知）; 重寫/潤稿文章（不是 ingest）.
---

# ingest — 把讀完的文章編進 Robin 的 KB Wiki（route C）

你是 Nakama `IngestPipeline`（`agents/robin/ingest.py`）的對話介面。你的工作是
驅動 pipeline、守住 HITL gate、擷取品味訊號。**你不重寫 pipeline**——確定性邏輯
（Literature render、錨點、index 更新、紅線 tripwire）都在 Python，你呼叫它。

## 何時用
觸發：修修讀完文章按 Ingest，或說「ingest 這篇 / 這篇 KB 化 / 收進知識庫 / /ingest <slug>」。
不要用於：開永久卡（每日回顧，AI 不代筆）、KB 查詢（kb-search）、書/影片（route B/E 未上線）、潤稿。

## 雙腦原則（出手前讀一次）
- 你維護的是**你自己的** Wiki（Sources/Concepts/Entities），跟修修的 Permanent Note 獨立。
- **ingest 全文**；修修的劃線是「他在意這裡」的強調訊號，**不是內容過濾器**。只 ingest 劃線
  會讓你的 Wiki 變成他注意力的鏡子，就無法給他「他漏看的洞見」。
- 你絕不寫 `KB/Permanent/`。對「這 Concept 對應哪張永久卡」只在 Concept 頁加 defer 建議。

## 流程（兩 Phase，HITL 卡中間）
1. 確認 route：文章/論文 → route C。書/影片 → 告知未上線、停。讀 `references/route-c-article.md`。
2. **Phase 1**：呼叫 `IngestPipeline.ingest(annotation_slug=<slug>, source_type="article", interactive=True)`，
   先 render Literature Note（`KB/Literature/{slug}`，`^p-N` 錨，idempotent）。
3. **Phase 2（順序鎖）**：Source Summary（全文）→ Entities → Concept plan
   （create / update_merge / update_conflict / noop）。
4. **🚦 HITL gate**：把 concept/entity plan 列給修修，**停下來等 accept / defer / exclude**。
   不可自動放行。先讀 `GOTCHAS.md` 確認沒踩紅線。
5. 修修點頭 → pipeline 寫 `KB/Wiki/Concepts`、`Entities` → 更新 `index.md` + `log.md`。
6. **擷取品味訊號**（taste loop）：把修修的 accept/defer/exclude + 一句理由記下來，
   依 `style/taste-profile.md` 規則分流（見下）。

## Taste loop（每次 ingest 讓你更懂他）
- reject/exclude + 為什麼 → 追加 `GOTCHAS.md`（附 why，例：「不要把純方法名抽成 Concept」）。
- accept 的高品質抽取 → 存 `examples/`（few-shot）。
- 命名/粒度/邊界偏好 → 更新 `style/taste-profile.md`。
- 該觸發沒觸發 / 誤觸發 → 補 `evals/evals.json`。
- promote 進 store **由修修點頭**；raw 判斷先記，蒸餾再進。

## 細節
route C 文章分支見 `references/route-c-article.md`；紅線與坑見 `GOTCHAS.md`。
```

### 3.4 GOTCHAS.md 草稿（每條附 why）

```markdown
# ingest gotchas（route C）

## 五條紅線（Centaur 規格 v0.2 §7，有 tripwire）
- 絕不寫 `KB/Permanent/` 正文與 status。why：那是修修的腦，品味只有他驗收；
  唯一入口 `update_permanent_bookkeeping()` 白名單 key。
- 每個事實宣稱附 citation，溯源回 Raw / Annotation 錨點。why：防捏造。
- Concept 可寫可 merge，但不冒充永久卡（`author` 欄必填 agent_robin）。why：provenance 分離。
- ingest 不建 MOC。why：MOC 等修修的擠壓點，建不建是人決定。
- Concept/Output 終端證據只能是 Sources/Raw/Annotations，**不得以另一個 Concept/Output 當事實來源**。
  why：防 citation laundering / wiki 自我餵食。

## route C 專屬坑
- ingest **全文**，不要只抽劃線段。why：只抽劃線＝注意力鏡子，給不了漏看的洞見。
- `^p-N` 段落錨必須是 render-time 1-based 序位的 exact-copy。why：Literature 與 Wiki 對得起來、
  re-render byte-identical（規格 §9）。
- re-ingest 要 idempotent：保留 frontmatter 記帳欄（`mined_concepts`/`status`）與「✓ 已開卡」標記，
  只重畫 render 區。why：書/文章分多天讀，re-ingest 是常態，不能洗掉記帳。
- 寧可 update 既有 Concept，不要狂建新頁。why：CLAUDE.md 禁止 page explosion。
- 不要自動把 Concept 連到修修的 Permanent 卡，只加 defer 建議。why：不污染他的結構（規格 §8）。
```

### 3.5 evals/evals.json 草稿

```json
{
  "skill_name": "ingest",
  "evals": [
    {"id": 1, "prompt": "我把《卡片盒筆記》第三章那篇讀完了，幫我 ingest 進 KB", "should_trigger": true},
    {"id": 2, "prompt": "/ingest atomic-habits-ch1", "should_trigger": true},
    {"id": 3, "prompt": "這篇 PubMed 文章我劃完線了，收進知識庫", "should_trigger": true},
    {"id": 4, "prompt": "把剛剛那篇文章 KB 化，重點放在 CBT-I 那段", "should_trigger": true},
    {"id": 5, "prompt": "讀完這篇文章，幫我入庫", "should_trigger": true},
    {"id": 6, "prompt": "KB 裡面有沒有講到間歇性斷食的概念？", "should_trigger": false, "note": "→ kb-search"},
    {"id": 7, "prompt": "幫我把這個 Concept 開成一張永久卡並連到 X 卡", "should_trigger": false, "note": "→ 每日回顧, AI 不代筆"},
    {"id": 8, "prompt": "這篇文章幫我潤稿、改成適合 FB 的版本", "should_trigger": false, "note": "→ Brook 重組"},
    {"id": 9, "prompt": "幫我 ingest 這個 YouTube 影片逐字稿", "should_trigger": false, "note": "→ route E 未上線, 先擋"},
    {"id": 10, "prompt": "幫我把這本 epub 整本 ingest", "should_trigger": false, "note": "→ route B 走每日迴圈, 先擋"}
  ]
}
```

> 反例 6–10 都是「近似誤觸」（共享關鍵字但該走別處），才測得出 description 邊界。

---

## 4. 待補的小增強：annotation 當 emphasis signal

現況 `_get_concept_plan(summary_body, summary_path, user_guidance, content_nature)` 沒吃 annotation set。
增強：route C ingest 時，把該 slug 的 annotation（劃線 + 筆記）摘要成一段「修修強調了這些」，
注入 concept 抽取 prompt 當**加權訊號**（不是內容邊界）。效果：Robin 仍讀全文，但更知道修修在意什麼，
抽出的 Concept 更貼品味。實作落點：`ingest.py` Phase 2 前讀 `KB/Annotations/{slug}`，傳進 `_get_concept_plan`。

---

## 5. Taste loop 落點（接既有基建）

- 行為層 gotchas → `memory/claude/feedback_*.md`（既有 184 條庫）。
- skill-local 領域 gotchas → `.claude/skills/ingest/GOTCHAS.md`。
- few-shot → `examples/`（仿 Foundry `edit_log → examples`）。
- 品味模型 → `style/taste-profile.md`，未來升格成 `taste-profile` skill（跨 Robin skill 共用）。
- 蒸餾引擎 → 掛 `shared/memory_maintenance`（新增 `distill` 子命令，把 raw 判斷蒸餾進上述 store）。

---

## 6. 落地：給 Claude Code 的 P9 task prompt

> 在 sibling worktree 執行（主倉庫是 control plane）。

**1. 目標**：把 route C 文章 ingest 包成 `.claude/skills/ingest/` skill（thin wrapper over `IngestPipeline`），含四件套，不改 pipeline 行為。

**2. 範圍**：新增 `.claude/skills/ingest/{SKILL.md,GOTCHAS.md,references/route-c-article.md,style/taste-profile.md,evals/evals.json}` 與 `examples/.gitkeep`。**可選**第二 PR 做 §4 annotation-emphasis 增強（動 `agents/robin/ingest.py` + prompt + 測試）。

**3. 輸入**：本設計 doc；`agents/robin/ingest.py`、`shared/literature_writer.py`、Centaur 規格 v0.2 §2/§7/§9；對照筆記方法論。

**4. 輸出**：可被 Claude 觸發的 ingest skill + evals；route C 一篇真實文章 pilot run 紀錄。

**5. 驗收**：(a) evals 10 題觸發/不觸發符合預期；(b) 走一篇真實文章端到端，產出 Literature + Source + Concept plan，HITL gate 有停；(c) 既有 tripwire（紅線、route C self-feeding）全綠；(d) SKILL.md < 300 行、引用一層深、gotchas 附 why。

**6. 邊界**：不改 pipeline 對外行為（除非做 §4 增強，且需新增測試）；不碰 `KB/Permanent/` 寫入路徑；不建 MOC；skill 寫法照 `seo-audit-post` thin-wrapper 慣例。

---

## 7. 今天可做（不必等落地）

ingest skill 不必 commit 進 repo 才能跑——`IngestPipeline` 已在。今天可：
1. 我扮演 Robin，照本 SKILL.md 草稿對你的讀書筆記走一次 dry-run（concept plan 那關停給你審）；或
2. 等你把材料放進 `Inbox/web/` 後直接開跑。
```

---

## 8. 家族其餘 7 個 skill 的設計（spec 級，供 Panel review）

> `ingest` 已 ship-ready（§3）。以下 7 個是 spec 級設計：description 草稿 + 流程 + verdict 落點 + 紅線 + 依賴 + 社群可借 + 狀態。**狀態欄區分「design-only / 等資料」**——半數要先有被 ingest 填過的 Wiki 才驗得起來（見 §9）。

### 8.1 `kb-query`（他的腦 · 查詢→合成）
- **一句話**：問 Robin 的 Wiki，回**帶引用**的綜合答案，必要時存回 `KB/Wiki/Outputs/`。
- **description（草稿）**：`Answer a question from Robin's KB wiki with citations. Reads index → relevant pages → synthesizes, clearly separating fact / judgment / inference / open uncertainty; offers to save high-value answers to KB/Wiki/Outputs. Use for「KB 裡有沒有講到 X」「綜合一下我讀過的關於 Y」「KB 怎麼說 Z」. Do NOT use for: 純關鍵字查檔（用 kb-search）、ingest、開永久卡.`
- **流程**：thin wrapper over `kb-search`（FTS5 + `scripts/search.py`）→ 讀 3–5 頁 → 合成 → 區分事實/判斷/推論 → 問是否存 Output。
- **verdict → taste loop**：修修 accept/reject 答案結構與「值不值得存 Output」→ examples（好答案範式）/ gotchas（別把推論寫成事實）。
- **紅線**：Output 終端證據只能 Sources/Raw/Annotations，**不得 cite 另一個 Concept/Output**（規格 §7.5）。
- **社群可借**：`wiki-query`（read index→pages→synthesize、context discipline）。**狀態：等資料**（Wiki 要先有量）。

### 8.2 `kb-lint`（他的腦 · 健康檢查）
- **一句話**：掃 Wiki 的孤兒 / 斷鏈 / 矛盾 / 重複 / 過時 / 缺引用，列清單給修修決定要不要修。
- **description（草稿）**：`Health-check Robin's KB wiki: orphans, dead links, duplicate concepts, contradictions, stale claims, missing citations. Produces a prioritized findings list; does NOT auto-fix. Use for「KB 體檢」「lint 一下知識庫」「有沒有孤兒卡/斷鏈」. Do NOT use for: ingest、查詢內容（kb-query）、重構（要人點頭）.`
- **流程**：確定性檢查（孤兒/斷鏈/重複）走 script；矛盾/過時走 LLM；**只報不修**（refactor 是人決定）。
- **verdict → taste loop**：哪些 finding 修修認為是真問題 → 調 lint 規則閾值（style）/ 收 gotchas（什麼不算問題）。
- **社群可借**：`wiki-lint` 8-category 直接可借。**狀態：等資料**。

### 8.3 `gap-scout`（他的腦 · 自我擴充 / autoresearch）
- **一句話**：掃自己 Wiki 的薄弱/缺漏主題 → 上網 + PubMed/arXiv 找新文獻 → **推薦給修修讀**（寫 `Inbox/` 或 `Watchlist/`，**不自動 ingest**）。
- **description（草稿）**：`Scan Robin's wiki for thin or stale topics, run a bounded web/PubMed/arXiv research loop to find new literature filling those gaps, and queue recommendations for the user to read. Writes to Inbox/Watchlist only — never auto-ingests into KB. Use for「Robin 你看看自己哪裡知識有洞」「找新文獻補 X 主題」「最近有什麼該讀的」. Do NOT use for: ingest（人讀完才 ingest）、直接寫 KB.`
- **流程**：gap 分析（讀 Wiki + lint）→ research 迴圈（`shared/pubmed_client.py`、`shared/arxiv_client.py` + WebSearch）→ 候選經 taste-profile 過濾 → 寫推薦佇列。
- **verdict → taste loop**：修修對推薦的「讀/不讀/已知」→ **最強的 taste 訊號**（直接教他什麼該推給我）→ taste-profile + gotchas（別推某類）。
- **紅線**：web 找到的東西**人讀完劃線才 ingest**，AI 不從 web 直接寫 KB（防 wiki 自我餵食外擴）。
- **社群可借**：`autoresearch`（可設定的 `references/program.md`：偏好來源、信心評分、max rounds）幾乎直用——改成「prefer PubMed」即合 longevity 領域。**狀態：等資料 + 需 taste-profile**。

### 8.4 `insight`（橋 · 浮現連結）
- **一句話**：主動從 Robin 的 Wiki 挖「修修沒想到的連結/洞見」，尤其橋接到他在意的主題，推給他。
- **description（草稿）**：`Proactively surface non-obvious connections and insights from Robin's wiki that the user likely hasn't made — especially bridging to topics they care about (from annotations + permanent notes). Presents as suggestions, never writes the user's Permanent notes. Use for「給我一些我沒想到的連結」「最近 KB 有冒出什麼洞見」. Do NOT use for: 查已知答案（kb-query）、代寫永久卡.`
- **流程**：取 taste-profile 的「在意主題」→ 在 Wiki 找跨領域/反直覺連結 → 產 insight 卡（含證據連結）→ 推到 insight 佇列；**只建議，不寫 Permanent**（規格 §8）。
- **verdict → taste loop**：修修對 insight 的「有用/沒感覺/已知」→ taste-profile（什麼算好洞見）。
- **社群可借**：無直接對應（原創）。**狀態：等資料 + 需 taste-profile**。

### 8.5 `recommend`（橋 · 薦讀排序）
- **一句話**：把 `gap-scout` 找到的 + 既有未讀的，依修修品味 + 他知識缺漏排序，給「下一步讀什麼」。
- **description（草稿）**：`Rank what the user should read next, combining gap-scout findings, unread queue, the user's taste profile, and Robin's wiki gaps. Use for「我接下來該讀什麼」「排一下待讀」「這幾篇哪篇先讀」. Do NOT use for: 找新文獻（gap-scout）、ingest.`
- **流程**：聚合候選 → taste-profile 評分 → 排序 + 理由 → 寫薦讀清單。可與 `gap-scout` 合併，但 verdict 面不同（薦讀的選/不選）。
- **verdict → taste loop**：選讀順序 → taste-profile。**狀態：等資料 + 需 taste-profile**。

### 8.6 `daily-review-assist`（橋 · 開卡協助，Centaur 已有）
- **一句話**：每日回顧開卡時，建議卡名 + typed-edge 候選 chips，修修寫永久卡正文。
- **流程**：已是 Centaur N522/N523 的能力（`agents/robin/daily_review.py` + Web UI）；skill 化只是把這條對話介面正式化。**AI 絕不寫 Permanent 正文**（紅線 1）；只給建議。
- **verdict → taste loop**：修修採不採納卡名/連結建議 → taste-profile（命名、連結品味）。**狀態：已有實作，低優先 skill 化**。

### 8.7 `taste-profile`（引擎 · 跨 skill 基建）
- **一句話**：從劃線 + 永久卡 + 每次 ingest/推薦/insight 的判斷，建並維護「修修品味模型」，供上面所有 skill 載入。
- **流程**：= taste loop 的 `style/` 層升格成跨 skill 共用。資料來源：annotations、Permanent、各 skill 的 verdict log。產出：一份結構化 `taste-profile.md`（在意主題、命名偏好、什麼算好洞見/好答案、negative 邊界）。掛 `shared/memory_maintenance` 的 `distill` 蒸餾。
- **紅線**：區分用途——「品質判斷/選題/批評」走專家視角不 mimic 過去文章（`feedback_expert_persona_over_style_mimic`）；「語氣一致」才學樣本。
- **狀態：基建，可先 stub**（一份會 accrete 的 md），ingest 不必等它完成就能 ship。

---

## 9. 落地策略：設計全部，但分波落地（建議）

你提的流程（家族全設計 → Claude Code + Panel review → 落地 → 你做正式 ingest）方向對，但**「一次落地全部 8 個」我建議改成分波**，理由有三、且兩條是硬的：

1. **一半的 skill「等資料」才驗得起來（硬）**：`insight` / `gap-scout` / `recommend` / `kb-query` 都需要一個**被 ingest 填過的 Wiki**才有東西可挖、可查、可補洞。Wiki 現在 Concepts/Entities 是 0。先 ship `ingest`、你跑幾篇真實 ingest 把 Wiki 養起來，這幾個才有 ground truth 可 review。
2. **符合你自己的紀律（硬）**：`feedback_iterative_playbook_refinement`「偏好 run 過實際輸出再修 playbook、不接受 preemptive theoretical rewrite」；對照筆記方法論也說「先做交集分數最高的，不要一次鋪一堆」「每個 skill 是一種稅」。一次 land 8 個＝8 份未經真實 run 的 playbook。
3. **Panel 該 review 的是架構，不是 8 份 SKILL.md 細節**：`feedback_grill_then_panel_for_big_adr` — panel 用在大 ADR 的決策矩陣。這裡真正該被 panel 拍的是**雙腦架構 + 家族邊界 + 紅線**（§1–2），那是「大 ADR」級；個別 SKILL.md 是 P7 執行級，跑過再修。

**建議波次：**

| 波      | 落地                                             | 先決                        | 你做的事                   |
| ------ | ---------------------------------------------- | ------------------------- | ---------------------- |
| Wave 0 | Panel review **架構**（§1–2 雙腦 + 家族 + 紅線）→ 升 ADR  | —                         | 拍板架構                   |
| Wave 1 | `ingest`（route C）                              | Wave 0                    | **你做正式 ingest，養 Wiki** |
| Wave 2 | `kb-lint` + `kb-query` + `taste-profile`(stub) | Wave 1 跑過幾篇               | 用真實 Wiki 驗             |
| Wave 3 | `gap-scout` + `recommend` + `insight`          | Wave 2 + taste-profile 有料 | 驗複利飛輪                  |

**給 Claude Code 的交接**：把本 doc §1–2 送 Panel 做架構 review（升 ADR）；§3 的 `ingest` P9（§6）直接進 Wave 1 worktree 落地；§8 其餘維持 spec、待 Wave 2/3。這樣你今天就能在 Wave 1 ship 後做第一次正式 ingest，而不是等 8 個全做完。

> 若你仍想一次 land 全部，我也可以把 §8 每個都展開成完整 SKILL.md + evals——只是那會是「未驗證的 8 份 playbook」，跟你過去的偏好相左，故先建議分波。

---

## 10. Permanent 成長、自有產出回流、KB 邊界（2026-06-17 追加）

### 10.1 Robin ↔ Permanent 契約：只「記帳 + 建議」，不 authoring
程式碼確認 `shared/permanent_layer.py:37`：`ALLOWED_BOOKKEEPING_KEYS = {source_refs, modified, aliases}`，`update_permanent_bookkeeping()` 是唯一寫入口，docstring 明寫「絕不碰正文、status、或任何其他 key」。Robin 在你的網裡只做兩件事：
- **記帳**（白名單三欄）：`source_refs`（來源回指 + 錨點）、`modified`、`aliases`（跨語檢索）。
- **建議**（不落你的卡）：開卡時的卡名/typed-edge 候選 chips（§5）、Phase 5 沿你親手連結補 backlink + Concept 頁 defer 建議（§8）、MOC 的 `%%agent-robin-unfiled%%` + 孤兒標記（§10）、insight/recommend。
- **不碰**：正文、status、你的卡間連結。

### 10.2 新橋 skill：`gather`（per-topic 寫作前組裝）
- **入口**：你說「我要寫關於 X」→ Robin 跨 **Permanent 網 + 他的 Wiki** 撈相關卡 / Concept / Source / Entity +（若有）該主題 MOC + 證據叢集 + 問題清單 + outline skeleton，**攤出來排給你看，不代筆**（ADR-024 紅線）。
- **地基**：擴自 per-reading `agents/robin/reading_context_package.py`（純確定性聚合、不代筆）+ `thousand_sunny/routers/writing_assist.py`，升成 **per-topic**（現況 RCP 只針對「一份來源」，缺跨主題那層）。
- 你**不必先去 MOC**：MOC 是你手 curate 的可選導覽；`gather` 是隨選的跨層組裝入口。

### 10.3 Permanent 成長怎麼讓 Robin 同步：靠證據，不靠監看
- Robin **不監看你的 Permanent 卡**（那是你的工作面，且會違反「不讀 Permanent 正文」）。
- 你的卡會長大，通常是因為你**讀了更多** → 那些來源 ingest 進來時，他的 Wiki 自然長出對應 Concept。卡的 `source_refs`（他幫你記帳的那欄）就是「這張卡背後是哪些證據」的索引。
- 結論：**成長期不需要逐次通知他**；同步發生在你 ingest 新讀物的那個 checkpoint，不是連續監看。

### 10.4 自有產出回流：route O（own-output）
- 成熟的 Permanent → 部落格/影片腳本，或每週電子報 = **完成的思考結晶**，終點是一個 discrete 產出。
- 用**顯式 ingest** 把它當一個新 source 類型 `own-output`（`provenance: self`）收進來：Raw → Source Summary → Concept，跟外部文章同骨架，但標記「這是修修自己的綜整」。
- **firewall（要 Panel 拍）**：red line 5 防 wiki 自我餵食——若電子報本來就用 Robin 的 Concept 寫的，再 ingest 回去會循環。對策：own-output source 標 provenance、**不得當「獨立外部證據」單獨確認它自己衍生的 Concept**。這是 route O 的開放設計題。
- 這條就是 **Stage 7 → Stage 1 的複利飛輪**：你的產出回灌成知識。

### 10.5 KB 邊界：Robin 收什麼、Nami 收什麼
- **判準一句話**：這東西會不會 compound 成可複用的**領域知識**？
- **進 KB（Robin = 知識管理者）**：讀物（route B/C/E）、你的思考結晶/產出（route O）、領域 fleeting idea。
- **不進 KB（Nami = 秘書 / TaskNotes）**：會議紀錄、報價、行政、交易型雜務——操作型、不 compound。**Robin 不管，Nami 管。** 對齊 ADR-001（Robin = Knowledge Base、Nami = Secretary 含邀約報價）。

### 家族新增成員（接 §2 表）
| Skill            | 做什麼                                      | 屬於      | 狀態                                     |
| ---------------- | ---------------------------------------- | ------- | -------------------------------------- |
| `gather`         | per-topic 寫作前跨層組裝（Permanent + Wiki）      | 橋       | 有地基（RCP + writing_assist），待擴 per-topic |
| `ingest` route O | 自有產出（電子報/腳本/成熟卡）回流成 self-authored source | 他的腦（入口） | 待設計；firewall 要 Panel 拍                 |
