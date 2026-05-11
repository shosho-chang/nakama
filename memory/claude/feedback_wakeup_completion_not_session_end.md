---
name: wakeup task done ≠ session end
description: ScheduleWakeup 觸發回來時，若該 wakeup-prompt 任務已在中間 interactive 輪做完，不要當成「無事可做」停掉 — auto mode 下要主動 surface 下一步路線
type: feedback
---

修修明確指出（2026-05-07 ADR-021 implementation 收尾）：當我 schedule wakeup A → 期間 B 輪 interactive 對話我已經把 A 的工作做完 → wakeup 真的觸發時我看到 prompt 跟 B 輪一致，回了「Loop 結束，無下一步」就停。

**Why（修修為什麼覺得錯）**：
- Wakeup 的「特定任務完成」 ≠ session 整體沒下一步
- Auto mode 要求「主動推進、不要被動等指令」
- 即使該 wakeup 的 prompt 任務確實做完了，downstream 路線通常顯而易見：HITL gate 要 surface 給 user / AFK lane 還有 issue 可拆 sandcastle / 該 commit 該 push 等
- 「停下來說沒事」浪費了 auto mode 賦予的 momentum，user 還得再戳我才動

**How to apply**：
1. **Wakeup 觸發回來第一件事**：先檢查該 wakeup-prompt 任務是不是「已在中間 interactive 輪做完了」 — 若是，**不要回 "loop ended"**
2. **改成 surface 下一步**：
   - 若有顯而易見的下一個 AFK 工作 → 直接執行（auto mode 授權）
   - 若需要 user 出手 / HITL → 主動列出 user-side action + 同步可並行的 AFK 工作
   - 若真的整個 session 路線到這裡都做完了 → 列出已完成清單 + 主動建議「建議下個 session 起手」的方向，而不是「沒下一步」
3. **特別注意**：merge sequence / sandcastle 完工這種里程碑後，下一波通常是 HITL 驗 + 啟下個 wave，不是 dead end
