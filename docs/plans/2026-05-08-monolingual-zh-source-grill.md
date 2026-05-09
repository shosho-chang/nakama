# Monolingual-zh Source Path — Grill Summary

**Date**: 2026-05-08
**Status**: Grill complete; ADR + PRD pending
**Worktree**: `E:/nakama-qa-adr021` (branch `qa/N461-adr-021-e2e-v2`)
**Participants**: 修修 + Claude (Opus 4.7 1M)

## Why

修修手上有兩本台版中譯 EPUB 要念 + 偶爾要 ingest 純中文網路文章。既有 EPUB Reader (PRD #378) + Document Reader (PRD #351) 設計鎖死「英文 source + 中譯 target」bilingual mode，monolingual-zh source path 完全沒實作：

- EPUB upload form `bilingual` field required + ingest gate 強制 `has_original=True` 擋無英文原檔（`thousand_sunny/routers/books.py:120, 241-242`）
- Reader UI 走雙語 layout，無對應 single-column 形態
- `shared/translator.py` glossary 結構是 `{英文: 台灣中文}`，prompt 寫死「翻譯成中文」
- `textbook-ingest` skill / IngestPipeline (`/start`) concept extract prompt 假設 EN source（無實測 zh source 行為，未驗證 prompt robustness）

並非單純 schema 加欄位的事 — 跨 reader UI / translator / ingest prompt / annotation_merger 四處有結構性差異。

## Grill 8 題凍結

### Q1 — Mental model：mode 平行 vs 雙語特例
**凍結：平行（reality 強制）**

修修手上中譯書沒英文原檔，三處結構性失敗：
- `has_original` ingest gate 擋進不去
- bilingual layout 沒英文段可放
- EN-tuned translator 對中文 input 行為未定義

→ monolingual-zh = 跟 bilingual 平行的獨立 mode，不是 lang_pair="zh-zh" 特例

CONTEXT-MAP 詞彙已寫入：
- `bilingual mode` = 兩份檔（英文 source + 自翻雙語對照）
- `monolingual-zh mode` = 一份中文檔（台版中譯 / 中文 native / 中文網路文章）

### Q2 — PRD scope：1 個 vs 拆
**凍結：拆 2+1**

| PRD | Scope |
|---|---|
| PRD-A schema | mode field + detection helper + grounding pool helper + migration（cross-cutting） |
| PRD-B book | monolingual-zh book reader end-to-end（upload UI + reader render + ingest gate 放寬 + textbook-ingest zh-variant） |
| PRD-C document | monolingual-zh article（Web Clipper detection + Reader UI + /translate noop + /start ingest zh-variant） |

理由：scope 集中、shipping 順序清楚（PRD-B 先做配合修修手上兩本書）、避免 ADR-021 那種橫跨大怪物 grill 反覆。

### Q3 — Schema：mode field 形態
**凍結：B 新加 mode field、廢 lang_pair**

| Surface | Field |
|---|---|
| `books` table | `mode: "monolingual-zh" \| "bilingual-en-zh"` |
| Source frontmatter | `mode:` 同 enum |
| 既有 `lang_pair: str` | deprecated（不讀） |

字串值對齊 Q1 凍結的詞彙；廢 boolean `bilingual: true` flag 改 mode enum。

### Q4 — Detection 機制
**凍結：C auto detect + UI override**

- **EPUB**：主走 `meta.lang`（`shared/schemas/books.py:28` 已 capture 但未 wire 進 lang_pair 決定）；`meta.lang` 缺值 / 多語時 langdetect on first chapter sample
  - `meta.lang in {"zh", "zh-TW", "zh-CN"}` → `monolingual-zh`
  - `meta.lang in {"en", ...}` → `bilingual-en-zh` default
- **Document**：主走 langdetect on body chars
  - 套件選擇（`langdetect` / `pycld3` / `lingua`）PRD-A 拍板
- **UI**：upload form / inbox 顯示 detected mode badge + 「換 mode」按鈕

### Q5 — Concept page 命名 [ADR-WORTH]
**凍結：C 英文 canonical + 中文 alias**

| Aspect | 規則 |
|---|---|
| Page filename | 英文 canonical（`Concepts/Mitophagy.md`） |
| frontmatter aliases | 中文同義詞 array（`["粒線體自噬", "粒線體吞噬作用"]`） |
| 中文 native 無英文業界 term | fallback canonical 中文（`Concepts/中醫脾胃論.md`） |
| 既有 alias_map seed dict（5/8 P0 batch ship） | 擴 zh-EN entries |
| Brook synthesize 輸出 | wikilink alias display（`[[Concepts/Mitophagy\|粒線體自噬]]`） |

3 個 user journey scenario 驗證：
1. 中文 highlight sync 到 Concept page — alias map 認到既有英文 page
2. Obsidian 內搜尋中文 phrase — 內建 wikilink alias 命中
3. Brook 寫中文長壽科普 — multilingual embedding 跨語言 dense 撈 + alias display

### Q6 — Ingest concept extraction prompt
**凍結：B zh-source prompt variant + grounding pool**

- textbook-ingest skill 加 zh-source prompt variant，detection 分流（`mode==monolingual-zh` 走新 prompt）
- 既有 EN-source prompt 不動（zero 回歸 risk）
- prompt 預先帶**既有 KB Concept name + alias 當 grounding pool**（防 LLM hallucinate 假英文 canonical）
- 輸出 schema：

```yaml
- canonical_en: str         # 必須英文，優先 grounding pool 既有 name
  aliases_zh: list[str]     # 原文出現的中文同義詞，最多 3 個
  is_zh_native: bool        # true 時 canonical 可中文（無英文業界 term）
```

PRD-A spec 階段拍：
- Grounding pool 怎麼喂 prompt（全塞 vs retrieval-based 先撈相關 concept）
- token budget

### Q7 — annotation_merger LLM-match cross-lingual
**凍結：B prompt 微調 + 帶 aliases 進 candidate context**

- candidate Concept page 顯示時帶 frontmatter aliases 一起：`Mitophagy (aliases: 粒線體自噬, 粒線體吞噬作用)`
- system prompt 加：「annotation 跟 Concept name 可能不同語言，按 semantic 判斷不是字面」
- 1-2 few-shot example（中文 highlight + 英文 Concept + alias 的 match 範例）
- vector retrieve top-N 從 3 加大到 5（候選池更廣）
- 不新加 zh-annotation variant（task 是 judgement 不是 extraction、overkill）

PRD-A spec 階段拍：
- acceptance test：sample 5 條中文 highlight 測 match precision

### Q8 — Translate UI 在 monolingual-zh
**凍結：A 全條件 hide**

| UI 元件 | monolingual-zh 行為 |
|---|---|
| Document inbox row「翻譯」按鈕 | 條件 render = 不顯示 |
| Document Reader「譯文 v / 譯文 x」toggle | 同上 |
| EPUB upload form「EN 原檔 EPUB」dropzone | 隱藏 + `has_original` 自動 false |
| Detection failure default | bilingual mode（保留按鈕，避免功能消失） |

理由：
- monolingual-zh 結構性無翻譯目標
- Noop guard 是反 UX pattern
- Reverse-translate（中→英）超出 PRD-B/C scope

## 沒 grill 但 PRD spec 階段拍板的 implementation detail

1. **既有英文 Concept page 的 zh aliases 補強策略** — 推薦 lazy build（隨中文書 ingest 累積，textbook-ingest 自動 merge 進 frontmatter aliases）；batch LLM 補是 nice-to-have、PRD-B ship 後再考慮
2. **既有 hack 上傳的書 migration** — 修修可能之前把同份中文 EPUB 上傳兩次當 bilingual + original。Detect 時若 `bilingual.sha == original.sha` → 自動切 monolingual-zh + null original
3. **Detection edge case** — 多語混雜（譯者前言中文 + 內文英文）走 user override，不嘗試自動細粒度切
4. **`books` table 既有 N 筆 backfill** — 都是 `lang_pair="en-zh"`，backfill `mode="bilingual-en-zh"`
5. **`Inbox/kb/*.md` 既有檔 mode lazy detect** — 第一次 inbox load 時 detect + 寫回 frontmatter（要 review 對 user-edited frontmatter 的覆寫風險）
6. **digest.md 跨語言 KB hits 顯示** — search_kb 結果是英文 page name + relevance_reason，wikilink display 用 alias（`[[Mitophagy|粒線體自噬]]`）

## 下一步

### 1. Draft ADR
`docs/decisions/ADR-NNN-cross-lingual-concept-alignment.md`

Cover Q5 + Q6 + Q7 同條 cross-lingual implementation 軸：
- Concept page 命名規則（Q5）
- Ingest prompt variant + grounding pool（Q6）
- annotation_merger prompt 微調（Q7）

### 2. Panel review on ADR
multi-agent-panel skill（Codex + Gemini）push-back audit。

Trigger 理由：
- **Architectural lock-in** ✅ — KB 多年資料模型、ingest pipeline、annotation_merger 三處長期契約
- **Strong stated preference** ✅ — Claude reject A/B 很有 confidence；正是 confirmation bias 該被打的地方

預期 push-back 角度：
- 「為什麼不 monolingual-zh = 全中文 KB 並列、靠 multilingual embedding 跨語言 retrieve 就好」（拒 alias map 維護成本）
- 「grounding pool 大小怎麼控」（既有 100+ Concept、token 預算）
- 「跨語言 acceptance test 怎麼設」（precision/recall 量化）

成本：~10-20min wall + ~$3。對這層次的決定值得。

### 3. PRD-A / PRD-B / PRD-C spec
ADR + panel 收完後寫，每份配 vertical slice issue。

Ship 順序：PRD-A schema 先 ship → PRD-B book（修修主訴）→ PRD-C document。

每 PRD slice 走既有 sandcastle (deep modules) + manual worktree (UI surface) workflow。

## Cross-references

- [ADR-017](../../docs/decisions/ADR-017-annotation-kb-integration.md) — annotation KB integration（被本決定 extend）
- [ADR-021](../../docs/decisions/ADR-021-annotation-substance-store-and-brook-synthesize.md) — annotation substance store（v3 schema 直接複用）
- [ADR-022](../../docs/decisions/ADR-022-multilingual-embedding-default.md) — multilingual embedding default（Q5 跨語言 retrieve 倚賴的前提）
- 5/8 P0 batch (#496-#502) — alias_map seed dict 擴 zh-EN entries 是這場結論的延伸
- [PRD #378](https://github.com/shosho-chang/nakama/issues/378) — EPUB Reader（bilingual mode 既有實作）
- [PRD #351](https://github.com/shosho-chang/nakama/issues/351) — Stage 1 Ingest URL 入口升級
- [PR #451](https://github.com/shosho-chang/nakama/pull/451) — Web Clipper pivot + Robin pipeline wire
- [CONTEXT-MAP.md](../../CONTEXT-MAP.md) — bilingual mode / monolingual-zh mode 詞彙凍結
