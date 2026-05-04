---
name: 收工 — 2026-05-04 夜 PRD #337 QA + sync LLM JSON P0 揭露
description: PRD #337 三 PR (PR #342/#343/#344) ship 後 QA 跑 Phase 0-3 / 發現 sync LLM systematic invalid JSON P0 / grill 凍 fix plan / Slice 1-5 同期全 merged into main
type: project
created: 2026-05-04
---

從下午 PRD #337 ship → 晚上 dispatch Stage 1 ingest 5 slice → 夜 QA 跑 PRD #337 端到端驗收。預期跑完 Phase 2-6 真主軸，實際 Phase 3 揭露 sync LLM systematic 失敗 → grill 凍 fix plan 收線。

## 1. QA 進度

| Phase | 結果 |
|---|---|
| 0 pre-flight | ✅ |
| 1.1 Inbox markdown | ✅ |
| 1.2-1.4 雙語 / EPUB | ⏭ 跳過（卡點 #3 PubMed 雙語從未實測 / #4 EPUB ingest gap）|
| 2 annotation 持久化 | ✅ 標 / reload / 跨 session 全綠（卡點 #5 渲染位置 minor）|
| **3 sync to Concept** | ⚠️ **第 1 次成功 mutate 3 page；第 2-3 次 systematic 失敗** |
| 4 cross-source aggregate | ❌ 卡點 #7 P0 block |
| 5 edit/delete | ❌ 同上 block |
| 6 project bootstrap | ⏳ 跟 sync 解耦未跑（下次補）|

## 2. P0 真實 bug 揭露（卡點 #7）

`agents/robin/annotation_merger.py:_ask_merger_llm` 對 Opus 4.7 raw text→JSON 契約不穩 — 3 次 sync 觀察 2/3 失敗，server log `merger LLM returned invalid JSON` warning，silent swallow 變空 dict → reader UI「無匹配概念」誤導訊息。**Phase 4-5 工程驗收 block**，Line 2 Stage 2→3 銜接路徑表面綠實際斷。

### 9 條 bug log 全清單（[vault://Inbox/qa-line2-bugs-2026-05-04.md](file:///E:/Shosho LifeOS/Inbox/qa-line2-bugs-2026-05-04.md)）

| # | 主題 | 嚴重度 |
|---|---|---|
| 1 | Reader index 連結沒 url-encode（檔名含 `&` 開不了；4 處 router redirect 同 bug） | minor — 可繞 |
| 2 | Reader 沒 undo / delete UI（標錯只能編檔或 DevTools console） | UX friction |
| 3 | PubMed 雙語從未實測 + frontmatter `/robin/` prefix 連結 404 | minor — Slice 3 範圍 |
| 4 | 中文 EPUB 一般書籍 ingest 流程不存在 — Line 2 critical path 真 blocker | **獨立 PRD 議題** |
| 5 | Annotation callout reload 後位置跑掉（multi-occurrence 抓 first） | ADR-017 anchor 設計題 |
| 6 | Opus 4.7 reasoning model 仍傳 `temperature` → API 400（已 patch annotation_merger.py，stash@{0}；kb_writer.py 同 bug 待修） | 已部分修 |
| **7** | **sync LLM systematic invalid JSON — Phase 4/5 跑不下去** | **🔥 P0 — 下個 PR 修** |

## 3. 卡點 #7 grill 拍板

3 題凍結（詳見 [`docs/plans/2026-05-04-sync-llm-json-contract-fix.md`](../../docs/plans/2026-05-04-sync-llm-json-contract-fix.md)）：

| Q | 拍板 |
|---|---|
| Q1 LLM 契約 | **A. Anthropic tool_use forced JSON**（vs B JSON repair / C 換 Sonnet / D structured outputs） |
| Q2 PR scope | **三件並做**：LLM contract + error surface UI + idempotency short-circuit；不含卡點 #1/#5/#6 |
| Q3 時序 | B 原本是「等 Slice 1 merge」— **Slice 1-5 已全 merged 進 main**（main HEAD `b28343e`），Q3=B 條件已滿足，下個 session 直接開 branch |

## 4. 同期 main update（Slice 1-5 全 ship）

從 9efb4ad 拉到 main HEAD `b28343e`，多了：
- Slice 2 #353 `ae8bae9` — academic source detection 5-layer OA fallback
- Slice 3 #354 `5dfdde3` — 翻譯按鈕 + 雙語 reader
- Slice 5 #356 `87ce172` — 失敗檔丟棄 + annotation 連動刪
- 收工 memo #364 + 2 條 feedback memory（[feedback_sandcastle_env_drift_check](feedback_sandcastle_env_drift_check.md), [feedback_shared_config_slot_anti_pattern](feedback_shared_config_slot_anti_pattern.md)）

Stage 1 ingest unify PRD #351 已**全 5 slice ship**（Slice 1/2/3/5 確定 merged，Slice 4 圖片 first-class 推測一起進）。

## 5. Working tree 狀態（給下個 session 接）

- branch: `docs/2026-05-04-sync-llm-fix-plan`（這個 commit 在這 branch）
- stash@{0}: `annotation_merger.py` 砍 temperature 那行（卡點 #6 部分修 — 下個 session 切 fix branch 用 `git stash pop`）
- stash@{1}: 是更早的 `urlencode-filename-2026-05-04` WIP（無關，可保留或清）
- stash@{2}: `review-pr-320-temp` 殘留（無關）
- 本機 vault `KB/Annotations/` 已有 2 個檔（creatine + cardio QA 標的 annotation）
- `KB/Wiki/Concepts/` 3 個 page 被 mutate（肌少症 / 肌肉萎縮 / 骨骼肌量）— 含 boundary marker
- `/tmp/concept-creatine-before.md` + 3 份 `*-after-1st-sync.md` baselines（QA diff 用，下次清）

## 6. 下個 session 起手（fix/sync-llm-json-contract）

```bash
# 1. 換 branch
git checkout main && git pull
git checkout -b fix/sync-llm-json-contract
git stash pop  # 拿回 annotation_merger.py temperature removal

# 2. 按 plan doc §4 實作
#    a. _ask_merger_llm rewrite 走 tool_use forced JSON
#    b. SyncReport 加 short_circuited field + errors propagate
#    c. reader.html UI 區分 error / empty / short-circuit / success
#    d. tests 6 條
```

預估 2-3h 含 tests。完工後 push + open PR。

## 7. 重要意識

- **「已 ship + 工程綠 ≠ production 可用」** — PR #342/#343/#344 全綠 + auto code-review 通過 + 116 tests passed，但真實使用 2/3 失敗。test gap：所有 sync 相關 test 都 mock `_ask_merger_llm` 回 deterministic dict，從未驗 LLM 真實輸出 robust。下次 PRD ship 前要補 contract test（hit 真 Anthropic API + 跨 prompt size 驗 schema）— 太貴常駐 CI 跑，至少 weekly smoke。
- **「Auto code-review 抓不到 LLM 契約 robustness 題」** — code-review 看 code quality，不看 LLM 輸出穩定性。需要新型驗收：production smoke / canary。
- **silent swallow 是 reliability 反 pattern** — 把 error catch 成空狀態回去 caller 用「empty result」表示，UI 看不出差別。typed result（Result<T, E> pattern）必要。

## Reference

- [docs/plans/2026-05-04-sync-llm-json-contract-fix.md](../../docs/plans/2026-05-04-sync-llm-json-contract-fix.md) — 本軸 fix PR 完整 plan
- [vault://Inbox/qa-line2-bugs-2026-05-04.md](file:///E:/Shosho LifeOS/Inbox/qa-line2-bugs-2026-05-04.md) — 9 條 bug log 完整 trace
- [project_session_2026_05_04_pm_annotation_ship](project_session_2026_05_04_pm_annotation_ship.md) — 下午 PRD #337 ship
- [project_session_2026_05_04_evening_ingest_prd](project_session_2026_05_04_evening_ingest_prd.md) — 晚上 Stage 1 ingest PRD #351 ship
- ADR：[docs/decisions/ADR-017-annotation-kb-integration.md](../../docs/decisions/ADR-017-annotation-kb-integration.md)
- PR #343 (`af1c709`) — sync to Concept page 原始 ship
