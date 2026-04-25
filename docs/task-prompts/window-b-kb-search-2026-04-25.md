# Window B 交接 — 2026-04-25 晚（kb-search skill 化）

**Window A 同時在做**：Robin Reader UI（metadata 卡片 + 貼上圖片）— 改動在 `agents/robin/agent.py` / `agents/robin/image_fetcher.py` / `thousand_sunny/routers/robin.py` / `thousand_sunny/templates/robin/`。

**Window B 絕對不能碰**：
- `agents/robin/**`
- `thousand_sunny/routers/robin.py`
- `thousand_sunny/templates/robin/**`
- `agents/brook/**`（PR #139 等 review）
- `shared/approval_queue.py`、`thousand_sunny/routers/bridge.py`、`thousand_sunny/templates/bridge/**`（PR #140 等 review）

可以 grep / 讀取上述任何檔案當 reference，但**不要修改**。

---

## §1 目標

把 Robin `/kb/research` endpoint 包成 `kb-search` skill — 修修在 Claude Code 裡打 `/kb-search <query>` 就觸發 KB 查詢、跑 Robin retrieval pipeline、回傳結構化結果（chunks + citations + KB Wiki 候選頁面），可被下游 skill / agent 直接消費。

## §2 範圍

| 路徑 | 動作 |
|---|---|
| `.claude/skills/kb-search/SKILL.md` | **新建**：skill frontmatter（name / description / 觸發詞）+ interactive workflow |
| `.claude/skills/kb-search/scripts/search.py` | **新建**：主流程，CLI 接受 `--query` / `--limit` / `--out`，呼叫 `/kb/research` HTTP endpoint，parse SSE / JSON，輸出 markdown |
| `.claude/skills/kb-search/README.md` | **新建**（如 SKILL.md 不夠，可用 README 補 dev 細節） |
| `docs/capabilities/kb-search.md` | **新建**：capability card（依 [feedback_open_source_ready.md](../../memory/claude/feedback_open_source_ready.md)） |
| `tests/skills/kb_search/test_search_pipeline.py` | **新建**：mock HTTP → 驗 parse + markdown 輸出 |
| `agents/robin/kb_search.py` | **只讀**（理解 retrieval pipeline 形狀） |
| `thousand_sunny/routers/robin.py` | **只讀**（找 `/kb/research` endpoint 路徑 + response shape） |
| `.claude/skills/seo-keyword-enrich/SKILL.md` | **只讀**（範本參照：description 寫法 / scaffolding 結構） |
| `.claude/skills/keyword-research/SKILL.md` | **只讀**（範本：CLI script + 觸發詞） |

**不碰**：見上「絕對不能碰」段。

## §3 輸入

| 來源 | 內容 | 狀態 |
|---|---|---|
| `agents/robin/kb_search.py` | retrieval pipeline 實作（chunker + embeddings + ranking） | ✅ 100% test coverage（PR #119） |
| `thousand_sunny/routers/robin.py` `/kb/research` endpoint | 既有 HTTP API（Window A 不會動這個 endpoint，只動 Reader path） | ✅ E2E 2026-04-25 重驗通過 |
| Skill scaffold 範本 | `.claude/skills/seo-keyword-enrich/`（最近一份，3 subagent 並行 dispatch 範例）、`.claude/skills/keyword-research/`、`.claude/skills/project-bootstrap/` | ✅ |
| Skill 開發四坑 | [feedback_skill_scaffolding_pitfalls.md](../../memory/claude/feedback_skill_scaffolding_pitfalls.md) — `python -m` 失效要 sys.path shim / 嵌套 fence 用 4-backtick / `now_fn` forward 到所有 time call site / `(x or fallback)` 守衛在 list-comp and-chain 失效 | ✅ |
| Skill 三層架構原則 | [feedback_skill_design_principle.md](../../memory/claude/feedback_skill_design_principle.md) — 互動 workflow→skill / 確定函式→shared/*.py / agent 只做觸發編排 | ✅ |

## §4 輸出

**Skill SKILL.md 結構**（對齊 seo-keyword-enrich 風格）：

````yaml
---
name: kb-search
description: >
  Search the Robin knowledge base (KB/Wiki + KB/Raw) for relevant chunks
  given a natural-language query. Returns ranked chunks with source citations
  and KB/Wiki page candidates. Use when the user says "查 KB / 查知識庫 /
  kb search / 找關於 X 的資料 / 搜尋知識庫 X". Calls the Robin /kb/research
  endpoint over HTTP — assumes thousand_sunny is running locally or VPS reachable.
---
````

**`scripts/search.py`** CLI 介面：

```bash
python -m kb_search.search --query "zone 2 訓練" --limit 10 --out -
# 或 vault path
python -m kb_search.search --query "zone 2 訓練" --out KB/Research/searches/zone-2-2026-04-26.md
```

**輸出 markdown 範例結構**：

````markdown
---
name: KB search — zone 2 訓練
type: kb-search-result
generated_at: 2026-04-26T03:00:00Z
query: zone 2 訓練
total_chunks: 12
---

# KB Search Result

## Top chunks
1. **KB/Wiki/zone-2-protocol.md** (score 0.87)
   > Zone 2 是 60-70% 最大心率的有氧區間...

## Wiki page candidates
- KB/Wiki/zone-2-protocol.md
- KB/Wiki/aerobic-base-training.md
````

**Capability card** `docs/capabilities/kb-search.md`：能力 / IO / 實測成本（依 [feedback_open_source_ready.md](../../memory/claude/feedback_open_source_ready.md)）。

## §5 驗收

- [ ] `.claude/skills/kb-search/SKILL.md` 觸發詞與 `keyword-research` / `seo-keyword-enrich` 無衝突（grep 交叉檢查）
- [ ] `python -m kb_search.search --query "test"` 跑起來不 crash（前提：本機 thousand_sunny `uvicorn` 在跑；mock 測試走 conftest）
- [ ] `tests/skills/kb_search/test_search_pipeline.py` mock HTTP → 驗結構化輸出 + frontmatter
- [ ] 全 repo `pytest` pass，無 regression
- [ ] `ruff check` + `ruff format` 綠
- [ ] [feedback_skill_scaffolding_pitfalls.md](../../memory/claude/feedback_skill_scaffolding_pitfalls.md) 四坑全避（`python -m` shim / 嵌套 fence / now_fn forward / `(x or fallback)` 守衛）
- [ ] [feedback_dep_manifest_sync.md](../../memory/claude/feedback_dep_manifest_sync.md) — 加新 dep 時 requirements.txt + pyproject.toml 同步
- [ ] capability card 完工
- [ ] P7 完工格式

## §6 邊界

- ❌ 不改 `agents/robin/kb_search.py`（baseline 凍結，PR #119 100% coverage）
- ❌ 不改 `thousand_sunny/routers/robin.py` 任何 endpoint 邏輯（只 read schema）
- ❌ 不在 skill 內 reimplement retrieval pipeline — 透過 HTTP 呼叫 endpoint
- ❌ 不寫 vault Auto-write 邏輯（skill 只回傳 markdown 字串 / 寫到指定 `--out` path；vault rule 整合是後續題）
- ❌ 不做 query expansion / re-ranking（Phase 2 backlog）
- ❌ 不碰 `keyword-research` skill 設定（只讀為範本）

---

**Window A 同時做的 Robin Reader UI** 改動會落在 `/reader` 路由與 templates，與你的 `/kb/research` 消費完全不重疊；如果 commit 前看到 `agents/robin/` 或 `thousand_sunny/routers/robin.py` 出現在你的 `git diff main --stat`，就是出包了，請 reset。

---

## 開工前 checklist（避免再次 working tree 互相覆蓋）

1. **`pwd` 確認在獨立 worktree**：理想是 `cd /Users/shosho/Documents/nakama-window-b`（如修修已用 `git worktree add`），否則跟 Window A 同 tree 會踩到 PR #139 / #140 的 in-flight 改動
2. **開 feature branch**：`git checkout -b feat/kb-search-skill main`（從乾淨 main 切，不要從現有 feat/* branch）
3. **`git status` 確認 clean** 才動手
4. commit 前再 `git diff main --stat` 一次，確認檔案範圍對齊本檔 §2
