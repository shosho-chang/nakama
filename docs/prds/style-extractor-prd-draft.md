---
title: Style Extractor PRD (Draft)
type: output
status: draft
created: 2026-04-18
updated: 2026-04-18
source_refs:
  - https://blog.ninapanickssery.com/p/how-to-make-an-llm-write-like-someone
  - https://dev.to/thatechmaestro/replicate-an-authors-writing-style-using-prompt-engineering-insights-from-an-experiment-with-2hfk
  - https://arxiv.org/html/2509.14543v1
  - https://github.com/lechmazur/writing_styles
  - https://github.com/dlr-sc/style-vectors-for-steering-llms
confidence: medium
tags: [prd, nakama, style-extractor, draft]
related_pages:
  - "[[brook]]"
project: nakama
---

# Style Extractor PRD（草稿）

> 這是一份討論用草稿，定稿後會搬到 `F:\nakama\docs\style-extractor-prd.md` 並開 PR。

---

## 1. 問題陳述

修修在多個平台累積了大量既有寫作（YouTube 腳本、部落格、臉書貼文、Email 等），具有辨識度高的個人 voice。但目前 Brook 和其他 agent 在產生文稿時，**沒有系統化的方式擷取、保存、套用這個 voice**。結果是：

- 每次要 AI 生草稿時，都得手動貼樣本、手動寫風格指令
- 不同 agent（Brook、未來的其他）各自重造輪子
- Voice 一致性無法量測，也難以隨時間演進維護
- 新內容類型（短影音腳本 vs. 長文 vs. IG 貼文）需要不同 sub-voice，沒有統一管理

## 2. 目標 / 非目標

> ⚠️ **V2 重大調整**：style-extractor 不是獨立產品，而是**兩個下游 workflow skill 共用的 building block**。本 PRD 只負責產出 profile 與提供讀取介面；workflow 本身各自有獨立 PRD。

### ✅ 目標（V1）
- 提供 **`style-extract` skill**：給定樣本資料夾，產出 profile（guide.md + messages.json）
- **具體產出三個 profile**：
  1. `修修-人物報導` — 樣本 = 修修既有人物報導文章（已確認有 20+ 篇，挑 8–10 篇）
  2. `修修-科普文章` — 樣本 = 修修既有運動/營養/長壽科普內容
  3. `修修-讀書心得` — 樣本 = 修修既有閱讀心得分享文章
- 提供 **`load_style_profile(profile_name)` 讀取介面**，任何 skill / agent 可呼叫
- 存入 LifeOS Vault，納入知識庫生命週期
- Interactive refinement：產完 guide 後互動式讓修修校對，定稿才寫入 Vault

### ❌ 非目標（V1）
- ❌ **不含兩個下游 workflow**（人物報導 / 科普文章 workflow 是獨立 PRD 與 skill）
- ❌ 不做多使用者 / 多作者平台
- ❌ 不做 fine-tune（prompt-based 先上）
- ❌ 不做 web UI
- ❌ 不解決「AI 仍難精準模仿日常作者隱性風格」根本限制（見 arxiv 2509.14543）— 承認上限

## 3. 使用者場景

### 場景 A：修修建立 `修修-人物報導` profile（一次性）
```
修修指定 8–10 篇既有人物報導文章的 Vault 路徑
    ↓
執行 style-extract skill
    ↓
skill 讀樣本 → LLM 分析 → 產 draft guide
    ↓
互動式校對：「抓到的轉折詞對嗎？」「節奏描述準確嗎？」
    ↓
定稿 → 寫入 KB/StyleProfiles/修修-人物報導.md + .json
    ↓
Entity 頁 [[修修]] 自動更新 style_profiles 欄位
```

### 場景 B：修修建立 `修修-科普文章` profile（同上流程，不同樣本）

### 場景 B2：修修建立 `修修-讀書心得` profile（同上流程，不同樣本）

### 場景 C：下游 workflow 調用 profile（本 PRD 只定義介面，不含實作）
```python
# 未來的 interview-to-article skill 會這樣用：
profile = load_style_profile("修修-人物報導")
messages = profile["few_shot_messages"] + [current_turn]

# 未來的 kb-synthesize skill 會這樣用：
profile = load_style_profile("修修-科普文章")
```

### 場景 D：修修季度重新 extract（voice 會隨時間演進）
```
每季挑最新 8–10 篇 → 重跑 style-extract → 產 v2 → diff 比對 v1 → 審閱確認
```

## 4. 技術架構（Panickssery 五步驟改版）

```
┌─────────────────────────────────────────────────────────┐
│ Input: samples/ (資料夾內多個 .md，保留原始結構)        │
└────────────────────────┬────────────────────────────────┘
                         │
         ┌───────────────┴───────────────┐
         │                               │
         ▼                               ▼
┌─────────────────────┐       ┌─────────────────────────┐
│ Step A: 風格 meta   │       │ Step B: 對每篇樣本      │
│ 分析（一次性）       │       │ 反向生成 prompt（N 次）│
│                     │       │                         │
│ LLM → style guide   │       │ LLM → reverse prompt    │
│     .md             │       │     per sample          │
└─────────┬───────────┘       └───────────┬─────────────┘
          │                               │
          │                               ▼
          │                    ┌─────────────────────────┐
          │                    │ Step C: 兜對話歷史      │
          │                    │ [                       │
          │                    │   {role: user,          │
          │                    │    content: rev_prompt},│
          │                    │   {role: assistant,     │
          │                    │    content: 原文}       │
          │                    │ ] × N                   │
          │                    └───────────┬─────────────┘
          │                                │
          ▼                                ▼
┌─────────────────────────────────────────────────────────┐
│ Output:                                                 │
│   style-guide.md          （人類可讀，給修修校對）      │
│   few-shot-messages.json  （機器用，給 agent import）  │
└─────────────────────────────────────────────────────────┘
```

### 關鍵技術決策

| 決策點 | 選擇 | 理由 |
|---|---|---|
| LLM 選用 | **Opus 4.7**（預設） | 風格分析需深度 nuance；profile 一次性產出但塑造所有下游寫稿，不該省這一塊。年成本約 $9 可忽略 |
| 樣本保留 | **保留原始 markdown 結構**（不扁平化） | 研究一致發現結構=風格的一部分 |
| 樣本數甜蜜點 | **8–10 篇**（下限 5） | 研究一致結論，超過邊際遞減 |
| 反向 prompt 格式 | 自然語言任務描述 | 可讀、可手改 |
| 避免陳腔清單 | 內建 baseline（「not X, it's Y」等） | 消除 LLM 慣性 |
| 輸出位置 | `KB/Wiki/Entities/修修.md` 下的 style_profile 區塊 + `KB/StyleProfiles/` 獨立檔 | 符合 LifeOS 架構 |

## 5. 資料結構

### `style-guide.md` 範本
```markdown
---
profile_name: 修修-youtube-scripts
target_voice: 修修
content_type: youtube_script
language: zh-TW
samples_count: 8
created: 2026-04-18
version: 1
---

# 風格描述：修修 YouTube 腳本 voice

## 節奏與句構
- 平均句長 X 字
- 偏好：短句開場抓注意 → 中長句展開 → 短句收束
- ...

## 語氣與人稱
- 第一人稱「我」為主，偶用「我們」拉近距離
- ...

## 常用轉折與連接
- 其實 / 說到底 / 話說回來

## 慣用詞彙
- 健康用詞：...
- 生活化類比：...

## 結構慣例
- 開場 30 秒必有 hook
- ...

## 反面清單（避免使用）
- 避免：「不可否認」「眾所周知」
- 避免：「it's not X, it's Y」句型
```

### `few-shot-messages.json` 範本
```json
{
  "profile_name": "修修-youtube-scripts",
  "version": 1,
  "created": "2026-04-18",
  "messages": [
    {
      "role": "user",
      "content": "寫一篇 YouTube 腳本，主題是肌酸對大腦的影響，面向一般觀眾，約 5 分鐘長度。"
    },
    {
      "role": "assistant",
      "content": "（修修原文 #1）..."
    },
    {
      "role": "user",
      "content": "..."
    },
    {
      "role": "assistant",
      "content": "（修修原文 #2）..."
    }
  ]
}
```

## 6. LifeOS 整合

### 存放位置
- `KB/StyleProfiles/修修-youtube-scripts.md` + `.json`（或子資料夾）
- `KB/Wiki/Entities/修修.md` 加欄位：
  ```yaml
  style_profiles:
    - [[修修-youtube-scripts]]
    - [[修修-facebook-posts]]
    - [[修修-blog-articles]]
  ```
- `KB/index.md` 新增 StyleProfiles 區段

### 寫入規則
- Style-extractor 可以寫入 `KB/StyleProfiles/`
- 不可修改樣本原檔（樣本從 Vault 其他處讀取，如 `Projects/` 或 `KB/Raw/`）
- 每次執行 append `KB/log.md`

## 7. Skill 設計（V1 主介面）

按照 Nakama 標準化原則「能 skill 化的都 skill 化」，本功能交付為：

### Skill 1：`style-extract`（使用者直接用）
**位置**：`F:\nakama\.claude\skills\style-extract\SKILL.md`

**職責**：產出 / 更新 profile。互動式流程（對話決定樣本範圍 → 產 draft guide → 使用者校對 → 定稿寫入 Vault）。

**觸發**：
- 修修主動呼叫（`/style-extract` 或描述性：「幫我建立人物報導 profile」）
- 季度維護時重跑

### Helper Library：`shared/style_profile.py`（給其他 skill / agent 調用）

**提供 API**：
```python
from shared.style_profile import load_style_profile, list_profiles

profile = load_style_profile("修修-人物報導")
# profile.guide        → 人類可讀 markdown
# profile.messages     → few-shot messages (list[dict])
# profile.version      → 版本號
# profile.metadata     → content_type, language, 樣本數等

profiles = list_profiles()  # 列出所有可用 profile 名稱
```

**任何下游 skill / agent 只需 import 這層**，不需要知道 profile 實體檔在哪。

### 下游 workflow skill（V1 不實作，需獨立 PRD）

- **`interview-to-article`** — 吃 Podcast 逐字稿 → 互動對話決定大綱 → 用 `修修-人物報導` profile 寫稿
- **`kb-synthesize-article`** — 吃 Project + 引用的 KB refs → 互動對話決定大綱 → 用 `修修-科普文章` profile 寫稿
- **`book-reflection-compose`** — 吃某本書（`KB/Raw/Books/`）+ 修修的閱讀筆記 → 互動對話 → 用 `修修-讀書心得` profile 寫稿

## 8. Skill + CLI 呼叫介面

### Skill 呼叫（主要）
```
使用者：「幫我建立人物報導的 style profile」

skill-extract 互動流程：
  1. 問：樣本路徑？（可給資料夾或 .md 檔列表）
  2. 問：profile 名稱？（預設 修修-{content_type}）
  3. 確認樣本清單，顯示字數、建立時間
  4. 跑分析 → 產 draft guide
  5. 逐段校對：「這段描述你的節奏，對嗎？要改？」
  6. 定稿 → 寫 Vault → 更新 [[修修]] Entity 頁
```

### CLI fallback（批次 / 自動化用）
```bash
python -m nakama.style_extract \
  --samples-from "F:/Shosho LifeOS/StyleSamples/修修-人物報導/" \
  --profile-name 修修-人物報導 \
  --content-type profile_article \
  --non-interactive              # 跳過校對直接寫出

# 常用選項
--model opus|sonnet|haiku        # 預設 opus (Opus 4.7)
--min-samples 5  --max-samples 12
--dry-run                        # 不寫檔
```

## 9. V1 交付範圍

### 3 個 PR 切分

| PR | 範圍 | 交付物 |
|---|---|---|
| **PR #1** | 核心 pipeline + helper library | `shared/style_extractor.py`（五步驟 pipeline）+ `shared/style_profile.py`（load/list API）+ 測試 |
| **PR #2** | Skill 包裝 + LifeOS 整合 | `.claude/skills/style-extract/SKILL.md`（互動式校對流程）+ Vault 寫入邏輯 + `[[修修]]` Entity 頁自動更新 + KB/log.md append |
| **PR #3** | 建立 3 個實際 profile | 跑 skill 產出 `修修-人物報導` + `修修-科普文章` + `修修-讀書心得`，三份 profile commit 到 Vault；本 PR 主要是「使用」而非「程式碼」，但需要驗收報告 |

### V1 驗收條件
- ✅ `style-extract` skill 可從頭到尾跑完，互動式校對順暢
- ✅ 產出的 `修修-人物報導` / `修修-科普文章` / `修修-讀書心得` 三個 profile 合理、修修認可
- ✅ 其他 skill 能用 `load_style_profile()` 讀取 profile
- ✅ CLI 可獨立執行（非互動模式），不依賴 web server
- ✅ 測試覆蓋 > 70%
- ✅ profile 檔案格式與 `[[修修]]` Entity 頁連結正確

## 10. 未來擴充

### 🎯 下一步（已確認的明確方向，需獨立 PRD）

**`interview-to-article` skill** — Workflow 1
- 輸入：Podcast 逐字稿（可來自 Transcriber）+ 受訪者基本資料
- 流程：讀稿抽重點 → 互動式對話決定大綱 → 用 `修修-人物報導` profile 寫全文 → 修修校對
- 輸出：人物報導文章，存入 Vault 指定位置

**`kb-synthesize-article` skill** — Workflow 2
- 輸入：Project 路徑（含其引用的 KB refs）+ 主題描述
- 流程：讀 KB refs → 互動式對話決定大綱 → 用 `修修-科普文章` profile 寫全文 → 修修校對
- 輸出：科普文章，存入 Vault 指定位置

### 💡 後續擴充（V2+）

- Profile 版本控制與 diff（每季 re-extract，v1 → v2 比對）
- 風格評分（給一段文字，算 similarity score 對某 profile）
- 更多 profile（臉書貼文、IG、Email、YouTube 腳本）
- Profile merge（合併多 profile 為通用 voice）
- Web UI（在 Brook 頁面嵌入 profile 管理）
- PII redaction pass（樣本自動去識別化）

## 11. 設計決策（V2 已確認）

以下決策全部已於 2026-04-18 對齊：

| 決策點 | 結果 | 備註 |
|---|---|---|
| **Profile 數量** | **三個** | 修修-人物報導、修修-科普文章、修修-讀書心得（voice 差異明顯，分別建立） |
| **樣本數** | **8–10 篇**（下限 5、上限 12） | 研究一致結論，再多邊際遞減 |
| **LLM 預設** | **Opus 4.7** | 深度 nuance 分析需要，一次性產出，年成本 ~$9 可忽略 |
| **樣本路徑** | **直接從 Vault 讀** | 位於 `F:\Shosho LifeOS\StyleSamples\{profile-name}\` |
| **content_type** | **Enum** + `custom` fallback | 下游 workflow skill 靠這欄位自動路由 |
| **Enum 值清單** | `profile_article` / `popsci` / `book_reflection` / `youtube_script` / `facebook_post` / `blog` / `email` / `custom` | V1 用到前三個 |
| **PII redaction** | **不做**（V1） | 樣本都是已發表素材；V2 再評估 |
| **存放結構** | **每 profile 一個資料夾** | `KB/StyleProfiles/{profile-name}/` 下放 `guide.md` + `messages.json` |
| **命名慣例** | `{作者}-{content_type}` 繁中 | 例：`修修-讀書心得` |
| **校對 UX** | **一次 show 全部 + freeform feedback** | V1 先求 shipping；不滿意直接重跑（反正 Opus 成本也才 $2） |
| **Workflow 互動** | **先對話決定大綱，再產文章** | 本 PRD 不含，為下游 workflow skill 各自 PRD 的事 |
| **Skill 化** | **全面 skill 化** | style-extract skill + helper library，所有 agent / skill 透過 `load_style_profile()` 讀取 |

### ✅ 後續 workflow skill 確認

**下游需要三個 workflow skill，各自獨立 PRD：**
1. `interview-to-article`（Podcast → 人物報導）
2. `kb-synthesize-article`（KB refs → 科普文章）
3. `book-reflection-compose`（書 + 閱讀筆記 → 讀書心得）

**不影響本份 PRD 實作範圍**，但要在 §10 明列以提醒後續規劃。

## 12. 風險與限制

| 風險 | 嚴重度 | 緩解 |
|---|---|---|
| LLM 抓不準隱性風格（見 arxiv 2509.14543） | 中 | 誠實標示「需人工校對」，提供編輯介面 |
| Few-shot context 太長 → Brook 每次呼叫變貴 | 中 | 用 prompt caching（Anthropic 支援），大部分 token 打折 |
| 樣本太少時品質差 | 低 | 強制下限 5，少於 5 報錯 |
| 風格隨時間漂移（修修的寫法會變） | 中 | Profile 有版本號，建議每季重新 extract |
| 跨 agent 使用時 profile 格式相容性 | 低 | 用 shared helper 統一讀取邏輯 |

## 13. 研究參考

### 核心方法論
- [Nina Panickssery — How to make an LLM write like someone else](https://blog.ninapanickssery.com/p/how-to-make-an-llm-write-like-someone)：五步驟 pipeline 原始提案
- [DEV — Replicate an Author's Writing Style Using Prompt Engineering](https://dev.to/thatechmaestro/replicate-an-authors-writing-style-using-prompt-engineering-insights-from-an-experiment-with-2hfk)：實驗驗證結構保留的重要性

### 學術
- [arXiv 2509.14543 — Catch Me If You Can? LLMs Still Struggle to Imitate Implicit Writing Styles](https://arxiv.org/html/2509.14543v1)：limitations 誠實面對
- [DLR-SC — Style Vectors for Steering LLMs (EACL 2024)](https://github.com/dlr-sc/style-vectors-for-steering-llms)：進階 activation-level steering（V3+ 參考）

### 開源專案
- [lechmazur/writing_styles](https://github.com/lechmazur/writing_styles)：LLM 寫作風格 benchmark 框架
- [viktorbezdek/definitive-llm-writing-style-guide](https://github.com/viktorbezdek/definitive-llm-writing-style-guide)：風格指南模板庫

### 商業產品（對標）
- Scria AI、CoWrite.ai、Voicepal、Oiti、ToneCloner — 驗證市場需求，多偏英文 + LinkedIn/Twitter

---

## 討論清單（給修修）

### V1 實作前必備
三個 StyleSamples 資料夾各挑 8–10 篇放進去：

```
F:\Shosho LifeOS\StyleSamples\修修-人物報導\     ← 需 8–10 篇 .md
F:\Shosho LifeOS\StyleSamples\修修-科普文章\     ← 需 8–10 篇 .md
F:\Shosho LifeOS\StyleSamples\修修-讀書心得\     ← 需 8–10 篇 .md
```

每篇 `.md` 照 §5 的 frontmatter + 完整原始結構。放完告訴我，我們就可以進入實作階段。

### 仍待決定
- [ ] 三個下游 workflow skill 的 PRD 撰寫順序？（建議先寫最常用的那個）

### 變更紀錄
- **2026-04-18 v1**：初版草稿，泛用 style-extractor 概念
- **2026-04-18 v2**：按修修確認，重新定位為 building block；V1 明確產出兩個 profile；全面 skill 化
- **2026-04-18 v3**：新增第三個 profile（修修-讀書心得）；Q1–Q7 全部對齊（LLM 改 Opus 4.7、enum、每 profile 一資料夾等）；新增待決定：讀書心得 workflow skill 是否獨立做
- **2026-04-18 v4**：確認 `book-reflection-compose` 要獨立做；下游三個 workflow skill 全確立，各自獨立 PRD
