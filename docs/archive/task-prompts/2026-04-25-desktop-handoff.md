# Desktop Handoff — 2026-04-25 晚

**Mac 在做**：`/kb/research` E2E 驗證 + Slice C scaffolding 預備（branch off main，等 PR #133 merge）。
**修修在做**：PR #133 T1 benchmark + 4 件手動運維（vault template / xCloud / R2 token / merge gate）。

**桌機可動**：Bridge `/bridge/drafts` UI scaffolding。**單檔組獨立、零衝突**。

---

## 任務：Bridge `/bridge/drafts` UI

### 六要素（P9）

**1. 目標**
給修修一個地方看「Brook 已 compose、等 Usopp publish」的 draft queue — 列表 + 單篇預覽 + 「approve / reject / edit」三按鈕（先 stub，不接 backend mutation）。Phase 1 Wave 2 唯一未動的 UI 缺口。

**2. 範圍（精確路徑）**
- `thousand_sunny/routers/bridge.py` — 加 `/bridge/drafts` route + `/bridge/drafts/{draft_id:int}` detail route
- `thousand_sunny/templates/bridge/drafts.html`（新）— 列表頁
- `thousand_sunny/templates/bridge/draft_detail.html`（新）— 單篇詳情
- `thousand_sunny/templates/bridge/index.html` — 在 hub 加入「Drafts (N pending)」card（這份是現有的 hub landing）
- `tests/thousand_sunny/test_routers_bridge.py` — drafts 兩 route 的 smoke + happy path coverage

**3. 輸入（上游依賴）**
- `shared/approval_queue.py` 的 **公開 API（精確簽名）**：
  - `list_by_status(status: str, *, source_agent: str | None = None, limit: int = 50) -> list[dict[str, Any]]` — 列表用 `list_by_status("pending")` 抓；要算 hub card 數字也用同一支
  - `get_by_id(draft_id: int) -> dict[str, Any] | None` — 詳情用，注意是 **draft_id (int)** 不是 op_id；route URL 用 `/bridge/drafts/{draft_id:int}`
  - status 列舉值（已驗）：`pending` / `in_review` / `claimed` / `approved` / `published` / `failed` / `rejected`（從 transition() 的 ALLOWED_TRANSITIONS 推出）
- `shared/schemas/internal/approval.py` — `ApprovalPayloadV1` schema（drafts 的 `payload` 欄位是這個 dump 出的 JSON）
- 設計系統：**動手前必讀** [docs/design-system.md](docs/design-system.md)，配色/字型/spacing 全用 tokens，**禁止硬寫色碼或 Inter font**
- Bridge 既有 templates（`index.html` 是 hub landing、`memory.html` / `cost.html` / `franky.html` 是 sub-page）— 抄 layout 結構但不要照抄內容

**4. 輸出（交付物）**
- 新檔 2：`drafts.html` + `draft_detail.html`
- 改檔 2：`bridge.py`（route）+ `_landing.html`（hub card）
- Test：`test_routers_bridge.py` drafts 部分 smoke + happy path（mock `list_pending_drafts` + `get_draft`），不打真 DB
- PR title: `feat(bridge): drafts queue UI scaffolding (read-only)`

**5. 驗收**
- `pytest tests/thousand_sunny/test_routers_bridge.py -v` 全綠
- `ruff check . && ruff format --check .` 全綠
- 本機 `uvicorn thousand_sunny.main:app --reload` 起來，瀏覽器打開 `/bridge/drafts`：
  - 沒有 pending draft 時 → empty state（**不要寫「No drafts found」這種 AI slop**，學一下 design-system.md 的 voice）
  - 有 draft 時 → 列表顯示 title / op_id / created_at / status badge
  - 點進去 `/bridge/drafts/{draft_id}` → 顯示 payload (ApprovalPayloadV1) preview + 三 stub button（先 `disabled` + tooltip「Phase 2 待實作」）
  - hub `/bridge` 上的 「Drafts」card 數字 = 真的 pending count
- 隨機按一下 keyboard tab — focus ring 看得到，AAA contrast

**6. 邊界（不能碰）**
- ❌ **不要動 `/bridge/memory` / `/bridge/cost` / `/bridge/franky`** — 那三個都已上 VPS，動到就要重新部署
- ❌ **不要實作 approve/reject/edit mutation** — 留 stub，這輪只做 read-only
- ❌ **不要碰 `shared/approval_queue.py`** — 只能讀它的公開 API
- ❌ **不要新加 deps**（pydantic / jinja / fastapi 之外都不行）
- ❌ **不要碰 `.claude/skills/seo-keyword-enrich/`** — 那是 PR #133 在動的範圍
- ❌ **不要碰 `agents/brook/compose.py`** — 那是 Mac 的 Slice C scaffolding 預備範圍

---

## 工作流程

1. `git checkout -b feat/bridge-drafts-ui-scaffolding`（從 main）
2. 動手前讀 [docs/design-system.md](docs/design-system.md)、看現有 `_landing.html` / `memory.html` 的 token usage
3. 寫測試 → 寫 code → `ruff check . && ruff format .` → `pytest tests/thousand_sunny/`
4. 本機 uvicorn 啟動，瀏覽器實測 4 個 acceptance scenario（empty / list / detail / hub card）
5. PR description 寫清楚「stub button 為 Phase 2 預留」、附 1-2 張 screenshot
6. 自己跑 ultrareview？這個 PR 規模小（< 300 LOC code + tests）可以略過，直接 squash merge

---

## 衝突檢查

桌機這份任務和 Mac 並行做的兩件事**檔案完全不交集**：
- Mac `/kb/research` E2E：只動 `tests/agents/test_robin_kb_research.py` + 可能加 `agents/robin/kb_search.py` 的 docstring，不碰 `thousand_sunny/`
- Mac Slice C scaffolding 預備：只 read & note，不寫 code（等 #133 merge 才動 `agents/brook/compose.py`）

修修 4 件手動任務也不碰 repo 程式碼。

---

## 報告格式

完工後 PR ready for review，貼 PR 連結。如果中途卡住或發現上面假設不成立（例如 `list_pending_drafts()` 不存在或簽名不一樣），停下來 flag，**不要默默改 scope**。
