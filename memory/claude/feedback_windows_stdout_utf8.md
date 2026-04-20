---
name: Windows Python stdout 預設 cp1252 無法印中文
description: 任何 Nakama Python script 有中文 print / docstring / argparse help 時，必須在 module 載入時強制 UTF-8，否則 argparse --help 直接炸
type: feedback
---

Python 3.10 在 Windows 預設用 cp1252 編碼 stdout/stderr — 任何中文 print 都會丟 `UnicodeEncodeError: 'charmap' codec can't encode characters`，連 argparse 自己印的 `--help` 都會炸（因為 docstring / help 字串有中文）。

**Why:** PYTHONIOENCODING 環境變數是解法之一但要求使用者設定；強迫每個使用者設 env var 不現實，而且 Claude Code 在自動化環境下呼叫 script 也不會設。

**How to apply:**
任何寫給 Nakama 的 Python script 只要有中文輸出（print、docstring、argparse help），在 module 頂端就強制 reconfigure：

```python
import sys
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")
```

- 放在 module 頂層，`sys.path` 修改之前
- `errors="replace"` 而不是 `strict`，避免邊緣字元再度炸
- `hasattr` 檢查為了相容老 Python / 已被包裝的 stdout
- 現場實例：`scripts/ab_ingest_bench.py` 2026-04-20 修掉這個坑（PR #47 fix commit）
