# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased]

---

## [0.4.1] - 2026-04-10

### Docs
- 建立 `ARCHITECTURE.md`：系統架構、核心模組、資料流、技術選型說明
- 建立 `docs/decisions/` 目錄與 ADR 規範
- `ADR-001`：記錄 v0.1.0 → v0.4.0 的 Agent 職責重新分配決策
- 更新 `README.md`：修正 Usopp/Sanji/Franky/Brook 的職責描述（與程式碼對齊）、新增先決條件、補充 VPS 部署步驟、新增文件索引

---

## [0.4.0] - 2026-04-10

### Added
- `shared/events.py` — Agent 間 Event Bus（`emit` / `consume` / `peek`）
- `state.db` 新增 `agent_events` table，支援跨 agent 事件傳遞與消費追蹤
- `shared/memory.py` — 跨 session 記憶系統（`load_memory` / `save_memory` / `append_memory`）
- `memory/` 目錄，每個 agent 有獨立記憶檔（robin.md）和共用背景（shared.md）
- `BaseAgent.get_memory_context()` — 自動合併 shared + agent 記憶，供注入 system prompt
- Robin 的所有 Claude 呼叫現在自動注入跨 session 記憶
- `shared/retry.py` — `with_retry()` 函數，對 Anthropic API 錯誤自動指數退避重試（最多 3 次）
- `shared/anthropic_client.py` — `ask_claude()` 整合 retry + cost tracking；`set_current_agent()` 設定 context
- `state.db` 新增 `api_calls` table 及 `agent_runs.input_tokens/output_tokens` 欄位
- `shared/state.py` — 新增 `record_api_call()` 和 `get_cost_summary()` 函數
- `BaseAgent.execute()` — 自動呼叫 `set_current_agent()`，所有 Claude 呼叫自動歸因到當前 run
- `shared/prompt_loader.py` — 集中式 prompt 載入器，支援 shared partials 自動插值
- `prompts/` 目錄，集中管理所有 agent 的 prompt：
  - `prompts/shared/writing-style.md` — 語言與格式規範（繁中、專有名詞）
  - `prompts/shared/domain.md` — 領域知識背景（longevity、wellness）
  - `prompts/shared/vault-conventions.md` — 知識庫頁面規範
  - `prompts/robin/` — Robin 的 4 個 prompt（重構，移除重複 persona 行）
  - `prompts/nami/morning_brief.md` — Nami Morning Brief 起始模板
  - `prompts/zoro/intel_report.md` — Zoro 每日情報報告起始模板
- `agents/robin/ingest.py` — 改用 `load_prompt()` 取代舊的 `_load_prompt()`

---

## [0.3.0] — 2026-04-08

### Robin — 閱讀標註系統

#### Added
- **Reader UI** (`/read`)：閱讀介面，可標記重點與新增筆記，標註直接寫入原始 `.md` 檔
  - `Ctrl+B`：標記選取文字為重點（黃色底色）
  - `Ctrl+Shift+C`：對選取文字新增筆記，彈出輸入框後確認，筆記以 Tufte sidenote 顯示於右側空白處
  - Dark mode 切換，偏好儲存於 `localStorage`
  - 標註語法相容 Obsidian（`==highlight==`、`> [!annotation]`）
  - Auto-save：每次標註後自動存回 Inbox 原始檔案
- **閱讀狀態追蹤**：`完成閱讀` 按鈕標記已讀，狀態存入 SQLite `files_read` 表
- **Inbox 卡片更新**：`.md`/`.txt` 檔案顯示「閱讀」按鈕，已讀檔案顯示 badge
- `shared/state.py`：新增 `is_file_read()`、`mark_file_read()`

#### Changed
- Sidenote 渲染改為兩段式：先用 HTML comment placeholder，`marked.parse()` 後再替換，避免被 `<p>` 包裹導致 float 失效
- `summarize.md` prompt：新增 annotation 說明，指示 Claude 優先處理使用者標記的段落
- `extract_concepts.md` prompt：新增 annotation 優先級說明

---

## [0.2.0] — 2026-04-07

### Robin — Web UI

#### Added
- **FastAPI Web UI**（`agents/robin/web.py`）：瀏覽器互動式 ingest 流程，取代終端機操作
  - `/login`：HMAC cookie 密碼保護
  - `/`：Inbox 檔案清單
  - `/start`：啟動 ingest，複製檔案至 `KB/Raw/`
  - `/processing`：SSE 即時進度回饋，透過 `asyncio.to_thread()` 執行 Claude API 不阻塞事件迴圈
  - `/review-summary`：閱讀 Source Summary，輸入引導方向
  - `/review-plan`：勾選要建立/更新的概念與實體頁面
  - `/done`：顯示完成報告
- **Jinja2 templates**：`login.html`、`index.html`、`processing.html`、`review_summary.html`、`review_plan.html`、`done.html`
- **手動 source type 選擇**：Inbox 卡片的 dropdown 可覆寫副檔名推測的類型（article / paper / book / video / podcast）
- `SOURCE_TYPE_TO_RAW_DIR` mapping：依用戶選擇的類型決定 `KB/Raw/` 子資料夾
- `nakama-web.service`：systemd service，部署於 VPS port 8000
- `requirements.txt`：新增 `fastapi`、`uvicorn`、`jinja2`、`python-multipart`
- `.env.example`：新增 `WEB_PASSWORD`、`WEB_SECRET` 欄位

#### Fixed
- `index.md` Sources 區塊改為 Obsidian wikilink 格式（`[[slug]]`），並自動修正舊的 plain-path 格式
- Starlette 1.0 API 相容性：`TemplateResponse` 改用新簽名 `(request, template, context)`

---

## [0.1.0] — 2026-04-06

### Robin — 核心 Pipeline

#### Added
- **Interactive ingest mode**（`--interactive` flag）：一次處理一份文件，支援人工引導
  - `_generate_summary()`：呼叫 Claude 產出結構化 Source Summary
  - `_get_concept_plan()`：根據 Summary + 用戶引導產出概念/實體建立計畫（JSON）
  - `_execute_plan()`：依審核後的計畫建立或更新 Wiki 頁面
  - `_update_index()`：更新 `KB/index.md`，使用 wikilink 格式
- **Concept/Entity 篩選標準**（`extract_concepts.md`）：
  - Concept：需跨來源可重複出現，限 3–5 個
  - Entity（人物）：僅建立有長期參考價值的核心研究者，限 1–3 個
  - 每項需附 `reason` 說明符合標準的原因
  - 支援 `{user_guidance}` 欄位，讓用戶引導重點方向
- **READMEs**：專案頂層與各 Agent（Robin、Nami、Zoro、Usopp、Sanji、Franky、Brook）

#### Fixed
- `extract_concepts.md`：JSON 範例中的 `{}` 改為 `{{}}` 避免 Python `.format()` 解析錯誤

---

## [0.0.1] — 2026-04-05

### 初始建置

#### Added
- 專案骨架：`shared/`、`agents/`、`config/` 目錄結構
- `shared/anthropic_client.py`：`ask_claude()` 封裝（claude-sonnet-4-6，max_tokens=4096）
- `shared/config.py`：`load_config()`、`get_vault_path()`、`get_db_path()`、`get_agent_config()`
- `shared/state.py`：SQLite 狀態管理（`files_processed`、`agent_runs`、`scout_seen`、`community_alerts`）
- `shared/obsidian_writer.py`：`write_page()`、`read_page()`、`append_to_file()`、`list_files()`
- `shared/utils.py`：`slugify()`、`read_text()`（多編碼）、`extract_frontmatter()`
- `config.yaml`：vault 路徑、db 路徑、各 agent 設定
- `.env.example`：API keys 範本
- Robin agent 基礎實作：Inbox 掃描 → `KB/Raw/` 複製 → Summary → Concept/Entity → index/log 更新
- 所有其他 Agent stub（NotImplementedError）
