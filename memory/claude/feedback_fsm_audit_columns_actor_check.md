---
name: FSM transition audit columns 寫入要看 from_status，不能只看 to_status
description: state transition 寫 reviewer/reviewed_at 等 audit field 時，不能憑 to_status 一刀切；同 to_status 從不同 from_status 過來語意不同（人類覆核 vs cron reset）
type: feedback
tags: [fsm, audit, approval-queue, hitl]
created: 2026-04-25
updated: 2026-04-25
confidence: high
ttl: 360d
originSessionId: 17b79f57-77eb-4db5-8b5e-806439bf9adf
---
當 FSM `transition()` 寫 audit column（`reviewer` / `reviewed_at` / `review_note`）時，**寫入條件必須同時看 `from_status` + `to_status`**，不能只看 `to_status` 一刀切。

**Why:** Bridge UI Phase 2 (PR #140) 把 approve helper 從「只支援 in_review→approved」擴成「pending|in_review→approved」。第一版誤把條件從 `to_status == "approved" and from_status == "in_review"` 改成只看 `to_status == "approved"` — 這會讓 `claimed → approved`（stale-claim reset cron）也寫 reviewer 欄位，把 cron 的 actor 名字（"stale_claim_reset"）蓋掉原本人類 reviewer 的簽名（"shosho"）。同 to_status 但語意不同：人類覆核要記名字，cron reset 不要。

**How to apply:**
- 設計或擴展 `transition()` 類函式時，audit column 寫入條件的 if 同時 explicit 列出 `from_status in (...)` allowlist，**不要**只用 `to_status == X`
- ADR-006 §4 FSM 表會明示哪些 transition 是「人類動作」vs「自動回收」— 兩類 transition 對 audit 欄位的處理邏輯通常相反
- 對既有 `transition()` 改邏輯時，掃所有 from_status × to_status combination，特別注意 stale reset / retry / cleanup 這類「same to_status 但不同 actor 性質」的 edge

修法（PR #140 shared/approval_queue.py）：
```python
if to_status == "approved" and from_status in ("pending", "in_review"):
    # HITL approve — both pending→approved and in_review→approved record reviewer.
    # claimed→approved (stale reset) intentionally excluded: that path's "actor" is
    # the cron job, not a human reviewer, and we don't want to overwrite the
    # original reviewer column from the prior approve.
    set_fragments.append("reviewer = ?")
    ...
```
