---
name: Context budget 自我監控 — Messages 200k 警戒 / 250k 硬上限
description: Opus 4.7 1M context 不代表可以放飛；修修明確上限 Messages 區塊 250k tokens，200k 開始準備收尾（commit 進度 + 寫 handoff memory）；不依賴修修提醒，每輪 reply 前自查
type: feedback
created: 2026-05-06
---

Opus 4.7 1M context window 不代表 token budget 沒上限。修修明確規則：
- **200k tokens (Messages 區塊)** = 警戒線，準備收尾
- **250k tokens (Messages 區塊)** = 硬上限

不是 total context (含 system prompt / tools / memory)，是 **Messages 區塊** — 對話本身的 token。`/context` 指令的 "Messages" line 是觀察點。

**Why**: 2026-05-06 ADR-020 multi-agent panel session 一路寫到 Messages 331k 修修才喊停。LLM 自己沒 budget awareness 預設，要主動監控不要等 surface 警告。Compact 之後 context 重啟，progress 不存就丟。

**How to apply**:

1. **每幾輪 reply 前自查 Messages token 估算**（沒 `/context` 工具時靠 cumulative reply 長度心算 — 一般長 reply ~3-8k tokens / round）
2. **超過 ~150k 開始**：
   - 主動避免長 verbatim dump（特別是 audit / review report 全文重貼）
   - 用 reference path 指向已存的檔案（「完整 audit 在 docs/research/...」）取代重貼
3. **超過 200k 警戒線**：
   - 當前任務告一段落時主動提議 compact
   - **commit 進度 + 寫 handoff memory** 給下個 session 起手用
   - 不再 dispatch 新 agent / 新長任務
4. **超過 250k**：硬上限，立刻收尾不開新動作

**Handoff memory 寫法**:
- file: `memory/claude/project_session_{date}_{topic}.md`
- 內容: 已完成 deliverables list / 凍結決策 / 下次 session 起手要做什麼 / artifact 路徑（檔案、PR、issue）
- 寫法目的: 下個 session 從零起跳能在 5 分鐘內 reload 場景

**為什麼 1M context 不等於沒 budget**:
- 性能：context 越大 LLM「lost in the middle」越強，回覆品質下降
- 成本：context 越大每輪 token cost 越高（input cost + cache 重算）
- Compact friction：超過上限 compact 必丟 nuance，不如主動凍結進 git/memory

**對照 feedback_context_offload_isolated_subsystem**: 那條講「offload 工作到 sandcastle 不爆主線」是延長壽命的策略。本條是「上限到了就收」的硬規則。兩條互補：先 offload 延壽，到 200k 仍主動收。
