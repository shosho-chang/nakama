# Chapter Source Summary — Section-by-Section Template

Use this prompt template when summarizing a single textbook chapter into
a Source page (`KB/Wiki/Sources/Books/{book_id}/ch{n}.md`).

Per ADR-010 §D2, the summary structure is **section-by-section**, not a
single dense paragraph. Reasoning: Robin's existing summarizer compresses
to ~few hundred words which is fine for 10-20-page articles but loses
critical density when applied to 50-200-page textbook chapters. We keep
the chapter as one file (vault-clean) but expand the body to track the
chapter's internal section structure.

---

## Inputs

- `book_id` — slug, e.g. `harrison-internal-medicine-21e`
- `book_title` — full title, e.g. `Harrison's Principles of Internal Medicine`
- `chapter_index` — 1-based int
- `chapter_title` — string, e.g. `Cardiovascular Examination`
- `page_range` — string, e.g. `142-187`
- `section_anchors` — list of section headings detected in the chapter
- `chapter_content` — the full chapter text (Opus 1M context fits 30-100 pages)
- `language` — `en` / `zh-TW` / `zh-CN`

---

## Output structure

```markdown
# Chapter {chapter_index} — {chapter_title}

## {section_anchor_1}

（這節 2-3 段重點，covering：核心定義、機制、臨床或實務應用）

## {section_anchor_2}

（同上）

…

## 章節重點摘要 / Chapter takeaways

3-5 條 bullet 點，整章最重要的結論 / 臨床啟示 / 跨章關聯。

## 關鍵參考數據 / Key reference values

如有公式、正常範圍、閾值、診斷標準，整理成表格：

| 數據 | 數值 / 範圍 | 來源 |
|------|------------|------|
| 例：左心室射出分率正常值 | 50-70% | 本章 §3.2 |

## 跨章 / 跨書 連結建議

LLM 自評：本章內容跟既有 KB 的什麼 concept / source 強相關？列出建議
加入 `mentioned_in:` 的 wiki 頁。
```

---

## Writing guidelines

1. **每節 2-3 段 = 約 300-500 字** — 不要寫成一句話 bullet（壓縮太狠失去
   檢索價值），也不要寫成完整章節原文（Source 頁是摘要不是抄錄）。
2. **保留原文術語並附中文翻譯** — 例如：`Frank-Starling Law（佛朗克–史塔林
   定律）`。retrieval LLM 對 bilingual term 比單語精準。
3. **明確列出概念之間的 parent → child 關係** — 用 `[[concept-slug]]` 內鏈
   範例：`[[cardiovascular-examination]] → [[auscultation]] → [[murmur-grading]]`
4. **保留教科書明確標記的「常見誤解」段落** — 教科書區分 fact vs
   common misconception 是 KB 寶貴的差異化來源
5. **避免**：
   - 過度詩意 / 抒情語調（這是醫學參考頁，不是部落格）
   - 自己加觀點 / 推測超出原文（教科書 ingest 是忠實摘要，不是評論）
   - 一節寫太長以致 LLM 之後 retrieve 時讀不完

## 信心等級判定（給 retrieval LLM 用）

教科書內容基線為 established knowledge。如果章內提到尚有爭議的內容
（例如 "emerging evidence suggests..."），在 summary 內明確標註，
不要讓 retrieval 把 emerging 當 established 餵給 Chopper。

---

## Full prompt（fill-in template）

```
你的任務是閱讀以下教科書章節內容，並產出結構化的 Chapter Source Summary。

書資訊：
- 書名：{book_title}
- 版本：{edition}
- 章節：第 {chapter_index} 章 — {chapter_title}
- 頁碼範圍：{page_range}
- 偵測到的節：{section_anchors}
- 語言：{language}

請按照以下結構輸出 markdown body（frontmatter 由 ingest pipeline 自動加）：

# Chapter {chapter_index} — {chapter_title}

## {對每個 section_anchor}
（2-3 段重點，covering：核心定義、機制、臨床或實務應用，
保留原文術語 + 中譯，用 [[concept-slug]] 標出可建概念頁的詞）

## 章節重點摘要 / Chapter takeaways
3-5 條 bullet 整章重要結論

## 關鍵參考數據 / Key reference values
如有公式、正常範圍、閾值、診斷標準 → 表格化

## 跨章 / 跨書 連結建議
本章跟既有 KB 哪些 concept / source 強相關（給 retrieval 補 mentioned_in 用）

---

寫作指引：
- 每節 300-500 字。不要壓縮成 bullet，也不要照抄原文
- 術語雙語：「Frank-Starling Law（佛朗克–史塔林定律）」
- 保留教科書明示的「common misconception」段落
- emerging evidence 明確標註，不要當 established knowledge
- 客觀紀錄不主觀評論

章節內容如下：

{chapter_content}
```
