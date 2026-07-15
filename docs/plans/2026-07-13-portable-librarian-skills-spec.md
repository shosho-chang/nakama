# 可攜版 Librarian Skills 規格 — `/ingest` + `/draft-article`（v2，補來源地景）

> 目標：把「劃線註記 → Literature Note → KB → 文章骨架」流程脫離 Nakama 系統，
> 做成**只用 Obsidian + Claude Code（Cowork）就能跑**的兩個 skill，打包成 plugin。
> 本文是功能與邏輯規格（P9 產物），不是實作。
>
> v2 變更（2026-07-13）：修修指出 v1 沒把「其他使用者的材料來源」查清楚就設計輸入契約。
> v1 假設「全文 + inline 劃線」，但實查六種 capture 工具後，**Kobo / Snipd 落進 vault 的
> 檔案只有劃線集、沒有全文** —— ingest 必須支援兩種輸入形狀。§0.5 是新增的事實基礎，
> §1 據此重寫。
>
> 事實來源（repo 內）：
> - 正牌 route C：[.claude/skills/ingest/SKILL.md](../../.claude/skills/ingest/SKILL.md)、
>   [GOTCHAS.md](../../.claude/skills/ingest/GOTCHAS.md)、
>   [references/route-c-article.md](../../.claude/skills/ingest/references/route-c-article.md)、
>   [scripts/ingest_steps.py](../../.claude/skills/ingest/scripts/ingest_steps.py)
> - 可攜雛形：`C:\Users\Shosho\.claude\skills\kb-ingest\SKILL.md`（早期抽出版，零系統依賴）
> - draft-article 原版：[.claude/skills/draft-article/SKILL.md](../../.claude/skills/draft-article/SKILL.md)
>   （系統依賴僅 `shared.config.get_vault_path()` 一行；`lint_draft.py` 純 stdlib）
> - Annotation 格式：[ADR-017](../../docs/decisions/ADR-017-annotation-kb-integration.md)

---

## 0. 設計前提（兩個 skill 共用）

| 前提 | 內容 | 對照系統版 |
|---|---|---|
| 執行環境 | Claude Code / Cowork，**cwd = vault root**。找不到 `KB/` 結構 → 問使用者 vault 在哪，絕不猜 | `shared.config.get_vault_path()` |
| 狀態儲存 | 一切狀態都是 vault 內 markdown（資料夾即 schema），無 DB、無 HTTP、無 Python pipeline | `agents/robin/ingest.py` + state.db |
| Vault 佈局 | 見下表；skill 首次執行時不存在就建 | `docs/VAULT-LAYOUT.md`（ADR-028）的子集 |
| 語言 | 頁面正文繁體中文、frontmatter key 英文、專有名詞保留原文 | 同 |

### 可攜版 vault 佈局（系統版的相容子集）

```
Inbox/                      # capture 落點（Clipper / Snipd / Kobo 匯入 / YT 筆記）
KB/
├── Literature/{slug}.md    # 劃線註記的統一 render（/ingest Phase 1 產出）
├── Wiki/
│   ├── Sources/{slug}.md   # 來源摘要（draft / candidate 層）
│   ├── Concepts/{slug}.md
│   └── Entities/{slug}.md
├── index.md                # 每次寫頁後同步更新
├── log.md                  # append-only 操作紀錄
└── taste-profile.md        # HITL defer/exclude 的品味觀察（raw 區）
Drafts/                     # /draft-article 產出（非 KB，單向 provenance）
```

沿用系統版資料夾名的理由：修修自己的 vault 不用改就能跑同一套 plugin；教學文件對外也用同一套名詞。

### 使用者手寫區保護（紅線的可攜版）

- skill **只寫** `KB/Literature/`、`KB/Wiki/`、`KB/index.md`、`KB/log.md`、`KB/taste-profile.md`、`Drafts/`。
- vault 內任何其他資料夾（含 `KB/Permanent/`、`Journals/`、使用者自建目錄）一律唯讀。
- 系統版靠 `assert_not_permanent_target()` tripwire；可攜版**降級為 SKILL.md 內的硬邊界宣告 + 交稿前 checklist 自審**——弱化點明列於 §1.3 差異表。

---

## 0.5 來源地景 — 其他使用者的材料實際長什麼樣（v2 新增，逐一查證）

修修的系統：Reader 內有**完整原文**（EPUB 匯入 / News Coo 抽全文），劃線註記存
`KB/Annotations/{slug}.md` 分離 store。**其他使用者沒有這個** —— 他們的材料是各 capture
工具落進 vault 的檔案，形狀分兩種：

### 六種 capture 輸出的實查結果

| # | 來源 × 工具 | 落進 vault 的檔案 | 有全文？ | 劃線格式 | 定位資訊 |
|---|---|---|---|---|---|
| 1 | 文章 × Obsidian Clipper（inline highlighting 模式） | 整篇文章 markdown，劃線以 `==highlight==` inline 嵌在原文裡 | ✅ | `==...==` inline | 段落位置天然存在 |
| 2 | 文章 × Clipper（highlights-only 模式） | 只有劃線清單，「without any of the page content」 | ❌ | 清單項 | 無（只有出現順序） |
| 3 | YouTube × YTranscript（insert into note） | **整篇字幕**插進筆記、帶可點 timestamp（預設每 32 行一個，可調）；使用者在字幕上劃 `==...==` | ✅ | `==...==` inline | timestamp |
| 4 | YouTube/影音 × Media Extended timestamp notes | 使用者手打筆記 + `#t=MM:SS` 格式 timestamp 連結（W3C Media Fragments 語法）；無字幕全文 | ❌ | 手寫筆記本身 | `#t=` timestamp 連結 |
| 5 | Podcast × Snipd 官方 plugin | **一集一檔**；每個 snip = 該片段逐字稿節錄 + AI 摘要 + 個人註 + 音檔跳轉連結 + episode metadata。**不含全集逐字稿**。episode/snip 兩層 template 可自訂 | ❌ | snip block | 音檔 timestamp 連結 |
| 6 | Kobo × 內建 export（商店書/Kobo Plus）或 Kobo Highlights Importer plugin（sideload 書） | 內建：highlights + notes 檔（md/txt/html/pdf，經 Kobo.com 下載，30 天期限）。plugin：frontmatter（書名/作者/ISBN/進度）+ 依章節分組的劃線 blockquote + 註 + 時間戳，Eta.js template 可自訂。**都不含書的全文** | ❌ | blockquote 清單 | 章節（plugin 版）；內建版無保證 |

（工具能力查證來源：Obsidian Clipper [Highlighter 官方文件](https://obsidian.md/help/web-clipper/highlight)、
[YTranscript](https://github.com/lstrzepek/obsidian-yt-transcript)、
[Media Extended timestamp 格式](https://mx.aidenlx.site/docs/v4/reference/timestamp-format)、
[Snipd plugin](https://www.snipd.com/blog/sync-snips-to-obsidian-plugin)、
[Kobo 內建 export](https://help.kobo.com/hc/en-us/articles/29991333812631-Export-annotations-from-your-books)、
[obsidian-kobo-highlights-import](https://github.com/OGKevin/obsidian-kobo-highlights-import)。）

### 收斂成兩種輸入形狀（ingest 的輸入契約）

**Shape A — 全文 + inline 劃線**（#1、#3；SOP 對文章與 YouTube 的推薦路線）
原版 route C 語意**完整成立**：全文 ingest、劃線是強調訊號不是過濾器、摘要吃全文。

**Shape B — 劃線集（無全文）**（#2、#4、#5、#6；Kobo 與 Podcast 天生如此）
vault 裡**根本沒有全文**。後果誠實面對，不假裝：

- 「全文 ingest、防注意力鏡子」**物理上不成立** —— 劃線集就是全部素材。Source Summary
  只能從「劃線 + 註 + metadata」派生，頁面必須誠實標示（`derived_from: annotation-set`、
  `confidence: low`），Concept 抽取上限收緊（見 §1.2 B 路徑）。
- 這不是 bug 是事實：Kobo 書的全文有版權也不在 vault；Snipd 不同步全集逐字稿。
  系統版之所以能堅持全文，是因為 Reader 擁有原文 —— 其他使用者沒有。
- 部分來源可**升級成 Shape A**：YouTube 用 YTranscript 補插全字幕（SOP 教這條）；
  文章一開始就用 inline 模式 clip（SOP 明定，不教 highlights-only）。書和 podcast 升不了。

### 統一 convention（SOP 對外教的劃線方式）

- **有全文的筆記**（Shape A）：劃線 `==...==`；個人想法 = 劃線段落後緊接一行 `note:: 想法`
  （容忍 `> [!annotation]` callout）。
- **劃線集筆記**（Shape B）：工具產出照舊；個人想法補在對應劃線下方 `note:: ...`
  （Snipd/Kobo importer 的自訂 template 可直接把個人註 render 成 `note::`，SOP 附範本）。
- 兩種形狀最後都由 /ingest 正規化成同一種 Literature Note（§1.2 Step 3），下游
  （draft-article）只認 Literature Note，不用管上游是哪個工具。

---

## 1. `/ingest`（可攜版）— 功能與邏輯

**一句話**：讀完一個來源後，把 capture 檔正規化成 Literature Note、產 Source 摘要
（全文派生或劃線集派生，誠實標示）、提出 Concept/Entity 計畫、**停下來給使用者裁決**、再寫入 KB。

### 觸發

「ingest 這篇 / 收進知識庫 / 這篇 KB 化 / 《X》第 N 章讀完了幫我 ingest / /ingest <slug or path>」。

### 拒絕與邊界（依形狀分流，主動說明而非默默照做）

| 情境 | 回應 |
|---|---|
| Shape A 下說「只收劃線那段 / 只要重點」 | 拒絕並說明：有全文就全文 ingest；劃線是強調訊號。要強調方向用 guidance 傳 |
| Shape B（本來就只有劃線集） | 不是拒絕情境——照 B 路徑走，但明講「這份是劃線集派生，沒有全文佐證」 |
| 整本書的劃線一次收 | **允許**（Kobo 匯入天生是整本的劃線集；一檔一 ingest）。但整本書**全文**一次收仍擋（那是系統版 route B 的領域） |
| 重寫 / 潤稿 | 不是 ingest，指去 /draft-article 或一般對話 |
| 「ingest 順便連到我的永久卡 / 手寫筆記」 | ingest 可做；連結是使用者的決定，skill 不碰使用者手寫區 |

### 1.1 Step 0 — 判定輸入形狀與來源 profile（v2 新增）

定位來源檔（使用者路徑 → `Inbox/` → `KB/Raw/`）後，先判 profile（依 frontmatter、
檔案結構特徵、必要時問使用者，**不猜**）：

| Profile | 判定特徵 | Shape |
|---|---|---|
| `article-fulltext` | 正文 >N 段落 + `==...==` inline（Clipper inline 模式） | A |
| `video-transcript` | timestamp 規律出現 + 連續字幕文本（YTranscript 插入） | A |
| `highlights-list` | 只有清單/blockquote、無連續正文（Clipper highlights-only） | B |
| `timestamp-notes` | 手寫筆記 + `#t=` / timestamp 連結（Media Extended） | B |
| `snipd-episode` | Snipd episode metadata + snip blocks | B |
| `kobo-book` | 書 frontmatter（ISBN/進度）+ 章節分組 blockquote（Kobo importer / 內建 export） | B |

判不出來 → 列出檔案開頭 + 問使用者這是什麼來源，絕不硬套。

### 1.2 流程（八步，HITL 卡第六步；A/B 分流處明標）

**Step 1 — 讀取與 metadata**
從 frontmatter 抽 `title` / `author` / `source_type` / `url` / `date`；缺的先問，不腦補。

**Step 2 — 抽 annotation set（per-profile parser）**
把該 profile 的劃線 / 註 / 定位資訊解析成統一三元組 `(highlight, note, locator)`：

- `article-fulltext`：`==...==` + 後接 `note::`；locator = 段落序位
- `video-transcript`：`==...==` + `note::`；locator = 最近一個 timestamp
- `snipd-episode`：snip 逐字稿節錄 + 個人註；locator = 音檔 timestamp 連結
- `kobo-book`：blockquote + 附註；locator = 章節名 + 章內序位
- `highlights-list` / `timestamp-notes`：清單項 / 筆記行；locator = 序位 / timestamp

Shape A 且零劃線 → 可 ingest，但告知「Literature Note 會空，只產摘要與計畫」。
Shape B 且零項目 → 沒有素材，停。

**Step 3 — render Literature Note → `KB/Literature/{slug}.md`（兩形狀共同出口）**
統一格式（exact copy，不 paraphrase）：

```markdown
---
title: "{title}"
type: literature
source: "{原檔相對路徑或 URL}"
source_type: article|video|podcast|book
capture_profile: {profile}
created: {today}
generated_by: ingest
---

## {章節名 或 不分章}

> {劃線/節錄原文 1}（[MM:SS](連結) 若有 timestamp） ^p-1

note:: {使用者註 1}

> {劃線原文 2} ^p-2
...
```

- `^p-N` 段落錨：render 時 1-based 序位；**timestamp / 章節 locator 保留在引文行內**，
  之後 Concept 頁與 draft-article 的 citation 能跳回原始時間點/章節。
- Idempotent 規則（簡化版）：Literature Note 視為 **generated 檔**，重跑 ingest 整檔重
  render；使用者的論點寫在 capture 檔的 `note::`，不直接編 Literature Note（SKILL.md 明講）。

**Step 4 — Source 摘要 → `KB/Wiki/Sources/{slug}.md`（A/B 分流核心）**

*A 路徑（有全文）*：LLM 讀**全文**產七段式摘要（① Core Claims 3-5 條附 confidence
② Key Data ③ Key Insights ④ Related Concepts `[[...]]` ⑤ Related Entities ⑥ Uncertainties
⑦ Actionable Takeaways，沿用雛形 `summarize.md`）。劃線紀律：摘要**必須涵蓋**劃線內容，
但**不得只涵蓋**劃線（防注意力鏡子）。>30,000 字：分塊 Read（offset/limit）→ 每塊小結 →
合併。frontmatter `derived_from: fulltext`、`confidence: medium`。

*B 路徑（劃線集）*：同七段式結構，但輸入只有「劃線 + 註 + metadata」。額外紀律：

- frontmatter `derived_from: annotation-set`、`confidence: low`；摘要開頭一行免責：
  「本頁自使用者劃線集派生，未經全文佐證；主張的完整脈絡請回原始來源。」
- **不得腦補來源沒說的內容**：每條 Core Claim 都要對得到至少一條劃線；Uncertainties
  段必列「劃線未覆蓋的部分無從判斷」。
- 可選增強（skill 問一次、不自動）：來源有 URL（文章）→ 問使用者要不要現場抓全文升級
  成 A 路徑；書/podcast 不提供此選項。

**Step 5 — Concept / Entity 計畫（吃摘要，不吃全文）**
資料流與系統版一致：抽取輸入是 Step 4 摘要（`_get_concept_plan(summary_body)` 的既定事實）；
要強調某段用 guidance。

1. Glob `KB/Wiki/Concepts/*.md`、`KB/Wiki/Entities/*.md` 列既有頁。
2. 4-action 判定（語意照抄 route-c-article.md）：**create 🆕** / **update_merge 🔀**
   （優先於建新頁，防 page explosion）/ **update_conflict ⚠️**（必附既有 vs 新 claim）/
   **noop 🟢**（只補 source 引用）。
3. 上限：A 路徑 3-5 concepts、1-3 entities（沿用）；**B 路徑收緊為 1-3 concepts、0-1
   entity** —— 劃線集的資訊密度撐不起五個 concept 頁，寧缺勿濫。
4. 過濾規則沿用雛形：concept 要跨來源可重複、有獨立解釋價值；entity 嚴選
   （person 只收長期追蹤、tool 只收會用、book 通常跳過、org 幾乎不建）。

**Step 6 — 🚦 HITL gate：accept / defer / exclude（不可自動放行）**
計畫列表（action + 標題 + 理由；conflict 顯示兩邊主張），使用者逐項裁決：
**accept** 留下；**defer** 移除 + 記 `KB/taste-profile.md` raw 區；**exclude** 移除 +
**記理由**（最強負面品味訊號）。主觀品味驗收 LLM 吸收不掉
（`feedback_hitl_gate_serves_subjective_taste`），對外同樣保留——賣點不是摩擦。

**Step 7 — execute（只寫 accepted 項目）**

- Concept 頁模板沿用雛形 `write-concept.md`（Definition → Core Principles → Sub-concepts →
  Main Controversies → Practical Applications → Related Concepts → Sources）；
  frontmatter `author: claude`、`source_refs: ["[[{source page}]]"]`。
- **update_merge**：讀既有頁、併新主張進對應段落、`source_refs` 追加；不整頁重寫。
- **update_conflict**：不抹平，寫「## 文獻分歧」段（`feedback_kb_concept_aggregator_principle`）。
- Entity 頁依 type 沿用雛形 `write-entity.md`。
- **Citation 紀律（紅線 2 可攜版）**：每個事實宣稱附得回 `[[Sources/{slug}]]` 或
  `[[Literature/{slug}#^p-N]]`（B 路徑的宣稱**只能**引 Literature 錨——那才是一手材料）；
  **終端證據只能是 Sources / Literature / 原檔**，不得引另一個 Concept（紅線 5 防
  citation laundering——系統版有 `provenance_linter` 硬擋，可攜版降級為 checklist 自審）。
- 更新 `KB/index.md` + append `KB/log.md`：
  `- [{timestamp}] ingest: {title} ({profile}) → created N, updated M`。

**Step 8 — 報告 + 品質 checklist**
列出寫了哪些頁、update 了哪些、index/log 已更新。自審：

- [ ] 所有 `[[wikilink]]` 指向真實檔名
- [ ] A 路徑：摘要涵蓋所有劃線；B 路徑：每條 Claim 對得到劃線、免責標示在
- [ ] 沒有為既有概念建重複頁
- [ ] 每個事實宣稱有 citation、終端證據不是 Concept
- [ ] 頁數在該路徑上限內
- [ ] 沒碰使用者手寫區

### 1.3 與系統版差異表（防規格漂移誤會）

| 面向 | 系統版（Nakama） | 可攜版 | 弱化？ |
|---|---|---|---|
| 引擎 | `agents/robin/ingest.py` Python pipeline | SKILL.md 指示的 LLM 步驟 + 檔案操作 | 行為靠 prompt 紀律，無確定性保證 |
| 輸入 | Reader 擁有全文 + `KB/Annotations` store；永遠有全文 | 六種 capture profile、兩種形狀；**B 形狀無全文**（§0.5） | **語意分流**（不是弱化，是事實不同） |
| 全文原則 | 一律全文 ingest（紅線 D-A） | A 路徑沿用；B 路徑改為「劃線集派生 + 誠實標示 + 收緊上限」 | B 路徑無法滿足 D-A，明示於頁面 frontmatter |
| Literature render | `write_literature_note`（N521 idempotent writer，保留記帳區） | per-profile parser → 統一格式整檔重 render，宣告為 generated 檔 | 是（無記帳區保留） |
| 長文摘要 | `_generate_summary` 自動 map-reduce | skill 指示分塊 Read + 合併 | 等效但靠紀律 |
| 紅線 enforcement | `assert_not_permanent_target` + `provenance_linter` tripwire | SKILL.md 硬邊界 + checklist 自審 | **是，明確弱化** |
| taste loop | `style/taste-profile.md` + 人工蒸餾 | `KB/taste-profile.md` raw 區 only | 簡化 |
| KB 相關區（FTS5 撈） | Literature Note 尾端 `## 🔗 KB 相關` | 不做（原版也是死區雜訊，draft-article 明言跳過） | 砍 |
| 書/影片邊界 | 整本書 route B、影片 route E 未上線全擋 | 整本書**全文**仍擋；**整本書的劃線集（Kobo）與影音筆記檔可收** | 語意調整（§0.5 的直接後果） |

---

## 2. `/draft-article`（可攜版）— 功能與邏輯

**一句話**：從一個已 ingest 的 source，讀使用者**全部**的劃線與 `note::`，產出
「讀者分析 + 3 Hook + 逐條覆蓋 annotation 的理性文章骨架」；句子層聲音留給使用者。

原版邏輯**幾乎照搬**（本來就設計成讀 vault 檔案、單向 provenance），改動是環境接線 +
一條 Shape B 的查證限制。

### 2.1 保留不動的核心（照抄原版）

1. **分工邊界**：skill 出「策略結構 + 完整理性骨架」（TA 分析、3 Hook、論證弧、每節
   「為什麼這對你重要」）；使用者加「句子層的情緒口吻」。對外賣點：**AI 出骨架，聲音是你的**。
2. **Step 順序**：解析 slug → 載入素材 → 盤點兩條素材線（來源的邏輯骨架 / 使用者 `note::`
   逐條編號清單）→ 讀者優先三題（TA、payoff、怎麼留住他）→ 3 Hook（願景 → 傳統做法 →
   看似可行 → 已被阻斷 → 新地圖；三版本不同入口）→ compose（每節：來源論點 + 使用者
   note 論據）→ 寫檔 → lint + 自審 → 交付。
3. **六條紀律**（違反任一 = 失敗）：逐條覆蓋每條 `note::`（理性 ≠ 精簡）／展開舉例／
   保留使用者的具體（「龍氏蛋」不抽象成「蛋」）／理性銜接不寫句子層情緒／絕不虛構
   （每個立場、數字對得到真實 note 或 source）／衝突標記出來問、不自行抹平。
4. **硬邊界**：只出理性骨架；provenance 單向（KB → 草稿，絕不回寫 KB）；不定稿不發布。
5. **lint**：`scripts/lint_draft.py` 純 stdlib、零依賴，**原封不動隨 plugin 打包**
   （emoji / 台味句尾助詞 >8 / 驚嘆號過多；exit 1 = ornament creep，清掉重跑）。

### 2.2 環境接線改動

| 原版 | 可攜版 |
|---|---|
| `VAULT=$(python -c "from shared.config import get_vault_path; ...")` | cwd = vault root（§0 前提），相對路徑 |
| 素材：`KB/Annotations/{slug}.md`（完整 store，查證用） | 查證走 **Literature Note + 原始 capture 檔**（`source_refs` 指回去） |
| 素材：`KB/Raw/...` 全書全文（補脈絡） | **Shape A**：原始 capture 檔有全文可查。**Shape B：無全文** —— 補脈絡不可用，缺的脈絡列成問題問使用者，不自行補寫（紀律「絕不虛構」的自然延伸） |
| 輸出：`AgentOutputs/brook/drafts/{slug}-draft-{date}.md` | `Drafts/{slug}-draft-{date}.md`（`generated_by: draft-article`；`source` 指 `[[KB/Literature/{slug}]]`） |

### 2.3 前置條件與失敗模式

- `KB/Literature/{slug}.md` 不存在 → 「這篇還沒 ingest，先跑 /ingest」，停。
- Literature Note 存在但 `note::` 為零 → 明講「沒有你的論點，只能出『來源論點』單線骨架，
  價值減半」，問使用者要不要先補 note（不默默出貨）。
- Shape B 來源（`capture_profile` 在 frontmatter）→ 開頭提示「本篇素材是劃線集，
  沒有全文可查證；引用的數字與主張以你劃的原文為準」。
- slug 多重命中 / 找不到 → 列候選問，不猜。

---

## 3. Plugin 打包結構（librarian plugin）

```
librarian/
├── .claude-plugin/plugin.json
├── skills/
│   ├── ingest/
│   │   ├── SKILL.md                  # §1 邏輯（以 kb-ingest 雛形升級）
│   │   └── references/
│   │       ├── conventions.md        # 劃線 convention + vault 佈局 + 六 profile 判定特徵（與 SOP 同源）
│   │       ├── summarize.md          # 七段式摘要（A/B 兩路徑紀律）
│   │       ├── extract-concepts.md   # 4-action + 過濾規則 + A/B 上限
│   │       ├── write-concept.md      # Concept 頁模板（自雛形搬）
│   │       └── write-entity.md       # Entity 頁模板（自雛形搬）
│   └── draft-article/
│       ├── SKILL.md                  # §2 邏輯
│       └── scripts/lint_draft.py     # 原封搬入（純 stdlib）
```

- 打包工具：`cowork-plugin-management:create-cowork-plugin` skill（已安裝）產 `.plugin`。
- Capture 層 SOP 文件（四來源教學）是**獨立交付物**，不進 plugin；其 convention 段與
  `references/conventions.md` 同源維護。SOP 的推薦路線要跟 §0.5 對齊：
  - 文章：Clipper **inline highlighting 模式**（教 Shape A，不教 highlights-only）
  - YouTube：YTranscript 插全字幕 + `==劃線==`（升級成 Shape A）；輕量替代 = Media Extended timestamp notes（Shape B）
  - Podcast：Snipd 官方 plugin（Shape B 天生；snip template 加 `note::` 範本）
  - Kobo：主推薦 Kobo Highlights Importer（商店書 + sideload 通吃；template 附 `note::` 範本）；
    內建 export（Markdown）列次選 —— 2025-09 上線後多人實測回報選項未出現/不穩，且限商店書（皆 Shape B）

## 4. 開放問題（實作前要拍的）

1. **對外命名**：plugin 版 skill 建議改名（如 `/kb-ingest`、`/note-to-article`），避免與修修
   環境的系統版 `/ingest`、`/draft-article` 觸發語互撞。
2. **B 路徑可選增強的邊界**：文章 highlights-only 檔提供「現場抓 URL 全文升級 A 路徑」——
   要不要 v1 就做？（多一個 WebFetch 依賴；不做則 SOP 更用力推 inline 模式。）
3. **writing-style**：對外版維持繁中預設（受眾 = 修修的社群）。
4. **domain**：雛形綁 Health & Wellness；對外版首次執行問使用者領域、寫進 conventions。
