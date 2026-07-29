# ingest skill 補丁：capture-profile 生成邏輯 ＋ Source 摘要 ＋ 文獻分歧

> 交給 Cowork 補進 Liberian Girl 的 `ingest` skill（`E:\Projects\張修修的AI創作者新世紀\skills\ingest\`）。
> 本檔自足可獨立執行 —— 不需讀 Nakama repo 也能照做。
>
> **這份補丁綁三件事**（2026-07-15 對話收斂）：
> 1. **capture-profile 感知的 Literature Note 生成**（主）—— 讓沒有 Nakama 系統的使用者，
>    用 Web Clipper / Media Extended / Snipd / Kobo 產出的真實檔案也 ingest 得動，且**保留
>    timestamp / 章節 locator**（現行版把這些丟掉，導致 citation 跳不回原始時刻）。
> 2. **補回 Source 摘要頁** —— 現行版把摘要用完即丟、沒存成頁；補回後每個來源留一份可重讀的濃縮版，
>    也是 demo 腳本（段落 5.3「Literature Note → Source 摘要 → Concept 候選」）要展示的產物。
> 3. **補上文獻分歧處理** —— 新來源與既有 Concept 矛盾時不靜默併掉，寫「## 文獻分歧」並陳兩邊，
>    KB 長大不會假一致（健康/醫學題常互相打臉；也是誠實性賣點）。
>
> 依據：capture 路線見同目錄 `2026-07-13-obsidian-capture-routes.md` 與
> `...portable-librarian-skills-spec.md`。

---

## 補丁落點

1. **`skills/ingest/SKILL.md`**
   - Step 1 之前插 **Step 0：判定 capture profile**（§1）。
   - Step 1 的 Literature 渲染改成「per-profile 解析 → 統一單元 → 保留 locator」（§2–§3）。
   - Step 1 **加寫 Source 摘要頁**（§4）；Concept 候選改成**吃這份摘要**。
   - Step 2 的 Concept 寫入改成 **4 動作分派**，含 `update_conflict → 文獻分歧`（§5）。
2. **新增 `skills/ingest/references/capture-profiles.md`** — 放 §1 的偵測表 + §2 的四個解析規則。
   SKILL.md 在 Step 0 指示「不確定 profile 就讀這份」。

---

## 1. Step 0（新增）：判定 capture profile

定位來源檔後先判 profile（依 frontmatter、結構特徵；判不出來就列檔案開頭問使用者，**不猜**）：

| Profile | 來源 × 工具 | 偵測特徵 | Shape | Locator |
|---|---|---|---|---|
| `article-web` | 文章 × Obsidian Web Clipper（整篇 clip → 在 Obsidian 劃線） | clipper frontmatter（`source`/`author`/`published`）+ 連續正文含 inline `==...==` | **A**（有全文） | 段落序位 |
| `youtube-transcript` | YouTube × Media Extended v4（下載字幕 → 逐字稿複製行） | 出現 `[MM:SS](…youtube…#t=NNN) 台詞` 形態的引文行 + `note::`；完整 SRT 可能存於 Raw | **A**（有全文字幕） | 時間戳 `#t=` |
| `podcast-snipd` | Podcast × Snipd 官方 plugin | 一集一檔；episode metadata frontmatter + 每個 snip = 逐字稿節錄 + AI 摘要 + 個人註 + 音檔跳轉連結 | **B**（無全集逐字稿） | 音檔 timestamp 連結 |
| `ebook-kobo` | 電子書 × Kobo Highlights Importer | 書 frontmatter（`title`/`author`/`isbn`/進度）+ 依章節分組的 blockquote 劃線 + 附註 | **B**（無全文） | 章節名 + 章內序位 |

**Shape 的意義**：A = 有全文 → 摘要/Concept 抽取吃全文；B = 只有劃線集 → 摘要**只以劃線派生**，
標 `derived_from: annotation-set`、`confidence: low`、頁首免責、Concept 上限收緊 1–3。

---

## 2. Per-profile 解析規則（→ `references/capture-profiles.md`）

每個 profile 解析成統一三元組 **`(quote, note, locator)`**：`quote` = 劃線/節錄原文（exact copy，
不 paraphrase）；`note` = 使用者自己的想法；`locator` = 定位資訊。

### 2.1 `article-web`（Shape A）
- **quote**：抽出所有 inline `==...==` 標記的文字（保留原文，去掉 `==` 標記符）。
- **note**：緊接該劃線段落**下一行**的 `note:: ...`（容忍 `> [!annotation]` callout）。
- **locator**：該劃線在正文的段落序位（第 N 段）。
- **全文**：整篇正文存 `KB/Raw/{slug}.md`（Shape A 的全文佐證來源）。

### 2.2 `youtube-transcript`（Shape A）
- **quote**：使用者從逐字稿面板複製的行 `[MM:SS](url#t=NNN) 台詞` → 取「台詞」為 quote。
- **note**：其後的 `note:: ...`。
- **locator**：該行的 `[MM:SS](url#t=NNN)` **時間戳連結原樣保留**。
- **全文**：若 vault 有下載的完整 SRT/逐字稿 → 存/連 `KB/Raw/{slug}.*`（Shape A）；沒有則降級當 B。

### 2.3 `podcast-snipd`（Shape B）
- **quote**：每個 snip 的逐字稿節錄段。
- **note**：snip 的個人註欄位（Snipd template 建議 render 成 `note::`；沒設 template 就抓 snip 原生
  note/comment 欄位）。snip 的 AI 摘要**當補充**放 quote 後、標 `（AI 摘要）`，不與使用者原話混。
- **locator**：該 snip 的音檔 timestamp 跳轉連結，原樣保留。
- **全文**：無（Snipd 不同步全集逐字稿）→ Shape B。

### 2.4 `ebook-kobo`（Shape B）
- **quote**：每條 blockquote 劃線。
- **note**：該劃線的附註（Kobo importer template 建議 render 成 `note::`；否則抓 `highlight.note`）。
- **locator**：所屬**章節名** + 章內序位（Kobo importer 依章節分組，保留分組資訊）。
- **全文**：無（書有版權，全文不在 vault）→ Shape B。

**共同紀律**：
- quote 一律 exact copy，**不改寫、不 paraphrase、不補寫使用者沒寫的想法**。
- 零劃線的 Shape A → 可續跑（只產摘要與 Concept 候選，告知 Literature 會空）；
  零項目的 Shape B → 沒素材，停。
- 「只收某段/只要重點」在 Shape A 主動回絕（有全文就全文，劃線是強調不是過濾）；
  Shape B 本來就只有劃線集，照 B 走並明講「無全文佐證」。

---

## 3. 統一 Literature Note 輸出（保留 locator）

四個 profile 最後都 render 成**同一種** Literature Note，下游只認這個檔、不管上游是哪個工具。
相對現行版，**新增 `capture_profile` / `shape` frontmatter，並把 locator 保留進每條引文行**。

```markdown
---
type: literature
source: "{原檔相對路徑或 URL}"
source_kind: article | video | podcast | book
capture_profile: article-web | youtube-transcript | podcast-snipd | ebook-kobo
shape: A | B
derived_from: fulltext | annotation-set     # A→fulltext, B→annotation-set
slug: {slug}
captured: {date}
status: unprocessed
mined_concepts: []
---

## {章節名 或 「不分章」}

> {quote 1}  ⟶  {locator: [12:34](url#t=754) 或 📖 第3章·§2}
^p-1

note:: {使用者的想法 1}

> {quote 2}
^p-2

note:: {使用者的想法 2}
```

規則：
- **locator 內嵌引文行**：影片/Podcast 放可點 timestamp 連結；書放「章節·序位」；文章不必額外標。
- `^p-N` 區塊錨保留 —— 供 Concept citation 回指 `[[Literature/{slug}#^p-N]]`；因 locator 在行內，
  點 citation 能一路跳回**那一秒 / 那一章**。
- **Shape B 專屬**：`derived_from: annotation-set`（下游 Source 摘要帶 low confidence + 免責，見 §4）。

---

## 4. Source 摘要頁（補回）

Literature 渲染後、Concept 抽取前，**產一份整份來源的摘要並寫成頁**：`KB/Wiki/Sources/{slug}.md`。

**內容＝七段式**（每段可空但要出現）：
1. **Core Claims**（3–5 條，各附信心 high/medium/low）
2. **Key Data**（具體數字、統計、研究結果）
3. **Key Insights**（作者獨到觀點）
4. **Related Concepts**（`[[...]]`）
5. **Related Entities**（人/工具/書/機構，`[[...]]`）
6. **Uncertainties**（未解問題、需更多證據處）
7. **Actionable Takeaways**（可操作結論）

**frontmatter**：
```yaml
---
type: source
status: draft
author: ai
source_kind: article | video | podcast | book
capture_profile: {同 Literature}
shape: A | B
derived_from: fulltext | annotation-set
confidence: medium        # Shape A = medium；Shape B = low
source_refs: ["{Raw 路徑或 URL}", "[[Literature/{slug}]]"]
created: {date}
---
```

規則：
- **A 路徑**：LLM 讀 `KB/Raw` 全文產摘要。摘要**必須涵蓋**使用者劃線內容，但**不得只涵蓋**劃線
  （防注意力鏡子）。>30000 字 → 分塊 Read（offset/limit）→ 每塊小結 → 合併。
- **B 路徑**：只吃 annotation set 產摘要。`confidence: low`，頁首加一行免責
  「本頁自使用者劃線集派生，未經全文佐證；完整脈絡請回原始來源」。每條 Core Claim 都要對得到
  至少一條劃線；Uncertainties 段必列「劃線未覆蓋處無從判斷」。**不腦補來源沒說的內容**。
- **資料流（重要，別搞錯）**：**Concept 候選抽取吃「這份摘要」，不是全文**。要強調某段用 guidance 傳。
- Source 摘要頁是 `status: draft` candidate 層，跟 Literature 一樣在 HITL 前就寫（它是摘要，不是
  正式知識；審查只針對 Concept/Entity）。

---

## 5. Concept 寫入：4 動作分派 ＋ 文獻分歧（補上）

現行版只有「新建 / 併入不覆蓋」兩態。改成**四動作**，並在提案（HITL gate）時就標好給使用者看：

| 動作 | 何時 | 做什麼 |
|---|---|---|
| **create 🆕** | 無同名 Concept | 建新頁 |
| **update_merge 🔀** | 既有頁，新來源**補充/延伸**、不衝突 | 併進對應段落，`source_refs` 追加。**優先於亂建新頁**（防 page explosion） |
| **update_conflict ⚠️** | 既有頁的主張與新來源**互相矛盾** | **不 merge、不覆蓋** → 在該頁寫/追加 `## 文獻分歧` 段，並陳兩邊：`〔來源 A〕主張 X ／〔來源 B〕主張 not-X`，各附 citation |
| **noop 🟢** | 已完整涵蓋 | 只補 source 引用，不改正文 |

**衝突偵測**：抽 Concept 候選時，對每個既有同名頁比對「新來源的主張 vs 頁上既有主張」。同一主題
方向相反 → 標 `update_conflict`，候選要帶 `{topic, existing_claim, new_claim}` 給 HITL 顯示。

**HITL 顯示**：`update_conflict` 項目要把「既有 vs 新」兩句並列給使用者看，讓他判斷（accept 就寫進
文獻分歧、defer/exclude 就不寫）。

**「## 文獻分歧」段格式**（寫進該 Concept 頁）：
```markdown
## 文獻分歧
- **{主題}**：〔[[Sources/{A}]]〕主張 {existing_claim}；〔[[Sources/{B}]]〕主張 {new_claim}。
```

**為什麼**：Concept 頁是**跨來源匯集器**，不是後蓋前。矛盾要誠實標記，KB 長大不會假一致
（健康/醫學題尤其常互相打臉）。demo 通常不會觸發（單次 ingest 1–2 篇、無同概念第二來源），
但這是零風險先埋、將來自動生效的長期紀律。

---

## 6. 下游接線

- **Annotations store**（`KB/Annotations/{slug}.md`）：保存原始三元組，`^a1/^a2…` 錨點。
- **Concept 頁 frontmatter**：`author: ai`；`source_refs` 含 `[[Sources/{slug}]]`；每個事實宣稱
  citation 到 `[[Literature/{slug}#^p-N]]`（locator 在行內 → 跳得回原始時刻/章節）。
- **終端證據紀律（紅線 5）**：Concept 的事實只能溯到 Sources / Literature / Annotations / Raw，
  **不得拿另一個 Concept 頁當事實來源**（防自我餵食）。
- **index / log**：新頁掛 `KB/index.md`；`KB/log.md` append 一行
  `- [{ts}] ingest: {title} ({profile}) → created N, merged M, conflict K`。
- **note:: 對齊**：SOP 教使用者把 Snipd / Kobo importer 個人註 render 成 `note::`；parser 同時容忍
  工具原生 note 欄位當 fallback。

---

## 7. 驗收（Cowork 補完後自測）

capture profiles：
- [ ] 四種 profile 各餵一個真實 capture 檔，都判對 profile、render 出統一 Literature Note。
- [ ] `youtube-transcript`：Literature 引文行保留可點時間戳，點下去跳回該秒。
- [ ] `ebook-kobo`：Literature 依章節分組，引文帶「章節·序位」。
- [ ] `podcast-snipd`：AI 摘要與使用者原話分離標示，音檔連結保留。
- [ ] quote 全是 exact copy，無 paraphrase、無腦補。

Source 摘要：
- [ ] 每次 ingest 都寫出 `KB/Wiki/Sources/{slug}.md`，七段式齊全。
- [ ] Shape A 摘要涵蓋劃線但不只劃線；Shape B 有 `confidence: low` + 頁首免責。
- [ ] Concept 候選確實吃「摘要」產出（非全文）。

文獻分歧：
- [ ] 餵兩篇對同一概念**主張相反**的來源 → 第二篇標 `update_conflict`、HITL 顯示「既有 vs 新」、
  accept 後該 Concept 頁長出「## 文獻分歧」段並陳兩邊，**未被靜默 merge**。
