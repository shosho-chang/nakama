# Google Search Console — Setup（已 deprecated）

> ⚠️ **本 runbook 已 deprecated（2026-04-25）。**
> 不要照本檔流程建新 GCP project / service account — 會跟既有 `nakama-monitoring` project 重工。

## 為什麼 deprecated

ADR-009 Slice A 規劃時漏查 repo 內既有 setup runbook。實際上 ADR-007 Franky 早已建立：

- GCP project：`nakama-monitoring`
- Service account：`nakama-franky@nakama-monitoring.iam.gserviceaccount.com`
- 已授權 GSC property：`sc-domain:shosho.tw` + `sc-domain:fleet.shosho.tw`
- env var convention：`GCP_SERVICE_ACCOUNT_JSON`（絕對路徑到下載的 JSON）

`shared/gsc_client.py` reuse 同一份 sa，**不需要新建 GCP project / service account / JSON key**。

## 正確 setup 路徑

1. **Service account / JSON key / GSC property 授權** → 走 [setup-wp-integration-credentials.md §2](setup-wp-integration-credentials.md)
2. **SEO 專用 property 識別字串** → 在 `.env` 補：

   ```bash
   GSC_PROPERTY_SHOSHO=sc-domain:shosho.tw
   GSC_PROPERTY_FLEET=sc-domain:fleet.shosho.tw
   ```

   （GSC property 兩種型態與 `siteUrl` 格式：`sc-domain:<domain>` for domain property、`https://shosho.tw/` for URL-prefix property。）

3. **驗收 smoke test**：

   ```bash
   cd /Users/shosho/Documents/nakama
   source .venv/bin/activate
   python -c "
   from datetime import date, timedelta
   from shared.gsc_client import GSCClient
   import os

   client = GSCClient.from_env()
   today = date.today()
   rows = client.query(
       site=os.environ['GSC_PROPERTY_SHOSHO'],
       start_date=(today - timedelta(days=10)).isoformat(),
       end_date=(today - timedelta(days=3)).isoformat(),
       dimensions=['query'],
       row_limit=10,
   )
   print(f'Got {len(rows)} rows; top: {rows[0] if rows else \"(empty)\"}')"
   ```

   預期：印出 `Got N rows; top: {'keys': ['...'], 'clicks': ..., ...}`。

## 若你已照舊版本流程建了 nakama-seo project

1. GCP console：選 `nakama-seo` project → IAM & Admin → Settings → SHUT DOWN
2. 本機 `.env`：把 `GSC_SERVICE_ACCOUNT_JSON_PATH=` 行刪掉，改用 `GCP_SERVICE_ACCOUNT_JSON=`（指向 Franky 的 sa JSON 路徑）
3. 補 `GSC_PROPERTY_SHOSHO` / `GSC_PROPERTY_FLEET` 兩行

## 教訓

見 [feedback_prior_art_includes_internal_setup.md](../../memory/claude/feedback_prior_art_includes_internal_setup.md) — 新 ADR / 新 skill 設計階段必 grep repo 既有 setup runbooks 與 env keys，避免重複建 service account / GCP project。
