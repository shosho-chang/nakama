---
name: 收工 — 2026-05-04 晚 Stage 1 ingest 5 slice 全 ship
description: PRD #351 落地：5 個 slice 全 merged（#357 schema/dispatcher + #359 翻譯按鈕 + #360 圖片 + #361 discard + #363 academic 5 層）。Wave 1 並行：1 sandcastle (#353) + 3 Agent (#354/#355/#356) + Slice 2 sandcastle 處理 Slice 1 設計缺陷（attachments_abs_dir slot 衝突）。修修 urlencode WIP stashed on main（fix/reader-url-encode-filename branch）。
type: project
created: 2026-05-04
---

從 evening PRD ship → Wave 1 dispatch → review → merge 全收。Stage 1 ingest URL 入口完整就位。

## 1. 5 PR 全 merged

| PR | Slice | Issue | 路線 | 結果 |
|---|---|---|---|---|
| #357 | 1 tracer bullet | #352 | Agent + 我接手 review fix | merged b5290c6 |
| #363 | 2 academic 5 層 | #353 | sandcastle 1 iter | merged ae8bae9 |
| #359 | 3 翻譯按鈕 | #354 | Agent + 美學 P9 | merged 5dfdde3 |
| #360 | 4 圖片 first-class | #355 | Agent + 我手動 finish | merged b483667 |
| #361 | 5 discard | #356 | Agent + 美學 P9 | merged 87ce172 |

main HEAD `ae8bae9`。58 PRD U-stories U1-U28 全 cover（U29-30 deferred reserved）。

## 2. Slice 1 設計缺陷 → Slice 2 修

Slice 1 (PR #357) `URLDispatcherConfig.attachments_abs_dir` slot 設計成 fetch_fulltext + image fetch 共用 — 但 Slice 2 (academic PDF dir = `KB/Attachments/pubmed`) 跟 Slice 4 (image dir = `KB/Attachments/inbox/{slug}/`) 是不同 path，shared slot 衝突 last-writer-wins。

Slice 2 PR #363 順帶修了 → split into:
- `fulltext_attachments_abs_dir` + `fulltext_vault_relative_prefix`（Slice 2 use）
- `image_attachments_abs_dir` + `image_vault_relative_prefix`（Slice 4 use）

Slice 4 tests (`test_url_dispatcher_image.py` + `test_scrape_translate_endpoint.py`) updated to match。教訓寫進 [feedback_shared_config_slot_anti_pattern](feedback_shared_config_slot_anti_pattern.md)。

## 3. Sandcastle Wave 1 戰績

| Issue | 路線 | 結果 |
|---|---|---|
| #353 academic 5 層 | sandcastle 1 iter | ✓ 32 new tests + 完整 PMID/DOI/arxiv/biorxiv handlers |
| #354 翻譯按鈕 | Agent (UI) | ✓ 13 new tests + reader header button + bilingual short-circuit |
| #355 圖片 | Agent (manual finish 因 PowerShell deny kill agent) | ✓ 11 new tests + image_downloader_fn 整合 |
| #356 discard | Agent (UI + native dialog) | ✓ 28 new tests + DiscardService + native `<dialog>` |

**Sandcastle gotcha 一次性 fix**：API key 輪替後 `.sandcastle/.env` 沒自動同步 → first run AgentError "Invalid API key"。Sync via `grep "^ANTHROPIC_API_KEY=" E:/nakama/.env > E:/sandcastle-test/.sandcastle/.env && echo "GH_TOKEN=$(gh auth token)" >> ...` 後 retry success。教訓寫進 [feedback_sandcastle_env_drift_check](feedback_sandcastle_env_drift_check.md)。

**Sandcastle prompt 排除 UI**：`docs/runbooks/sandcastle-templates/prompt.md` 寫「Aesthetic surfaces stay out of scope — UI / Bridge UI / Brook templates are NOT sandcastle-eligible」。所以原計畫 4 issue 全跑 sandcastle 改成「sandcastle 1 (backend) + Agent 3 (UI/borderline)」混搭。

## 4. 多次 review 戰績

每個 UI-heavy PR (#359 + #361) 跑 multi-lens reviewer（aesthetic + logic）：

| PR | Aesthetic | Logic | 結論 |
|---|---|---|---|
| #357 (Slice 1) | — | 5 blockers + 3 majors | 我接手修 → merge |
| #359 (Slice 3) | 0 blockers (M1 states gap pre-existing) | 0 blockers (M1+M2 follow-up) | ship as-is |
| #361 (Slice 5) | combined: 0 blockers (M1-M4 nits) | combined: 0 blockers | ship as-is |
| #363 (Slice 2) | n/a (sandcastle agent's TDD output 自包) | n/a (我 rebase 過程 verify schema split 對齊) | ship after rebase |

11 件 follow-up 收進 [#358 issue](https://github.com/shosho-chang/nakama/issues/358)。

## 5. Branch protection serial-merge cycle

Branch protection strict + enforce_admins → 每次 merge 後其他 PR 變 BEHIND，需 `gh pr update-branch` + 等 CI re-run + merge。5 PR 全 merge 走 4 次 update-branch + CI cycle，每 cycle ~3-4 min wall。1 次手動 merge conflict resolve（#361 vs #360 imports + #363 vs #361 url_dispatcher.py）— 詳見 [feedback_branch_protection_strict_serial_merge](feedback_branch_protection_strict_serial_merge.md)。

## 6. 修修 pending WIP stashed

修修 在 `fix/reader-url-encode-filename` branch 開的 working copy 改 `templates/robin/index.html` 加 `{{ file.name | urlencode }}`，未 commit。Slice 3 (5dfdde3) + Slice 5 (87ce172) 都動了該 file，working copy 變 unmerged。

我把它 stash 了：`stash@{0}: shosho-WIP-urlencode-filename-2026-05-04`

修修回來後做：
```
git checkout fix/reader-url-encode-filename
git stash pop  # 可能 conflict，需要重新對 templates/robin/index.html 做 urlencode patch
```

或直接 drop stash + 重做 patch（urlencode 一行改）。

## 7. 下個 session 起手

Stage 1 ingest 全完。下一步根據 [CONTENT-PIPELINE.md](../../CONTENT-PIPELINE.md) 七層架構：
- **Stage 2 Reading + Annotation**：reader infra 上週 ship（PRD #337 ADR-017）+ 本週 Slice 3-5（翻譯/discard）。可開始走 Line 2 讀書心得手跑流程驗證實際 UX。
- **Stage 4 Output 工具**：reading session 邊界 / 心得 outline 工具 — PRD #337 §Out of Scope 列為 deferred，等修修手跑兩週浮現痛點後再決定。

修修可開始 manual smoke：paste BMJ Medicine / Lancet eClinicalMedicine URL → 走 5 層 OA path → 看 reader 內容完整 + 圖片 vault-local + 按翻譯鈕 → bilingual reader → annotation → 按 discard 或 ingest。

## Reference

- 早上 PRD ship + 5 grill freeze: [project_session_2026_05_04_evening_ingest_prd](project_session_2026_05_04_evening_ingest_prd.md)
- PRD doc: [docs/plans/2026-05-04-stage-1-ingest-unify.md](../../docs/plans/2026-05-04-stage-1-ingest-unify.md)
- Multi-lens review 方法: [feedback_multi_agent_review_three_lens](feedback_multi_agent_review_three_lens.md)
- Sandcastle runbook: [docs/runbooks/sandcastle.md](../../docs/runbooks/sandcastle.md)
- Stage 1 anchor in 7-layer arch: [CONTENT-PIPELINE.md](../../CONTENT-PIPELINE.md)
