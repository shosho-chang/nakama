---
name: Probe registry 必驗證每筆都有 producer caller
description: 加 health-check probe registry 前必須 grep producer call site；docstring 列名單 ≠ producer 已 instrument
type: feedback
created: 2026-04-26
originSessionId: 74954c3c-fdc6-4064-931b-fe3250a7d804
---
建立任何「列舉式 probe registry」（dict / tuple / list mapping 名字到監測 metadata）前，**必須 grep 確認 registry 裡每個 entry 都有實際的 producer call site**（writer 端真的在 emit 對應的資料）。Docstring 列出「conventional names」≠ producer 已 instrument。

**Why:** PR #170 Phase 5B-1 cron staleness probe 把 `shared/heartbeat.py` docstring 列的 9 個 conventional job_name 全部塞進 `CRON_SCHEDULES`，但 sub-agent reviewer grep 後發現只有 3 個（backup/mirror/integrity）有 `record_success` caller。其餘 6 個是 docstring intent 但從未 instrument → probe 永遠看 `hb is None` 走 skipped 路徑 → false-green，比沒 probe 更糟（operator 信任綠燈）。trim 至 3 verified entries 才合格 merge。同類陷阱：probe registry / metric name list / capability card list 全部適用。

**How to apply:**
- 加 entry 時 grep `record_<X>("<name>")` / `emit("<name>")` / `<name> in producer_namespace` 確認 producer 端真的 fire
- registry 上方 comment 寫嚴格規則「entry 必對應到實際 producer call site；待 instrument 的列入 follow-up backlog 不入 dict」
- Reviewer 提示 sub-agent 檢查「registry 每筆都有對應 producer」當 BLOCKER 級別審查項
- 適用範圍：health probe registry、metric registry、cron schedule registry、feature flag registry、event consumer registry
