---
name: deep_module_wiring_gap
description: 深模組 + 單元測試全綠 ≠ 生產接通；新 public API 必補一條穿過真 caller 的 integration test，否則 wiring gap 會悄悄漏過 CI（PR #414 → PR #419 教訓）
type: feedback
---

新 deep module 配它的單元測試 merge 進主幹**不代表生產有接通**。Slice 5 PR #414 把 `agents/robin/annotation_merger.sync_annotations_for_slug` v2 dispatch 跟 `agents/robin/book_notes_writer.write_notes` 推進來、各自有單元測試全綠，但**兩者都沒被任何生產 caller 呼叫**：

- Router `POST /sync-annotations/{slug}` 還在叫 v1 method `sync_source_to_concepts`
- `book_notes_writer.write_notes` 整個 codebase grep 不到生產呼叫者
- Comments 寫進 `KB/Annotations/{slug}.md` 後就停在那；`notes.md` 從沒產生過
- v2 concept path 寫死 `KB/Concepts/`（真實 path 是 `KB/Wiki/Concepts/`，silently no-op）
- Slice 5 issues #378 #383 標 closed，QA 結論「半成品」

**Why:** 單元測試只驗深模組 contract in isolation — 不驗任一 caller 真的會去 invoke 它。CI 全綠，但 production 完全沒走那條路。 mock LLM + assert file output 的 unit test 是 self-contained sandbox，跟 router 的 wiring 沒有關係。

**How to apply:** 任一 PR 引入新 public API（function / class method / module）必同時補**一條穿過真 caller 的 integration test**：

- 例：新 `write_notes()` 的 unit test 不夠，要再加一條 test 讓 sync entry point（router 或 CLI）真的跑起來、確認 `write_notes` 被呼叫到 + 產生 `notes.md`
- 「我有寫 unit test 啊」不是 PR 通過條件 — 還要回答「production 哪一條 trigger 會 invoke 它？那條 trigger 有測嗎？」
- review 時 grep `<new_function_name>` 在 prod tree（`agents/`, `thousand_sunny/`, `scripts/`）至少有一個 caller — grep 不到 = wiring gap 確定，必補
- Slice 拆解時別把 deep module 跟 wiring 切成兩個 PR — 一起 ship 才能驗 e2e
