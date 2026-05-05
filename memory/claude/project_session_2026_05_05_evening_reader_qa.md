---
name: session_2026_05_05_evening_reader_qa
description: 5/5 evening 走完 EPUB Reader 5 個 slice 端到端 QA + 補 3 個 hidden bug 的 PR + 開 5 條 follow-up issue；下次 session 起手用 #421/#422/#424 dispatch
type: project
---

**5/5 evening 收工 — Reader 5 slice QA 全跑完**

目的是驗收 PR #387/#392/#396/#400/#403/#404/#408/#409/#414 9 條 Slice 1-5 PR 端到端真的可用。結果 5 slice 全綠 但途中**抓到 3 個 hidden bug**（CI 全過、merge 進主幹但實際使用會炸），全補了 PR：

| 補丁 PR | 抓到的 bug |
|---|---|
| **#417** keyboard nav | Slice 1D 漏移植 foliate-js demo 的 host-page keydown / iframe-doc keydown — 第一頁 render 後 ←/→ 完全沒反應，desktop 只剩手機 swipe 能翻頁 |
| **#418** ingest confirm modal | 防誤按 — 30 分鐘 Opus token job 一鍵 fire 太危險，加 dialog gate（fallback 到 native confirm 防 stale page） |
| **#419** Slice 5 wiring | annotation_merger v2 + book_notes_writer 都沒接到 router；comments 從沒寫進 notes.md；v2 concept path 寫死 `KB/Concepts/` 而真實 path `KB/Wiki/Concepts/` — Layer-2 sync **完全沒在動** |

**Why:** Slice 5 PR #414 標題說 closes #383 #378，但只 ship 了 deep module + unit test 沒接 wiring，QA 才暴露。從這抽出兩條共通教訓記憶：
- [feedback_deep_module_wiring_gap.md](feedback_deep_module_wiring_gap.md) — 新 public API 必補穿過真 caller 的 integration test
- [feedback_test_fixture_path_constants.md](feedback_test_fixture_path_constants.md) — fixture 不能複製 hardcoded literal

**How to apply:** 下次 session 起手：
- 三條 ready-for-agent issue 可以直接 dispatch — [#421 sidebar 新增反思入口](https://github.com/shosho-chang/nakama/issues/421) / [#422 queue_processor watch mode](https://github.com/shosho-chang/nakama/issues/422) / [#424 textbook-ingest OS-neutral 文件化](https://github.com/shosho-chang/nakama/issues/424)
- 兩條 needs-info 待 design — [#420 margin notes](https://github.com/shosho-chang/nakama/issues/420)（Brook design pass mockup margin layout）/ [#423 EPUB blob backup](https://github.com/shosho-chang/nakama/issues/423)（R2 / vault sync / lazy fetch 三選一）
- PR review/merge：#418 #419 收工時 open，需要 review

**測試本書**：`KB/Annotations/test-book-1.md`（已驗 v2 schema 三型 union）+ `KB/Annotations/test-book-2.md`（測 ingest queue 走過 has_original=True 路徑）+ `KB/Wiki/Sources/Books/test-book-1/notes.md`（Slice 5 sync 落地產出）；測完想清理走 `data/books/{id}` rmdir 路線。
