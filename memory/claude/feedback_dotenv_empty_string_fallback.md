---
name: dotenv 空值 KEY= 不會走 os.getenv default 第二參數
description: `KEY=` 在 .env 設成空字串時 `os.getenv("KEY", default)` 回 ""，不是 default — 必用 `or default` 防禦
type: feedback
tags: [env, dotenv, fallback-trap, defensive-coding]
created: 2026-04-26
originSessionId: 6966e360-f96e-4739-b4e8-44d3c16567d4
---
**規則**：module-level 或 hot-path 的 env coercion，必用 `os.getenv("KEY") or "<default>"`，**不要** `os.getenv("KEY", "<default>")`。

**Why:** dotenv 解析 `KEY=`（無值）時得到 `""`（空字串），不是 `None`。`os.getenv("KEY", default)` 的第二參數只在 KEY 完全不存在時才回 default — 已存在但是空字串會回 `""`。下游 `int("")` / `float("")` / 拼 URL 都會炸或拼出錯誤值。

實際踩到（2026-04-26 連兩個 cron 同類 bug）：
1. **早上**：`scripts/backup_nakama_state.py` `int(os.environ.get("NAKAMA_BACKUP_RETENTION_DAYS", "30"))` 撞 `.env` 的 `NAKAMA_BACKUP_RETENTION_DAYS=` 空字串 → 04:00 cron silently failed
2. **下午**：`agents/franky/r2_backup_verify.py` `int(os.getenv("FRANKY_R2_MIN_SIZE_BYTES", str(1*1024*1024)))` 同類爆炸，PR #175 跑 test 時才暴露
3. **下午部署 smoke**：`agents/franky/health_check.py:probe_nakama_gateway` 用 `os.getenv("NAKAMA_HEALTHZ_URL", DEFAULT)` 回 `""`（VPS .env 設空），導致 `httpx.Client.get("")` raise `UnsupportedProtocol: Request URL is missing 'http://' or 'https://'`

**How to apply:**

1. 寫 module-level 預設或 cron 入口前的 env 讀取，**永遠**用 `or` pattern：
   ```python
   # 對 ✓
   timeout = int(os.getenv("MY_TIMEOUT") or "30")
   url = os.getenv("MY_URL") or "http://default.local"

   # 錯 ✗ — KEY="" 時 int("") / "" 都會出事
   timeout = int(os.getenv("MY_TIMEOUT", "30"))
   url = os.getenv("MY_URL", "http://default.local")
   ```

2. 字串型 env（如 prefix）回 `""` 沒副作用 → 用第二參數 OK：
   ```python
   prefix = os.getenv("MY_PREFIX", "")  # 空字串 fallback 沒副作用
   ```

3. PR review 時看到 `int(os.getenv(...))` / `float(os.getenv(...))` / URL 拼接，立刻檢查是不是 `, default` 而不是 `or default`。

4. 既知還沒修的同類 bug（grep `int\\(os\\.getenv\\|float\\(os\\.getenv\\|os\\.environ\\.get` 找）：
   - `agents/usopp/__main__.py` USOPP_POLL_INTERVAL_S / USOPP_BATCH_SIZE
   - `shared/notifier.py` SMTP_PORT
   - `shared/multimodal_arbiter.py` GEMINI_MAX_WORKERS
   - `agents/franky/health_check.py:NAKAMA_HEALTHZ_URL`（小 PR 還沒修）
