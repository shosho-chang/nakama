---
name: API 成本管理原則
description: Claude Code 費用優化策略 — Sonnet 為主、Opus 按需、禁 1M context
type: feedback
originSessionId: 183fc1e3-11a1-4a92-b462-da80427721e7
---
Opus 4.7（尤其 1M context tier）是主要成本來源，~$200-400/day。2026-04-20 實測：97% 是 Opus 主迴圈，review skill 的 Sonnet/Haiku 只有 3%。

**規則：日常工作用 Sonnet 4.6（200k）**，Opus 只用在 P9 規劃、P10 戰略、複雜 debug。

**Why：** Opus/Sonnet 價差 5x（input $15 vs $3，output $75 vs $15，cache read $1.50 vs $0.30）。1M context tier 再加 1.5x 乘數。預估切到 Sonnet 可省 70%+，月省 $4,000-$6,000。

**How to apply：**
- 全域 settings.json 已改為 `"model": "claude-sonnet-4-6"`（200k）
- 需要 Opus 時在 terminal 下 `/model opus`（不加 `[1m]`）
- 每個 task boundary 下 `/clear` 避免 cache read 複利累積
- code-review skill 的 5 Sonnet + 8 Haiku fan-out 對成本影響小（每次 ~$10），不是優化重點
