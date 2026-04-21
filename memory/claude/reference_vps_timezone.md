---
name: VPS timezone 與 cron
description: VPS 系統時區是 Asia/Taipei，cron 用本機時區觸發 — 新增 cron 時直接寫台北時間，不要算 UTC
type: reference
---

# VPS Timezone 與 Cron

**`timedatectl` 驗證**：VPS Time zone = `Asia/Taipei (CST, +0800)`。

## 影響 cron 設定

Cron daemon 用系統本機時區觸發。所以 `30 5 * * *` 是台北 05:30，**不是 UTC**。

常見踩坑（PR #68 修過一次）：寫 `30 21 * * *` 以為是 UTC 21:30 = 台北 05:30，結果實際在台北 21:30 跑。

## 檢查點

新增 / 修改 cron line 前：
1. `ssh nakama-vps 'timedatectl'` 確認 TZ 還是 Asia/Taipei
2. 想在台北 X 點跑 → cron 直接寫 X（不用 +8 / -8）
3. 若哪天 VPS 改 UTC，cron.conf 所有時間都要加 8 小時；檔頭有 TZ 註記提醒

## 程式碼端

`agents/robin/pubmed_digest.py` 的 digest filename 用 `datetime.now(ZoneInfo("Asia/Taipei")).date()` 而非 `date.today()` — 防 VPS 哪天切 UTC 時檔名錯位（PR #67）。寫日期型 filename / log 時優先這種做法。
