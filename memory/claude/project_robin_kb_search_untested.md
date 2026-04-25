---
name: Robin KB Research 功能狀態
description: /kb/research E2E 重驗通過 + kb-search skill PR #142 merged；server-side top_k=8 + UI 呈現待調整
type: project
tags: [robin, kb-search, obsidian]
created: 2026-04-11
updated: 2026-04-25
confidence: high
ttl: 90d
originSessionId: ecac2e9b-d409-4922-b30f-4270e46d6df0
---
## 已完成

**2026-04-13**：
- `/kb/research` endpoint 第一次測試通過（從 Obsidian 成功呼叫、回傳 8 筆結果）
- 修復 `nakama-config.md` 的 `robin_url` 尾部斜線問題（雙斜線導致 404）
- 修復 DataviewJS 自毀 bug：regex 匹配到自己 source code 中的 `<!-- kb-results -->`，改為 DOM 渲染 + localStorage 持久化

**2026-04-25**：skill 化前 E2E 重驗（PR #119 merged 後 `kb_search.py` 改過 — 修了 "Entities" type normalize bug）：
- 中文 query「肌酸對認知功能的影響」→ 8 筆，4.8s。5/8 強命中、2/8 OK、1/8 reach（心肺適能）
- 英文 query「sleep and longevity」→ 8 筆，6.1s。7/8 強命中、1/8 reach
- type normalize fix 沒倒退（`source` / `concept` / `entity` 規範值）
- path 格式一致（`KB/Wiki/Sources/<slug>`）

**2026-04-25 晚（Window B）**：**kb-search skill 化完成 PR #142 merged `6dc9474`**
- `.claude/skills/kb-search/` HTTP wrapper（不 import shared.*，無需 sys.path shim）
- 25 mocked tests + 全 repo 1491 tests 全綠 + ruff 全綠
- `docs/capabilities/kb-search.md` capability card
- 觸發詞：「查 KB / 查知識庫 / kb search / 搜尋知識庫 X / 找關於 X 的資料」
- CLI：`python .claude/skills/kb-search/scripts/search.py --query "..." [--limit N] [--out -|<path>]`（**不能 `python -m`**，目錄帶連字符）
- 本機 only（VPS `DISABLE_ROBIN=1`）

## 已知 enhancement / 待修

- **server-side `TOP_K=8` 寫死** `agents/robin/kb_search.py` → 相關性低時用 reach 補滿（「心肺適能」連兩 query 被推上）。skill `--limit` 只能 client-side 截短，沒法要求 >8。Phase 2 backlog：dynamic top_k 或 confidence threshold（要動 endpoint）
- **skill live endpoint smoke 未跑**：mock test 全綠 + `--help` 通過，但需要修修本機啟 `uvicorn` + 跑活 query 一次（`python .claude/skills/kb-search/scripts/search.py --query "zone 2 訓練" --out -`）才算完整驗收
- **觸發詞驗收**：在 Claude Code 對話打「查 KB <query>」是否觸發 `kb-search` 而非 `keyword-research`，未實機驗
- **KB Research 結果 UI 呈現方式**：修修想再改，具體需求待定

**How to apply:** Skill 已上線可用；修修跑活的 smoke 後可確認 Phase 1 收尾，Phase 2 enhancement（dynamic top_k / vault auto-write / re-ranking）等實際使用反饋再規劃。
