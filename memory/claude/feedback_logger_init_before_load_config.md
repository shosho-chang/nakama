---
name: Module-level logger init 必須在 load_config() 之後
description: shared/log.py 的 _initialized cache 鎖死 LOG_FORMAT；scripts module-level get_logger() 在 load_config 前跑會永遠卡 text format
type: feedback
originSessionId: 74954c3c-fdc6-4064-931b-fe3250a7d804
---
`shared/log.py` 的 `get_logger()` 用 module-level `_initialized` flag cache handler，第一次呼叫就決定 format（text vs JSON），之後 LOG_FORMAT env 變動不會 re-init。

3 個 script 都中：
- `scripts/backup_nakama_state.py:51` `logger = get_logger("nakama.backup")` 在 `load_config()` 之前
- `scripts/mirror_backup_to_secondary.py:43`
- `scripts/verify_backup_integrity.py:43`

VPS 雖然 `.env` 設了 `NAKAMA_LOG_FORMAT=json`，但 cron job 跑這些 script 時還是 text format → Phase 3 observability JSON log 在 VPS 沒生效（功能還在，但機器解析少了 structured field）。

**Why:** 2026-04-26 VPS deploy smoke 抓到。同 Phase 3 PR #152 想消滅的「default 失靈」anti-pattern 自己違反。

**修法已落地（PR #162 sweep B）**：`shared/log.py` `get_logger()` 開頭 lazy `from shared.config import load_config; load_config()` 再讀 LOG_FORMAT。`shared.config` 只 import stdlib + yaml + dotenv，無 cycle 風險。`tests/shared/test_log.py::test_get_logger_lazy_loads_dotenv_before_reading_format` 是 regression test。

**How to apply（給未來新 daemon / script）：**
- `logger = get_logger(...)` 出現在 module-top → fix 已生效，不用再 worry
- 但若繞過 shared/log.py 自建 logger，要 sanity check 先 `load_config()` 再讀 env
- 同類 anti-pattern：任何 `_initialized` cache + env-driven config 都要小心 cache lock-in（讀 env 之前先確保 env 已 load）
