---
name: 多 stage 大任務開動前必先報 context budget — 不依賴修修檢查
description: 開 4+ stage / 預期 +30k tokens 以上的任務前，主線必先 quote 當前 context 數字 + 評估是否該 fresh session；2026-05-07 textbook ingest plan 套 Codex critique 連續 edit 把 messages 推到 217.8k 才被修修點出
type: feedback
created: 2026-05-07
originSessionId: a66c3c49-d1d3-427a-a9bd-a850c59c9dbf
---
當意識到接下來要做一個**多 stage 任務 / 預期會堆 context 的工作**，必先**主動 report 當前 context budget**，由我（不是修修）發起。

**Why**: `feedback_context_budget_200k_250k` 規定 200k 警戒 / 250k 硬上限 + 自我監控，但**沒明文「任務級 trigger」**。實際 session 中很容易：
- 一個一個 Edit / Read / 短回應，每個都「沒多少 token」
- 但累積 30+ turn 後 messages 從 80k → 200k+
- 主線一路專注任務，沒回頭看 budget
- 修修 `/context` 才發現破 200k

5/6 burn → 5/7 早段 plan 套 Codex critique 連續 7 個 Edit + Read，messages 推到 217.8k，**修修又得自己檢查**。同 pattern 兩次。

**How to apply:**

下列任一觸發 → 開動前 1 個 turn 必先 report budget：

1. **Stage-based plan 開跑前**（任何 multi-stage doc 從計畫進實作那刻）
2. **預期 +30k tokens 連續工作前**（dispatch 4+ subagent / 連續 5+ Edit / 大 file rewrite）
3. **Codex/Gemini panel review 前**（critique 回來會放大套修工作量）
4. **「現在開始實作」這類 phase transition**

Report 格式（一行夠）：
```
[CONTEXT CHECK] Messages X.Xk / 250k hard. 預估本次 +Yk → Zk. <可繼續 / 建議 fresh session>
```

決策規則：
- 預估會破 230k → **強制建議 fresh session**，不問就建議
- 預估 200-230k → 提醒修修，由他決定是否繼續
- 預估 < 200k → 開跑，但每 5 turn 內查一次

**Anti-pattern signals** （一看到立刻警覺）:

- 「先處理完這個再說 context」 — 不行，先報再處理
- 「應該還夠」 — 沒 quote 數字 = 沒檢查
- 修修先說「context 怎樣？」 — 已經晚了，是失分

**Memory cross-reference**:
- [feedback_context_budget_200k_250k.md](feedback_context_budget_200k_250k.md) — 上層 200k/250k 硬規則
- [feedback_context_offload_isolated_subsystem.md](feedback_context_offload_isolated_subsystem.md) — context 守門靠 offload 子系統不是壓縮 prompt
- [feedback_dispatch_everything_minimize_main_context.md](feedback_dispatch_everything_minimize_main_context.md) — dispatch 一切可 dispatch 工作
