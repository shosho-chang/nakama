# EPUB 整本翻譯升級 — Grill Session Prep

> **Stage anchor**：Stage 2 閱讀（[CONTENT-PIPELINE.md](../../CONTENT-PIPELINE.md)）
> **Line 受益**：Line 2 讀書心得 critical path（修修要看英文書）
> **生成日期**：2026-05-05
> **下次對話**：grill session for EPUB 整本翻譯架構決策
> **狀態**：✋ pre-grill，ready 進 grill

---

## 1. 起點：當前翻譯 pipeline 狀態

### 1.1 兩條 EPUB 進入 path（沒打通）

| | **路徑 A：textbook-ingest skill** | **路徑 B：Reader 翻譯按鈕**（PR #354 Slice 3）|
|---|---|---|
| 觸發 | Mac Claude Code Opus 1M 跑 skill | Web reader「翻譯成中文」鈕 |
| 輸入 | 整本 EPUB 檔案路徑 | `Inbox/kb/{slug}.md` 單篇 markdown |
| EPUB 解析 | ✅ `parse_book.py`（ebooklib + bs4）| ❌ 沒有 EPUB upload UI |
| 翻譯 | ❌ **不翻**，產 KB 中文 summary | ✅ Claude Sonnet 4.6 全文翻 |
| 輸出 | KB/Wiki/Sources/{book}/ch{n}.md（英文摘要+ concept stub）| Inbox/kb/{slug}-bilingual.md（雙語）|
| Annotation | ✅ 標 KB Source 頁 | ✅ 標雙語 reader |

**現實 gap**：「整本英文 EPUB 從進系統到翻譯完雙語可讀」**沒有單一按鈕路徑**。

### 1.2 現有翻譯實作

**檔**：[`shared/translator.py`](../../shared/translator.py) (214 lines)

```python
_DEFAULT_MODEL = "claude-sonnet-4-6"
_BATCH_SIZE = 20             # 一次批 20 段
_BATCH_MAX_TOKENS = 16384    # 批次最大輸出
_SEGMENT_MAX_TOKENS = 4096   # 逐段 fallback
```

**System prompt**（第 78-89 行硬寫學術角色）：
```
你是一位專業學術翻譯員，專精生命科學、睡眠醫學、運動科學和營養學。
使用**台灣繁體中文**，遵循台灣學術界術語習慣（非中國大陸用語）。
保留英文人名、機構名、期刊名不翻譯。保留 Markdown 標題符號（#）、粗體（**）、連結等格式。
數字與單位保留英文（如 p < 0.05、95% CI、mg/kg）。

**術語對照表（必須嚴格遵守，不得使用其他譯名）：**
- adenosine → 腺苷
- circadian rhythm → 晝夜節律
... [189 項全部塞 prompt]
```

**User prompt**（第 115-121 行）：
```
請將以下 N 段學術文字翻譯成台灣繁體中文。
回傳純 JSON 陣列，格式：[{"index": 1, "translation": "..."}, ...]
不要有任何其他說明或 Markdown 包裝。

[1]
<segment>

[2]
<segment>
...
```

**Glossary 來源**：[`prompts/robin/translation_tw_glossary.yaml`](../../prompts/robin/translation_tw_glossary.yaml)
- `terms:` 區塊 — 人工維護 189 項（細胞 / 神經 / 睡眠 / 代謝 / 運動 / 營養 / 統計）
- `user_terms:` 區塊 — Robin 自動學習用，可由 `add_glossary_term()` 寫入

**雙語格式**（第 155-179 行）：每段原文後接一個 `> blockquote` 譯文。

### 1.3 現有 pipeline 三大 gap

1. ❌ **無 cross-chapter context** — 每 batch 獨立翻，章與章之間術語/人名各翻各的
2. ❌ **無 genre slot** — 學術 prompt 套全部書類
3. ❌ **無 prompt cache** — 每 batch 重灌 189 項 glossary（成本 → 全價 input）

---

## 2. 修修提的兩個痛點（2026-05-05 對話）

### 痛點 1：跨章節術語/人名飄移
> 「翻譯整本書時，前後文的對照相當重要。否則這一頁的一個專業術語或人名翻完了，下一頁的可能又會不一樣。」

具體例：
- 第一章 `Daniel Kahneman → 康納曼`，第三章可能變 `卡尼曼`
- 第一章 `anchoring → 錨定效應`，第五章可能變 `定錨效應`

### 痛點 2：書類型多樣，單一學術 prompt 不夠
> 「書本的種類也不只是有學術或是健康相關的，也有理財或者是其他比較一般（general）的類型。關於這點，我想應該可以用選擇的方式，使用不同的 prompt。」

修修提議：**genre 選擇 → 換不同 prompt**。社群驗證這是空白方向，不是重複造輪子。

---

## 3. Prior art research（2026-05-05）

### 3.1 Cross-chapter consistency 七種主流方法

| 方法 | 描述 | 採用工具 | 適合度 |
|---|---|---|---|
| **Sample-then-extract glossary** | 取樣 5 chunk（首/末/3 中間）→ LLM 抽 named entity + 重複術語 → canonical 譯名 → 翻譯時動態注入 | ⭐ [`deusyu/translate-book`](https://github.com/deusyu/translate-book) Claude Code skill；WMT 2025 SOTA [DuTerm](https://arxiv.org/html/2511.07461) | ★★★★★ |
| **Sliding window** | 翻第 N chunk 帶前 chunk 末 300 字 + 後 chunk 首 300 字當 read-only context | `deusyu/translate-book` + [`bilingual_book_maker`](https://github.com/yihong0618/bilingual_book_maker) | ★★★★ |
| **Running summary** | LLM 維護 3 段全書摘要 carry forward 給每章 | `bilingual_book_maker --use_context` | ★★★ |
| **Reflection second pass** | 翻完跑審查 LLM 抓不一致 patch | [Andrew Ng `translation-agent`](https://github.com/andrewyng/translation-agent) | ★★★ |
| **1M context whole-book** | Gemini 2.5 Pro / Claude Sonnet 4 1M 整本塞 | n/a | ★★★（output 64K 限制 → 只適合 < 5 萬字書）|
| **Multi-agent**（CEO/Editor/Translator/Proofreader） | 6 個 agent 角色分工 | [TransAgents](https://github.com/minghao-wu/transagents) TACL 2025 | ★（個人 over-engineering）|
| **Vector RAG / TM .tmx** | embed 已翻段落、新章 retrieval | 學術為主 | ★（不划算）|

**Sample-then-extract 是個人工具圈共識**。對 80k 字書建表成本 ~5-10k tokens（vs 翻譯本身 80k tokens 是 noise）。

### 3.2 Genre routing 業界現狀

**絕大多數 OSS 工具沒做 genre routing**：

- `KazKozDev/book-translator`：README 列 5 個 mode，**main 分支實際未實作**
- `bilingual_book_maker`：沒 genre slot，靠 `--prompt` 完全自定義
- `deusyu/translate-book`：沒 genre routing
- Immersive Translate：UI 沒選項，只 "Smart Context" 開關
- `translation-agent`：READMe 提 tone 可改，無內建 profile
- DeepL Pro：只有 formality slider（formal/informal），無 genre

**修修提的方向是社群空白，不是重複造輪子**。

### 3.3 不同書類翻譯側重點

| 書類 | 翻譯重點 | Prompt 元素 |
|---|---|---|
| **學術** | 術語精確、句構嚴謹、引文格式、數字單位保留英文 | "academic translator", "preserve technical terminology" |
| **健康科普** | 術語準確 + 大眾口語化、譬喻保留 | "balance professional with general audience" |
| **理財** | 401(k) / IPO / ETF / leveraged buyout 保留英文、$ 符號、punchy | "Taiwan financial journalist style" |
| **小說** | 對話自然、人名一次決定終身綁定、文化指涉 | "literary translator, natural dialogue" |
| **商業 / 自我成長** | call-to-action 直接、案例本土化 | "actionable, not academic" |
| **哲學 / 歷史** | 已有譯名查傳統（Kant → 康德）、新譯名附原文 | "use established Chinese translations" |

### 3.4 台灣繁中差異程度

理財 / 科技差異最大：
- software → 台「軟體」/ 中「软件」
- server → 台「伺服器」/ 中「服务器」
- optimization → 台「最佳化」/ 中「优化」
- leveraged buyout → 台「槓桿收購」/ 中「杠杆收购」

學術 / 醫學差異中等（mitochondria → 台「粒線體」/ 中「线粒体」）。

→ 你已硬寫 189 項學術表，**理財書要建獨立 glossary**，健康書混合學術+口語。

### 3.5 工具 capability card（8 個）

#### A. ⭐ `deusyu/translate-book`（最對位）

| 欄位 | 內容 |
|---|---|
| Repo | https://github.com/deusyu/translate-book |
| 後端 | Claude（Claude Code skill 形式） |
| Cross-chapter | ✅ Sample-5-chunk glossary 預建 + per-chunk 動態注入 + sliding window prev/next 300 字 + manifest SHA-256 校驗 |
| Genre | ❌ |
| Output | markdown / HTML / DOCX / EPUB / PDF |
| 維護 | 2026 仍活躍 |
| 適用 | **完全對位 nakama stack** — 建議直接 fork pattern |

#### B. `yihong0618/bilingual_book_maker`

| 欄位 | 內容 |
|---|---|
| Repo | https://github.com/yihong0618/bilingual_book_maker |
| 後端 | GPT-5/4 / Claude Sonnet 4 / Gemini / Qwen-MT / DeepL / 15+ |
| Cross-chapter | `--use_context` running summary（無 explicit glossary）|
| Genre | ❌ |
| Output | EPUB（雙語 side-by-side） |
| 維護 | 高，2025 活躍 |
| 適用 | 最低門檻試出雙語 EPUB |

#### C. `KazKozDev/book-translator`

| 欄位 | 內容 |
|---|---|
| Repo | https://github.com/KazKozDev/book-translator |
| 後端 | Ollama 本地 only |
| Cross-chapter | Two-stage（draft + reflect），README 聲稱有 genre 但**未實作** |
| Output | TXT / PDF / EPUB |
| 適用 | 本地 LLM 場景 |

#### D. Immersive Translate（沉浸式翻譯）

| 欄位 | 內容 |
|---|---|
| Tool | https://immersivetranslate.com/en/document/epub-translator/ |
| 後端 | 多家可接 |
| Cross-chapter | "AI Smart Context" pre-summarize 全書（技術細節未公開） |
| Output | 雙語 EPUB |
| 適用 | 不寫程式的個人讀者 |

#### E. `SakuraLLM/SakuraLLM`

| 欄位 | 內容 |
|---|---|
| Repo | https://github.com/SakuraLLM/SakuraLLM |
| 後端 | 自家 fine-tune（日中輕小說專用） |
| 適用 | **不適合英→繁中** |

#### F. `bookfere/Ebook-Translator-Calibre-Plugin`

| 欄位 | 內容 |
|---|---|
| Repo | https://github.com/bookfere/Ebook-Translator-Calibre-Plugin |
| 後端 | Google / ChatGPT / Gemini / DeepL |
| 維護 | 高，v2.4.1（2025 Apr），2.5k stars |
| 適用 | Calibre 用戶 |

#### G. `jb41/translate-book`

| 欄位 | 內容 |
|---|---|
| Repo | https://github.com/jb41/translate-book |
| 後端 | GPT-4 Turbo |
| 維護 | 2024 後未大更新 |
| 適用 | reference only |

#### H. `TransAgents`（學術 PoC）

| 欄位 | 內容 |
|---|---|
| Repo | https://github.com/minghao-wu/transagents |
| Paper | TACL 2025 / [arXiv 2405.11804](https://arxiv.org/abs/2405.11804) |
| 後端 | GPT-4 |
| Cross-chapter | 6 agent（CEO/Senior Editor/Junior Editor/Translator/Localization/Proofreader）|
| 適用 | reference architecture，個人讀者 over-engineering |

---

## 4. 三級升級路徑（候選，待 grill 拍板）

### 級別 1（1 day）— prompt 修 + genre slot + prompt cache

**改動**：

1. `_build_system_prompt(glossary, genre)` 加 `genre` 參數
2. 5+ 個 profile 字典（`academic` / `health` / `finance` / `novel` / `self_help` / `general`）
3. Glossary **不再全 system prompt 塞 189 項**，改「該段命中才注入」（平均 5-10 條/段）
4. 加 Anthropic `cache_control={"type": "ephemeral"}` 標 system + glossary（[Claude Prompt Caching docs](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)）

**效果**：
- ✅ 解痛點 2（genre routing）
- ✅ Prompt cache：50-chunk 書 → input cost ↓ ~90%
- ❌ 不解痛點 1（cross-chapter 飄移）

### 級別 2（2-3 days）— first-pass glossary extraction + sliding window

**新增**：

1. `extract_book_glossary(epub_or_chapters)`：
   - 取 5 個 chunk（首 / 末 / 3 中間）
   - LLM 抽 named entities + 重複術語
   - 對抽出的每 term 給 canonical 譯法
   - 寫 `book_<id>_glossary.json`
2. 翻譯時 glossary 兩層合併：
   - book_glossary（per-book，覆蓋優先）
   - global yaml（189 項 base）
3. `translate_segments(segments, prev_excerpt=None)` 加 `prev_excerpt` 參數
4. `batch_size` 從 20 降到 10-15（給前文 excerpt 留空間）

**效果**：
- ✅ 解痛點 1（人名 / 術語不再章與章打架）
- ✅ 建表成本一本 $0.10–$0.30
- ✅ 翻譯本身因 cache 反而降本

### 級別 3（暫不做）

Reflection pass / character bible / multi-agent / RAG — 等級別 2 翻完 3-5 本書真實踩到才考慮。

---

## 5. 5 個 genre profile starter（待 grill 細化）

```python
GENRE_PROFILES = {
    "academic": (
        "你是一位專業學術翻譯員，專精生命科學、睡眠醫學、運動科學和營養學。"
        "使用**台灣繁體中文**，遵循台灣學術界術語習慣（非中國大陸用語）。"
        "保留英文人名、機構名、期刊名不翻譯。保留 Markdown 標題符號、粗體、連結等格式。"
        "數字與單位保留英文（如 p < 0.05、95% CI、mg/kg）。"
    ),
    "health": (
        "你是一位健康科普譯者，專精睡眠 / 飲食 / 運動 / 情緒主題。"
        "使用**台灣繁體中文**，平衡專業術語準確與大眾閱讀友善。"
        "醫學術語第一次出現時可附英文，後續用中文。"
        "保留作者譬喻 / 案例敘述的口語節奏，不要過度學術化。"
    ),
    "finance": (
        "你是一位台灣財經編輯，專精個人理財 / 投資 / 商業書翻譯。"
        "使用**台灣繁體中文**，遵循台灣財經媒體用詞慣例（非中國大陸用語）。"
        "保留英文金融術語不翻譯：401(k)、IRA、IPO、ETF、leveraged buyout、margin、short selling 等。"
        "保留 $ 符號與英文公司名 / 股票代碼。文字 punchy、不死板。"
    ),
    "novel": (
        "你是一位文學譯者，重視對話自然與敘事節奏。"
        "使用**台灣繁體中文**，遵循台灣文學翻譯慣例。"
        "對每個角色名一次決定譯法（音譯或保留英文），全書綁定不再變動。"
        "保留作者的句子節奏、文化指涉、雙關（必要時加譯註）。"
        "對話用台灣口語，避免直譯腔。"
    ),
    "self_help": (
        "你是一位商業 / 自我成長書籍譯者。"
        "使用**台灣繁體中文**，文字 punchy、call-to-action 直接、不死板。"
        "保留作者的金句、列表、案例敘述節奏。"
        "案例若涉及美國本土文化（學校系統 / 退休金 / 健保），第一次出現時加 1-2 句台灣讀者可理解的補述。"
    ),
    "general": (
        "你是一位專業書籍譯者，使用**台灣繁體中文**。"
        "遵循台灣語言習慣（非中國大陸用語），保留作者風格。"
        "英文人名、機構名、書名、品牌名保留原文。"
    ),
}
```

---

## 6. Grill 議題（下個 session 要拍板）

**Q1（genre 來源）**：3 種來源優先序怎麼定？
- (a) 書 frontmatter 顯式 `genre:` 欄位
- (b) Reader UI dropdown（修修現場選）
- (c) LLM 自動偵測（看樣本判斷）

**Q2（glossary 多層合併）**：三層 priority 怎麼排？
- (a) global `prompts/robin/translation_tw_glossary.yaml`（189 項 base）
- (b) per-book `book_<id>_glossary.json`（sample-extract 產）
- (c) per-genre glossary（理財書專用 finance.yaml？）
- (d) `user_terms` 自動學習區塊

**Q3（extract 取樣策略）**：
- 5 個 chunk 固定 vs 比例（書越長越多 chunk）？
- chunk 大小（一章一個 vs 固定字數）？
- 取樣失敗怎麼辦（書太短 < 5 章）？

**Q4（cache TTL）**：
- ephemeral（5 分鐘）夠嗎？翻一本書 50 chunk × 30 秒 = 25 分鐘，cache 會失效
- 1h cache 是 enterprise tier，有沒有必要？

**Q5（genre 偵測自動化 vs 強制手動）**：
- 強制手動（修修每次選） vs 預設讀 frontmatter / 沒值 fallback `general`
- 自動偵測 cost vs benefit

**Q6（small books / articles 走哪條路）**：
- 級別 2 sample-then-extract 對 5000 字短文意義不大（採樣 = 全文）
- 短文走原本 path 就好？還是統一走新 path？
- threshold 怎麼定？

**Q7（小說 character bible）**：
- 是 `novel` profile 內預設啟用
- 還是另開 `novel_with_bible` mode？
- character bible 跟 sample-extract glossary 合併還是分開存？

**Q8（與 textbook-ingest skill 整合）**：
- skill 走 path A（不翻譯，產中文 summary）
- 級別 1+2 改 path B（翻譯按鈕）
- 兩 path 整合 / 各自獨立 / path A 也接翻譯？

**Q9（reader 端 EPUB upload UI）**：
- 級別 1+2 假設 EPUB 已轉 .md 進 Inbox
- 真要走「整本書 reader 翻譯」要不要先補 EPUB upload UI？
- 還是維持「textbook-ingest 拆 + reader 單章翻」流程？

**Q10（與 P0 #367 invalid JSON 解耦）**：
- #367 是 sync to Concept page LLM 回 invalid JSON（在 `agents/robin/annotation_merger.py`）
- 跟 translator 完全解耦
- 但 Line 2 critical path 兩個都要解才能走完整 flow
- 修 #367 在這個升級之前 / 之後 / 並行？

**Q11（成本天花板）**：
- 級別 1 +genre：每本書影響不大
- 級別 2 +sample-extract：每本書 +$0.10–$0.30 建表
- 翻譯本身：5-15 萬字 → $0.3-1（cache 後 ↓~$0.10-0.30）
- 月翻 5-10 本書預算？

**Q12（驗收標準）**：
- 怎樣算「跨章節一致性」綠了？
  - 同一英文 term 全書唯一中譯（regex 抓）
  - 修修讀 3 本不同類型書感覺自然
- 怎樣算 genre routing 綠了？
  - 5 種 profile 各跑一本書，修修選 3/5 以上覺得 tone 對
- A/B 對照：跑現有 prompt vs 新 prompt 同一本書，diff 看差異

---

## 7. 已決定原則（不再 grill 這幾個）

1. **不做 multi-agent**（TransAgents 風格） — 個人 over-engineering
2. **不做 RAG over chapters** — 沒 ROI
3. **不做 .tmx translation memory** — 個人不跨書
4. **不 fine-tune 自家模型** — 189 項表規模沒必要
5. **不走 1M context whole-book** — output 64K 限制是死結，僅短書（<5 萬字）勉強
6. **以 [`deusyu/translate-book`](https://github.com/deusyu/translate-book) pattern 為主要參考** — Claude / Claude Code / 個人工具完美對位
7. **優先序**：先修 #367 → 級別 1 → 級別 2 → 真實手跑一本書 → 再決定級別 3
8. **本機 only**：翻譯走桌機 / Mac，VPS 不碰（ingest 本來就重 GPU 路徑）
9. **修修 push back 過 / 已凍結的不再問**：CF R2 token、簡 prompt 列舉表

---

## 8. References

### 學術 / 論文
- [How Good Are LLMs for Literary Translation, Really? — NAACL 2025 Best Paper](https://aclanthology.org/2025.naacl-long.548/) / [arXiv 2410.18697](https://arxiv.org/html/2410.18697v1)
- [It Takes Two: Dual Stage Approach for Terminology-Aware Translation — WMT 2025](https://arxiv.org/html/2511.07461)
- [Document-Level MT with LLMs — arXiv 2304.02210](https://arxiv.org/abs/2304.02210)
- [Self-Retrieval from Distant Contexts for Doc-Level MT — WMT 2025](https://www2.statmt.org/wmt25/pdf/2025.wmt-1.13.pdf)
- [Retrieval-Augmented MT with Unstructured Knowledge — arXiv 2412.04342](https://arxiv.org/abs/2412.04342)
- [Can LLMs Learn to Translate from One Grammar Book? — arXiv 2409.19151](https://arxiv.org/abs/2409.19151)
- [TransAgents — TACL 2025 / arXiv 2405.11804](https://arxiv.org/abs/2405.11804)
- [TRITON 2021 Neural fuzzy match repair for TM](https://aclanthology.org/2021.triton-1.14.pdf)

### 工具 repos
- ⭐ [deusyu/translate-book](https://github.com/deusyu/translate-book)
- [yihong0618/bilingual_book_maker](https://github.com/yihong0618/bilingual_book_maker)
- [andrewyng/translation-agent](https://github.com/andrewyng/translation-agent)
- [KazKozDev/book-translator](https://github.com/KazKozDev/book-translator)
- [bookfere/Ebook-Translator-Calibre-Plugin](https://github.com/bookfere/Ebook-Translator-Calibre-Plugin)
- [SakuraLLM/SakuraLLM](https://github.com/SakuraLLM/SakuraLLM)
- [minghao-wu/transagents](https://github.com/minghao-wu/transagents)
- [Immersive Translate](https://immersivetranslate.com/en/document/epub-translator/)

### Anthropic / Google docs
- [Claude Prompt Caching official docs](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
- [Claude 1M context API release — BleepingComputer](https://www.bleepingcomputer.com/news/artificial-intelligence/claude-gets-1m-tokens-support-via-api-to-take-on-gemini-25-pro/)
- [Gemini long-context API docs](https://ai.google.dev/gemini-api/docs/long-context)

### 翻譯實務 prompt 範例
- [ProDoc — Translation prompt patterns](https://prodoc-translations.com/en/blog/prompts-for-translation-tasks/)
- [Pairaphrase 35 ChatGPT translation prompts](https://www.pairaphrase.com/blog/chatgpt-prompts-translation)
- [ByteLingo (台灣) — ChatGPT 翻財報實務](https://bytelingo.co/blog1)

### 相關 nakama 內部
- 現有 pipeline：[`shared/translator.py`](../../shared/translator.py)
- 術語表：[`prompts/robin/translation_tw_glossary.yaml`](../../prompts/robin/translation_tw_glossary.yaml)
- Slice 3 翻譯按鈕路由：[`thousand_sunny/routers/robin.py:687-737`](../../thousand_sunny/routers/robin.py)
- EPUB 解析：[`.claude/skills/textbook-ingest/scripts/parse_book.py:1086-1144`](../../.claude/skills/textbook-ingest/scripts/parse_book.py)
- Annotation merger（#367 卡點）：[`agents/robin/annotation_merger.py:98-180`](../../agents/robin/annotation_merger.py)
- ADR-011 textbook ingest v2：[`docs/decisions/ADR-011-textbook-ingest-v2.md`](../decisions/ADR-011-textbook-ingest-v2.md)
- ADR-017 annotation KB integration：[`docs/decisions/ADR-017-annotation-kb-integration.md`](../decisions/ADR-017-annotation-kb-integration.md)

---

## 9. 下個 session 起手指示

```
1. 讀 docs/plans/2026-05-05-epub-book-translation-grill-prep.md
2. 確認對 §1 當前 pipeline 認知對齊
3. 從 §6 Grill 議題 Q1 開始逐題拍板
4. Grill 結束後：
   - 寫 ADR（如有架構級決策）
   - 開 PRD GH issue + 拆 slice
   - 寫 task prompt（六要素）
5. 不在這份 doc 範圍內：
   - #367 invalid JSON 修法（解耦）
   - reader EPUB upload UI（Q9 先決定要不要做）
```

