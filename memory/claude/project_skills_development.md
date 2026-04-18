---
name: Skills 開發體系建立
description: 已建立 4 個自建 skill（prior-art-research、kb-ingest、article-compose、transcribe）+ 外部安裝；開發前先調研 + 開源化原則
type: project
created: 2026-04-14
updated: 2026-04-18
confidence: high
ttl: permanent
originSessionId: 2bdc5d8f-6b66-493f-b176-98cd30364d18
---
## 已建立的 Claude Code Skills

### prior-art-research（自建）
- 位置：`~/.claude/skills/prior-art-research/SKILL.md`
- 功能：開發前自動搜尋 6 個通道（本地 → skills.sh → Marketplaces → MCP → GitHub → PyPI），產出 adopt/extend/build 報告
- 經過 2 輪 eval 迭代，pass rate 95%
- 關鍵特性：Step 0 先查本地 codebase、自適應深度、強制閘門

### kb-ingest（自建，2026-04-14）
- 位置：`~/.claude/skills/kb-ingest/SKILL.md` + 7 個 references
- 功能：Robin KB ingest pipeline 改寫為互動式 Skill — 7 步 workflow（讀取→摘要→提取→寫頁→更新索引）
- 1 輪 eval，100% pass rate（vs baseline 87.5%，關鍵差異：互動式檢查點）
- References 從 `f:/nakama/prompts/robin/` + `prompts/shared/` 複製而來
- Prior art research 結論：Build（搜到 claude-knowledge-vault 等通用方案，但 Nakama 的篩選規則和領域特化需求無法直接 adopt）

### article-compose（自建，2026-04-14）
- 位置：`~/.claude/skills/article-compose/SKILL.md` + 4 個 references
- 功能：Brook 文章助手改寫 — 3 階段互動寫作（大綱→逐段→匯出），KB 整合
- 1 輪 eval，100% pass rate，比 baseline 快 48% 省 22% tokens
- 核心價值：防止一口氣寫完（baseline agent 自承 default tendency 是直接寫全文）
- Prior art research 結論：Build（content-research-writer 等通用寫作工具不含 KB 整合和繁中風格）

### transcribe（自建，2026-04-18）
- 位置：**`f:/nakama/.claude/skills/transcribe/SKILL.md` + 6 references**（首次放 repo 內，開源就緒）
- 功能：`scripts/run_transcribe.py` 互動 wrapper — 音檔 sanity / LifeOS Project 自動比對 / 成本預估 / 3 開關勾選 / QC 摘要
- 7 步 workflow，2 個不可略過確認點（音檔路徑 + 最終成本），快速模式可略過中間步驟
- Prior art research 結論：Build（7 個現有 skill/MCP 全部 Whisper-based，中文場景不適用；定位差異化：中文 podcast + 仲裁 pipeline + Obsidian 整合）
- **Eval 策略：skip 正式 eval，週一 2026-04-20 Angie 1hr 實測當 first eval**（理由：pipeline 本身 PR #22 已 E2E 修 9 bug 穩定；skill 只是互動層，合成 eval 成本不划算）
- References：pipeline-overview / cost-estimation / qc-report-format / lifeos-project-format / cli-args-reference / error-recovery
- 開源友善：`LIFEOS_PROJECTS_DIR` + `TRANSCRIBE_UPLOAD_MBPS` env var，無硬編碼個人路徑

### obsidian-markdown（外部安裝，2026-04-14）
- 位置：`~/.claude/skills/obsidian-markdown.md`
- 來源：kepano/obsidian-skills（Obsidian 創建者）
- 功能：教 Claude Code Obsidian Markdown 語法（wikilinks、embeds、callouts、properties）
- 作為 kb-ingest 和 article-compose 的互補基礎

### find-skills（手動安裝）
- 位置：`~/.claude/skills/find-skills.md`
- 來源：vercel-labs/skills
- 功能：搜尋 skills.sh 市場
- 注意：環境沒有 Node.js，`npx skills` 無法使用，改用 WebFetch/WebSearch

## 開發工作流程（更新）

```
使用者提出開發需求
  → prior-art-research 自動觸發
    → Step 0: 查本地 codebase
    → 搜尋外部生態系
    → 產出 adopt/extend/build 報告
  → 使用者確認方向
    → Adopt: 安裝現成工具
    → Extend: fork 改造
    → Build: 用 /skill-creator 建立（含 eval 迭代循環）
```

**Why:** 避免重複造輪子。Eval 2 測試中，without-skill agent 直接寫了 384 行未經授權的程式碼進生產檔案，with-skill agent 則先搜尋再報告。
**How to apply:** 每次收到開發需求時，先讓 prior-art-research skill 跑完再動手。

## Prompt 檔案同步策略

- **Source of truth**：`f:/nakama/prompts/`
- Skills 中的 `references/` 是 prompts/ 的副本（kb-ingest / article-compose）
- 更新 prompt 時需同步更新對應 skill 的 references/
- 目前已複製的對應：
  - `kb-ingest/references/` ← `prompts/robin/` + `prompts/shared/`
  - `article-compose/references/` ← `prompts/brook/` + `prompts/shared/`
- **transcribe 例外**：references 是原生文件（非 prompts 副本），直接寫在 repo 內 `.claude/skills/transcribe/references/`

## Skill 放置策略（2026-04-18 確立）

- **個人通用工具**：放 `~/.claude/skills/`（prior-art-research、kb-ingest、article-compose — 早期決策）
- **Nakama repo 專用 + 開源就緒**：放 repo 內 `.claude/skills/`（transcribe — 新慣例）
- **理由**：開源化原則 — skill + 對應 script 一起 clone 拿走即可，不依賴個人 HOME

**How to apply:** 新 skill 若依賴 repo 內的 script（如 `scripts/run_transcribe.py`），放 repo 內；若是純 prompt-based 互動工具，放 `~/.claude/skills/`。
