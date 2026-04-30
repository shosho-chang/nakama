---
name: ScheduleWakeup 是 /loop dynamic mode 專用 — 不要當通用「等一下再回來」timer
description: 我在 sandcastle 試水時用 ScheduleWakeup 想稍後檢查 docker build，wakeup 觸發後把原 prompt 整段 re-fire，使用者收到一個看似「我又下了同樣的指令」的訊號，造成困擾
type: feedback
created: 2026-04-30
---

`ScheduleWakeup` tool 的 schema 寫明「Schedule when to resume work in /loop dynamic mode」。它把當下 prompt 排在未來時間點 re-fire — 等於「使用者再說一次同句話」。

**踩到的場景（2026-04-30）：** sandcastle 試水跑 docker build 時，我以為 ScheduleWakeup 是通用「等 270 秒再回來看 build 進度」的 timer，呼叫了一次。Build 在 wakeup 之前就完成（被 background bash + Monitor 通知），但 wakeup 仍按時觸發、把原 prompt 「繼續新的 workflow 進行，我已經裝好 Docker 了」 重新發給我，看起來像是使用者第二次下同樣指令。我必須先解釋「這是 wakeup 不是新指令」才能繼續。

**Why:** Tool 設計目的是 /loop 模式的自我節拍器（每次循環 fire 同 prompt 繼續循環），不是 generic timer。誤用 = 給使用者製造混亂訊號。

**How to apply:**

1. 想等待背景任務 → 用 `Bash(run_in_background: true)` + `TaskOutput(block: true)` 拿結果
2. 想等待狀態變化（檔案/PR/build）→ 用 `Monitor` tool with `until-loop`
3. 想要排程未來工作（不在當下 session 內）→ `schedule` skill
4. **`ScheduleWakeup` 只在 `/loop` 動態節拍 + 自動模式內用**，prompt 應該是循環執行的指令本身（不是「醒來看一下」這類元指令）

**相關記憶：**
- [feedback_run_dont_ask.md](feedback_run_dont_ask.md) — 修修偏好直接執行不問
- [feedback_avoid_one_shot_summit.md](feedback_avoid_one_shot_summit.md) — 不要做超出當下需要的元工程
