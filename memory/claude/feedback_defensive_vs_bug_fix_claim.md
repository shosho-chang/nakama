---
name: 「防 bug」claim 要先能 reproduce bug，不然降級成 defensive hardening
description: 宣稱 code change 修特定 bug 前，先實測 pre-fix code 真的會觸發該 bug；不能 reproduce 就改成 defensive-level claim
type: feedback
originSessionId: 8b186861-89c8-4326-961a-325aea809a05
---
寫 PR body / docstring / commit message「這段 code 防止 X bug」前，先試一遍 pre-fix code 真的會觸發 X。不能 reproduce → claim 降級成 defensive hardening，別貼「真 bug fix」標籤。

**Why:** PR #123（Mac 2026-04-24，`shared/agent_memory.py` `update()` 加 `conn.rollback()`）宣稱「防 dirty-state leak」，但 reviewer 實測 pre-fix code 不會 leak — 單 UPDATE try-block 下 SQLite 本身對 single statement 有自動 statement-level rollback。最終保留 `conn.rollback()` 作 defensive guard（未來同 try-block 加多 statement 才真正起作用），但 claim 降級 + docstring / test / PR body 都改成「collision 後連線 state 乾淨 invariant」而不是「修 dirty-state leak bug」。

**How to apply:**
- 加 safety check 類 PR（rollback / retry / guard clause / defensive validation）寫 claim 前，先 git checkout pre-fix 版本，寫 10-20 行 repro script 試能不能真的觸發 bug
- 可以 reproduce → PR body 寫「fixes reproducible bug: <repro steps>」
- 不能 reproduce → 降級寫「defensive hardening for future <multi-statement / edge case / refactor> extensions」
- review 時對「防 X bug」claim 也要反問：pre-fix 真的會 X 嗎？還是 defensive 就足夠？
- 比較對象：PR #77 pydantic `model_construct()` 跳過 validators 那種是真 reproduce bug（有 failing test 證），跟 PR #123 defensive-only 不同等級

**相關 memory：**
- [feedback_design_rationale_trace.md](feedback_design_rationale_trace.md) — 寫「保留 X 是為了 Y」前要 trace pipeline，不靠直覺（同類：寫 rationale 要有實證）
- [feedback_rendering_truthify_audit_upstream.md](feedback_rendering_truthify_audit_upstream.md) — render 改說法要掃 upstream 真的有對應欄位（同類：訊息不能說謊）
- [feedback_model_construct_bypasses_validators.md](feedback_model_construct_bypasses_validators.md) — pydantic `model_construct` 跳過 validator 是真 reproduce bug（對照組：這種 claim 有實證）
