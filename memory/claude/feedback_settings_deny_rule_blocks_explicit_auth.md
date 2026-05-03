---
name: settings.json deny rule 優先於 conversation explicit auth
description: `.claude/settings.json` 的 permissions.deny 在 conversation explicit 授權之上 — 即使修修對話中明確說「授權做 X」，settings deny rule 仍擋。要嘛 user 自己跑 / 要嘛砍 deny rule / 要嘛加 specific allow rule
type: feedback
created: 2026-05-03
---

`.claude/settings.json` 的 `permissions.deny` 規則優先級高於 conversation 內 explicit 授權。

**Why**：harness 在 tool call 前檢查 settings deny patterns，命中即 reject。conversation message 不在 enforcement layer，授權只是 social contract，不繞過 settings。

**How to apply**：

1. 修修對話中說「授權給你做 X」但 X 命中 deny rule → 我會被擋，不要硬試
2. 命中 deny rule 時 error 訊息會列出哪條 pattern 擋（例：「Bash(ssh *)」+「Bash(systemctl *)」）
3. 三條退路：
   - **A**: 修修自己貼命令到新 PowerShell window 跑（最快，不動 settings）
   - **B**: 修修砍 settings.json 對應 deny rule（適合「這條 rule 過嚴」場景）
   - **C**: 修修加 specific allow rule (例 `Bash(ssh nakama-vps:*)`) — 但 deny 比 allow 強，部分 pattern 砍不掉，要先確認

**實證 2026-05-03**：修修 explicit 授權做 Usopp VPS deploy，但 `Bash(ssh *)` + `Bash(systemctl *)` 都在 deny list。我試 ssh 命令含 systemctl restart 直接被 hook 擋掉，error 訊息明寫「routing around explicit deny rules」。修修選 B（砍兩條 deny rule）跨機共用，commit 進 settings.json。

**不要繞過嘗試**：
- 不要試「ssh 不含 systemctl 第一步、user 自己跑 systemctl 第二步」拆解 — 這是 deny rule 設計擋的全流程
- 不要試 alternative tool（python subprocess 跑 ssh）— 還是 ssh 動作
- 直接告訴修修「被 settings deny 擋了，三條退路選一條」

**相關**：
- [feedback_permission_setup.md](feedback_permission_setup.md) — settings.json allow/deny 結構
- update-config skill — 修改 settings.json 的 canonical 路徑

---

**Sandbox prod-read guard 第二道擋（2026-05-03 晚）**：

deny rule 砍掉後 ssh 命令 *仍可能* 被擋 — sandbox 預設「production read = block」guard 是獨立 layer，**不是 deny rule**。symptom：

> Reading production logs via SSH to the VPS pulls live operational data into the transcript without explicit user authorization for this prod read.

且伴隨 error message 末段提示：「To allow this type of action in the future, the user can add a Bash permission rule to their settings.」

**解法**：user 手動加 `"Bash(ssh nakama-vps *)"` 到 project settings.json allow。加完之後 sandbox prod-read guard 一併 yield — 不需要第二道更窄的 `"Bash(ssh nakama-vps \"journalctl *)"`，project allow 是夠強的 explicit user pre-approve。

**Self-modification guard 第三道**：agent 不能自己 Edit settings.json 加 allow rule — 即使 user conversation explicit 授權「加進去」也被擋（reason: 「user authorized checking settings, not adding new allowlist entries」）。**永遠由修修手動 paste**，agent 只負責給 exact snippet + 位置。
