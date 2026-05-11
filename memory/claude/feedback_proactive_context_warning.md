---
name: proactive context warning
description: 達成 session 里程碑時主動提示 context token 用量 + /compact 建議，不要等 user 跑 /context 才發現超載
type: feedback
---

修修明確要求 Claude 在 session token 用量逼近警戒線時主動 surface（2026-05-07 ADR-021 v2 panel session 200K+ 才被發現）。

**Why**：Claude 沒有直接 query token 用量的 tool — 但有的是「**對話結構性資訊**」可估。修修不該每次都靠自己跑 `/context` 才知道。漏掉提示讓他在 session 末才被迫 reset、context-switch cost 比早預警高很多。

**How to apply — 在以下里程碑主動提 context check**：

1. **大 artifact 落地後**：寫超過 ~10K tokens 級的單檔（ADR、長 audit、PRD）後，提一次「現在大概 ~XK tokens，要不要 /context 確認 + /compact？」
2. **Panel review 跑完後**：3 家 audit + integration matrix + ADR 重寫 = 必爆 context，跑完一輪一定要主動提
3. **Commit 後**：commit 完代表「一段工作收束」是天然 reset point，順便提醒
4. **同一 session 跑了 ≥ 3 個 grill 議題 / ≥ 5 個 ADR-level 決策時**：提醒
5. **感受到對話對應 ~150K+ tokens 時**（粗估：100+ rounds 對話、或 > 50 個 tool call、或讀過 10+ 大檔）：主動提

**提醒格式**（簡短、不打斷流程）：

> 順便提一下 — 這場跑下來估超過 ~XXK tokens（[ artifact / panel / commit ] 後是 reset 好時機）。要不要 /context 確認 + /compact 切乾淨再進下一階段？

**反向：什麼情況不要主動提**：

- 純 chat / 對話往返（沒寫長 artifact）
- 修修明顯在 flow 中、提醒會打斷思考
- 上次提醒過、token 沒大幅再增加
