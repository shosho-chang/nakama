---
name: PowerShell 回收桶命令必嚴格對齊 .claude/settings.json allow prefix
description: nakama 的 PowerShell 刪除 allow rule 是 prefix match，多塞 `-NoProfile` 或 `foreach` loop 會 break 命中；用單一 inline 連續 DeleteFile 呼叫
type: feedback
created: 2026-05-04
---

`.claude/settings.json` 的 PowerShell allow 規則 prefix 是這兩條：

```
Bash(powershell -Command "Add-Type -AssemblyName Microsoft.VisualBasic; [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile*)
Bash(powershell -Command "Add-Type -AssemblyName Microsoft.VisualBasic; [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteDirectory*)
```

**Why**：Claude Code 權限系統用 trailing-`*` prefix match。命令必須**完全以這段為起頭**，中間任何字插入都 break 命中、落到「沒對到 allow → 走 ask/deny 路徑」，看起來像 PowerShell 被 deny 但其實不是 deny rule，是 allow miss。

**How to apply**：刪檔走回收桶時，**只能寫**這個結構：

```powershell
powershell -Command "Add-Type -AssemblyName Microsoft.VisualBasic; [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile('絕對路徑1', 'OnlyErrorDialogs', 'SendToRecycleBin'); [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile('絕對路徑2', 'OnlyErrorDialogs', 'SendToRecycleBin'); ..."
```

多檔批次刪除 = 在同一個 `-Command "..."` 字串裡用 `;` 連續串呼叫，**第一個呼叫必須是 `[FileSystem]::DeleteFile(` 緊接在 `Add-Type ...; ` 後**。

**禁忌**（會 break prefix match）：
- ❌ `powershell -NoProfile -Command "..."`（多了 `-NoProfile` flag）
- ❌ 用 `foreach ($f in @(...)) { ... }` 包起來
- ❌ 用 `Get-ChildItem | ForEach-Object { ... }` 管線
- ❌ 換用 Python `send2trash`（會被視為繞過 deletion 規則的 circumvention，修修會手動 deny）

**Why this matters**：CLAUDE.md 規定「禁止 rm / rmdir」+ 「改用 PowerShell 回收桶」是 deletion 唯一合法路徑；寫錯 prefix → 落 deny → fallback 換工具 → 看起來像繞 rm deny → 修修 manual deny + 信任成本上升。一次寫對是唯一不踩雷的路。

**Trigger**：任何刪檔需求（含 .tmp 清掉、stale branch 殘檔、過期 artifact）→ 起手就照這個結構寫，**不要**用其他 PowerShell 風格。
