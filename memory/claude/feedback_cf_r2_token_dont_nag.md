---
name: CF R2 dashboard 舊 token 別再提醒刪除
description: 修修煩躁明確 push back — CF dashboard R2 token 刪不了（admin 限制 / UI 卡住 / 反正攻擊面同），永遠別再列進 todo / 待辦 / 收工觀察 / 反問
type: feedback
created: 2026-05-03
---

修修明確指令（2026-05-03 晚 PR #329 後）：

> 「CF R2 dashboard 刪舊 token 不要再提醒我了，刪不了，很煩。」

**Why**：
- CF dashboard 上那幾個 admin / 全 bucket 舊 token 客觀上**刪不了**（owner permission / UI 限制 / CF API token scope 不夠 — 無法自動）
- 攻擊面跟新 mode-scoped token 沒實質差異（純命名 hygiene）
- 修修已經反覆被提醒、嘗試過、無解，繼續提醒只是 friction

**How to apply**：
- 任何代辦清單 / 收工 memo / 「修修瀏覽器待辦」表 / 反問選項 — **永遠不列「CF R2 dashboard 刪舊 token」**
- 若使用者主動問 R2 token 狀態，回「目前 production 用 mode-scoped 已 sync 完，舊 token 留著沒影響」即可
- `project_nakama_backup_deployed.md` §CF Token list 現況段落已記錄狀態，那是 reference 不是 todo
- `project_session_2026_05_03_pm_backlog_r2.md` 修修待辦表中這條已移除

**對應原則**：[feedback_minimize_manual_friction.md](feedback_minimize_manual_friction.md) — 每個沒意義的提醒都是摩擦力
