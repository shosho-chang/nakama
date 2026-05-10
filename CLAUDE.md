# Nakama — AI Agent 團隊

## 0. Memory

每次對話開始時，讀取 `memory/claude/MEMORY.md` 載入持久記憶索引。需要詳細內容時再讀取個別記憶檔。寫入紀律見 [Memory 寫入紀律](#memory-寫入紀律)節。

---

Health & Wellness / Longevity 內容創作者的 AI Agent 系統。部署於 VPS，產出同步至 Obsidian LifeOS。
詳細架構與 Agent 列表見 `ARCHITECTURE.md`、Agent 職責變更見 `docs/decisions/ADR-001-agent-role-assignments.md`。

**內容流程七層架構見 [`CONTENT-PIPELINE.md`](CONTENT-PIPELINE.md)** — 任何「開發 X」對話必先 anchor 在某個 stage（1 收集 / 2 閱讀註記 / 3 整合 / 4 輸出 / 5 製作 / 6 發布 / 7 監控），不可 spontaneous 開發。詳見 [feedback_pipeline_anchored_planning](memory/claude/feedback_pipeline_anchored_planning.md)。

---

## 工作方法論

改編自 [NYCU-Chung/my-claude-devteam](https://github.com/NYCU-Chung/my-claude-devteam)（再改編自 [tanweai/pua](https://github.com/tanweai/pua)，MIT）。

### 三條紅線（任何任務共同遵守）

1. **閉環意識** — 每個任務有明確 Definition of Done。「做到這裡差不多了」不算完成。
2. **事實驅動** — 每個判斷附實際程式碼的檔案路徑 + 行號。「我猜」「應該是」是違規。
3. **窮盡一切** — 清單不能跳過，乾淨的項目要明確標「已檢查，無問題」，不靜默忽略。

### 模式切換（依任務規模）

| 規模 | 模式 | 行為 |
|------|------|------|
| 單一功能、明確範圍 | **P7 執行** | 讀現狀 → 方案 → 影響分析 → 實施 → 三問自審 → `[P7-COMPLETION]` |
| 多模組 / 3+ 檔案 | **P9 規劃** | 拆成六要素 Task Prompt。禁止自己寫程式 — 輸出是 prompt 不是 code |
| 跨服務 / 5+ sprint | **P10 戰略** | 輸出戰略文件：目標、成功指標、風險、時程、資源分配 |

### P7 完工格式

任何功能性任務完成時用這個格式交付：

```
[P7-COMPLETION]

## What I changed
- `path/to/file.ts` — <一句話說明>

## Impact analysis
- Affected callers: <list, or "none">
- Tests run: <list, or "manual verification via X">

## Self-review
- 方案正確：<答>
- 影響全面：<答>
- 回歸風險：<答>

## Aesthetic direction（僅 UI 任務）
<在 docs/design-system.md 系統內做了什麼選擇、為什麼>

## Remaining work
- <out of scope 但發現的事，或 "none">
```

### P9 六要素 Task Prompt

跨檔任務 dispatch subagent 前，先寫清楚六要素：

1. **目標** — 一句話要達成什麼
2. **範圍** — 改哪些檔案 / 模組（精確路徑）
3. **輸入** — 上游依賴（schema、API spec、前置任務輸出）
4. **輸出** — 交付物（檔案、新 API、測試）
5. **驗收** — 怎樣算完成（通過哪些測試、達到哪些行為）
6. **邊界** — 明確不能碰什麼（避免副作用）

缺任何一項都是違規。

### 高壓觸發條件

以下任一狀況，切換到「窮盡一切、不留後路」的工作狀態：

- 同一任務失敗 2+ 次 → 停止重試原方案，寫三個全新假設逐一驗證
- 即將說「無法解決」「是環境問題」→ 禁止，先查文件讀源碼
- 被動等指令 → 主動找下一步
- UI 產出一看是 AI slop default → 重新設計，不能退回 default

---

## 美學要求

美學是 first-class requirement，不是 nice-to-have。見 [feedback_aesthetic_first_class.md](memory/claude/feedback_aesthetic_first_class.md)。

- **所有 UI surface 出手前讀 [docs/design-system.md](docs/design-system.md)**
- 視覺探索階段用 **Claude Design**（claude.ai/design）— 迭代到滿意再匯出，「交付套件 → Claude Code」單指令 handoff 給我落地
- 拒絕 AI slop default：Inter / Roboto default font、紫漸層、均勻 card grid（`grid-cols-3/4`）、通用 CTA（「Get Started」「Learn More」）、永遠置中 1200px column
- 每個 component 的 states（default / loading / empty / error / hover / focus / active / disabled）都要設計過，不是 afterthought
- tokens 寫進 CSS custom properties 或 tailwind config，**不要硬寫色碼 / 字型在 class 裡**
- accessibility（對比 AAA body、AA secondary、keyboard nav、semantic HTML、`prefers-reduced-motion`）不妥協

**適用範圍**：Bridge UI、Thousand Sunny 甲板儀表板、Chopper 社群 UI、Brook 對外 template。
**不適用**：Obsidian vault 內頁（獨立 CSS snippet）、agent markdown 輸出、Slack 訊息。

---

## Vault 寫入規則

所有 agent 寫入 Obsidian vault 時，必須遵守 LifeOS 的 CLAUDE.md 規則：

- `Journals/` — 完全禁止寫入
- `KB/Raw/` — 不可改寫原文，僅可補全 frontmatter
- `KB/Wiki/` — 主要工作區，可自由建立與更新
- `KB/Annotations/` — Reader annotation store（ADR-017）；僅 Reader 寫入，每個 source slug 一個 `.md` 檔
- `KB/index.md` — 每次新增/更新 Wiki 頁面後必須同步更新
- `KB/log.md` — Append-only，不可修改歷史紀錄
- 頁面內容用繁體中文，frontmatter key 用英文，專有名詞保留原文附英文翻譯

---

## 工作面紀律 (Worktree)

**最高原則：主倉庫 `E:\nakama` 是 control plane，不是 write surface。**  
除非修修明確授權「這次例外可以直接在主 worktree 改這些檔案」，否則不要在 `E:\nakama` 改檔、產生檔案、checkout 工作 branch、commit、或寫 durable memory。詳細血淚見 2026-05-08 cleanup session 與 2026-05-10 multi-window cleanup。

`E:\nakama` 只允許做：

- `git status` / `git log` / `git diff` / 讀檔
- `git fetch` / `git pull --ff-only` 同步 main
- 建立或移除 sibling worktree
- merge 已 review 且 CI green 的 PR

任何會寫檔的任務都必須先開 task-specific sibling worktree：

```powershell
git switch main
git fetch --prune
git pull --ff-only
git worktree add E:\nakama-<topic> -b <branch-name> origin/main
```

- **每個 task 開 sibling worktree**：例如 `E:\nakama-N513-source-map-builder`、`E:\nakama-toast-inbox-importer`、`E:\nakama-memory-update`
- **subagent dispatch** 走 Sandcastle (default) 或本機 `isolation: worktree`（見下節）
- **memory 寫入** 永遠不在 `E:\nakama`，要在 sibling worktree 或專屬 memory worktree
- **禁止 `git add .`**：只 stage 明確列出的 path，避免多視窗下把 unrelated memory、review artifact、screenshot、generated file 混進 PR
- **Stash 紀律**：用 `-m "<message>"` 命名，**不要** `git stash pop stash@{0}`（多視窗下 index 會漂移）— 用 `git stash list` 找 message 再 pop ref name
- **branch 命名**：feature 用 `feat/<topic>`，cleanup 用 `chore/<topic>`，docs/research 用 `docs/<topic>`

## AFK / 並行 dispatch

**Default = Sandcastle**（cloud isolation）。本機 `isolation: worktree` 只用於單一短任務且能盯住整段執行。詳見 `memory/claude/feedback_sandcastle_default.md`。

## Multi-agent 協作 (Claude + Codex)

當 Codex 上線後，記憶系統按 agent + 語言切割。詳細 schema 見 [`memory/SCHEMA.md`](memory/SCHEMA.md)。

- **讀寫範圍**：Claude 讀寫 `memory/shared/**` + `memory/claude/**`；Codex 讀寫 `memory/shared/**` + `memory/codex/**`
- **`memory/shared/**` 強制 bilingual frontmatter**（`name_zh` / `name_en` / `description_zh` / `description_en`）— 否則跨語言 agent 找不到
- **shared/ 是 rare-write curated**：CREATE 自由，UPDATE 走 `.lock` file 機制；衝突絕不 silent last-write-wins，必呼叫 user 介入
- **`INDEX.md` 是 generated artifact**，任何 agent 都不直接編輯（由 `shared/memory_maintenance.py reindex` 重建）
- **session-end trigger**（清對話 / 對話結束 / 收工）走 ephemeral handoff `.nakama/session_handoff_{timestamp}.md`，不寫 durable memory，不 git commit。詳見 [feedback_conversation_end.md](memory/claude/feedback_conversation_end.md)

---

## 檔案刪除規則

禁止使用 `rm` / `rmdir`（已在 `.claude/settings.json` deny）。
需要刪除檔案時，改用 PowerShell 回收桶：

```powershell
# 檔案
Add-Type -AssemblyName Microsoft.VisualBasic
[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile('路徑', 'OnlyErrorDialogs', 'SendToRecycleBin')

# 資料夾
[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteDirectory('路徑', 'OnlyErrorDialogs', 'SendToRecycleBin')
```

---

## Development

```bash
# 安裝依賴
pip install -r requirements.txt

# 設定環境變數
cp .env.example .env  # 填入 API keys

# 手動執行單一 agent
python -m agents.robin
python -m agents.nami
python -m agents.zoro

# 測試
python -m pytest tests/

# 記憶維護
python -m shared.memory_maintenance stats     # 查看記憶統計
python -m shared.memory_maintenance expire    # 清理過期記憶
python -m shared.memory_maintenance archive   # 歸檔舊低信心記憶
```

---

## Memory 寫入紀律

**覆寫平台預設**：不用 `~/.claude/projects/…/memory/`。所有記憶在 repo 內 `memory/`，git 跨平台共用。詳細 schema 見 [`memory/SCHEMA.md`](memory/SCHEMA.md)，行為設計見 `docs/research/2026-05-08-memory-system-redesign-v2.md`。

### 寫入觸發三層

| 層 | 訊號 | 動作 |
|----|------|------|
| **L1 明確** | User 說「記下這個 / save / remember X / update memory」 | 立即寫 durable memory，inline 確認 |
| **L2 強訊號** | User 明確 correction（「不要 X，要 Y」）／強烈 validation（「對，這方向繼續」）／發現新 stable user/project 事實 | 寫 durable memory，inline 確認 |
| **L3 session 邊界** | 「清對話 / 對話結束 / 收工」單獨出現 | **不**自動寫 durable memory；改寫到 `.nakama/session_handoff_{timestamp}.md`（gitignored）|

### Branch 紀律

- Memory commit 走 `paths-ignore`（不跑 CI），可直 push main
- **絕不** 在 feature branch commit memory（會污染 PR scope）
- 寫入 worktree 永遠不是 `E:\nakama`，是 sibling worktree

### 結構速覽

```
memory/
├── shared/{user,project,reference,decision}/    # 跨 agent，bilingual 強制
├── claude/feedback/                             # Claude 專屬行為調整
├── codex/feedback/                              # Codex 專屬行為調整
├── _archive/YYYY-MM/                            # 自動 rotate 的舊記憶
├── INDEX.md                                     # GENERATED, 不可手編
└── SCHEMA.md                                    # 詳細規則
```

過渡期 `memory/claude/*.md`（既有 297 檔）跟 `memory/shared.md` `memory/agents/{robin,franky}.md`（舊 schema）並存，未來 Phase 2 用 `memory_maintenance.py migrate` 漸進遷移。

---

## Agent skills

下列三項 routing 提供給 mattpocock engineering skills（`to-issues`、`to-prd`、`github-triage`、`diagnose`、`tdd`、`improve-codebase-architecture`、`zoom-out`、`grill-with-docs`）讀取，避免每次 skill 跑起來都要重新探勘。

### Issue tracker

GitHub Issues（透過 `gh` CLI）；PR-first 文化，issue 用於需 triage / ADR-level / 外部 reporter 的工作。See `docs/agents/issue-tracker.md`.

### Triage labels

五個 canonical triage label（`needs-triage` / `needs-info` / `ready-for-agent` / `ready-for-human` / `wontfix`）已 provision 在 GH repo，default 對映、無需 override。See `docs/agents/triage-labels.md`.

### Domain docs

Multi-context — 入口為根目錄 `CONTEXT-MAP.md`，各 agent 的 `CONTEXT.md` lazy-created。**ADR 在 `docs/decisions/` 不是 `docs/adr/`**。See `docs/agents/domain.md`.
