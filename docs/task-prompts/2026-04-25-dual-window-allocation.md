# 雙視窗工作分配單 — 2026-04-25 晚

**問題**：兩個視窗在同一個 working tree（`/Users/shosho/Documents/nakama`）平行開發，互相覆蓋對方未 commit 的檔案。

**解法**：用 `git worktree` 各自開獨立 working tree，branch 互不干擾。檔案分區明確，零重疊。

---

## §0. 一次性 setup（修修在任一視窗跑一次）

```bash
# 在 /Users/shosho/Documents/nakama 下執行（主 tree）
git worktree add ../nakama-window-b feat/bridge-drafts-mutations
```

跑完後：
- **Window A** 留在 `/Users/shosho/Documents/nakama`（主 tree）
- **Window B** 切到 `/Users/shosho/Documents/nakama-window-b`（worktree）

兩個目錄共用 `.git`，但 working tree 獨立 — 檔案、未 commit 改動、checkout branch 完全分離。修修在 IDE 或 terminal 開兩個視窗各自 cd 進去就好。

驗證：
```bash
git worktree list
# 應看到：
#   /Users/shosho/Documents/nakama          de89a6b [main 或 feat/seo-slice-c-brook-integration]
#   /Users/shosho/Documents/nakama-window-b <hash>   [feat/bridge-drafts-mutations]
```

---

## §1. Window A 工作範圍 — Robin Reader UI

**目錄**：`/Users/shosho/Documents/nakama`
**Branch**：開新分支 `feat/robin-reader-ui-polish`（從 main 切）

### 目標
完成 Robin Reader 的兩個本機體驗：metadata 卡片顯示 + 貼上圖片顯示（pending_tasks 待辦）。

### 範圍（可改）
- `agents/robin/agent.py`（Reader 後端如有 metadata endpoint）
- `agents/robin/image_fetcher.py`（圖片貼上 / fetch 流程）
- `thousand_sunny/routers/robin.py`（Reader route）
- `thousand_sunny/templates/robin/reader.html`（Reader 模板）
- `thousand_sunny/templates/robin/index.html`（KB Research 呈現方式如要改）
- `thousand_sunny/static/robin/`（如需新 CSS / JS）
- `tests/agents/robin/test_*.py`（除 `test_kb_search.py` / `test_ingest.py`）
- `tests/thousand_sunny/test_robin_router.py`

### 禁區（絕對不能碰）
- `agents/brook/**`（Slice C PR #139 已 commit，等 review）
- `shared/approval_queue.py`（Window B Bridge mutations 在動）
- `thousand_sunny/routers/bridge.py`（Window B）
- `thousand_sunny/templates/bridge/**`（Window B）
- `tests/test_approval_queue.py`、`tests/test_bridge_router.py`（Window B）
- `agents/robin/kb_search.py`、`agents/robin/ingest.py`（PR #119 / #121 baseline 凍結）

---

## §2. Window B 工作範圍 — Bridge Drafts UI Phase 2

**目錄**：`/Users/shosho/Documents/nakama-window-b`
**Branch**：`feat/bridge-drafts-mutations`（已存在，含 5 個 modified 檔）

### 目標
PR #136/#137 read-only drafts UI → 加 mutation actions（approve / reject / edit / requeue）+ detail 頁顯示 `error_log` + payload pretty-print 改 `model_dump_json(indent=2)`。

### 範圍（可改）
- `shared/approval_queue.py`（補 mutation helpers）
- `tests/test_approval_queue.py`
- `tests/test_bridge_router.py`
- `thousand_sunny/routers/bridge.py`（4 個新 POST endpoint）
- `thousand_sunny/templates/bridge/draft_detail.html`
- `thousand_sunny/templates/bridge/drafts.html`（如需）
- `thousand_sunny/static/bridge/`（如需新 CSS / JS）

### 禁區（絕對不能碰）
- `agents/brook/**`（Slice C PR #139 已 commit，等 review）
- `agents/robin/**`（Window A Reader UI）
- `thousand_sunny/routers/robin.py`、`thousand_sunny/templates/robin/**`（Window A）
- `shared/seo_enrich/**`、`shared/schemas/publishing.py`（SEO 路徑凍結）

詳細六要素見 §「任務 A」in [docs/task-prompts/dual-window-2026-04-25-handoff.md](dual-window-2026-04-25-handoff.md)（如已寫好）— 否則直接看本檔上方「目標」段做。

---

## §3. 共同邊界（兩視窗都不能碰）

- `docs/decisions/ADR-009-*.md`（凍結）
- `docs/task-prompts/phase-1-seo-solution.md`（凍結）
- `.claude/skills/seo-keyword-enrich/**`、`.claude/skills/keyword-research/**`
- `memory/claude/MEMORY.md`（清對話階段才動，避免 merge conflict）
- `config/style-profiles/*.yaml`（已凍結 baseline）

---

## §4. 流程紀律

1. **每個視窗只在自己的 worktree 工作**，cd 出去前確認 `pwd`
2. **commit 前先 `git status`**，確認只有自己範圍的檔案
3. **PR 開出來前再 `git diff main --stat`**，確認沒誤帶對方的檔案
4. **不要互相 rebase / cherry-pick**，等對方 PR merged 後再 `git pull origin main` 同步
5. **碰到衝突就停**，回報修修決定誰先 merge

---

## §5. 視窗結束時

- 工作做完 → 各自開 PR → 各自 merge
- worktree 不再用 → `git worktree remove ../nakama-window-b`（或留著下次重用）

---

## §6. 當前狀態 snapshot

| 項目 | Window A | Window B |
|---|---|---|
| Working tree | `/Users/shosho/Documents/nakama` | `/Users/shosho/Documents/nakama-window-b` |
| Branch | `feat/robin-reader-ui-polish`（待開） | `feat/bridge-drafts-mutations` |
| 最近 commit | （待開新 branch） | (5 modified 檔 uncommitted) |
| 任務 | Robin Reader UI metadata + 貼圖 | Bridge Drafts UI Phase 2 mutations |
| PR target | open 新 PR | open 新 PR |

**已完成**：
- ✅ PR #139 — Slice C Brook seo_context 整合（Window A 已 ship）

---

**首次違反**：今晚兩視窗都 cd 在同一目錄，導致 working tree 互相覆蓋。本分配單就是為了避免下次重蹈覆轍。
