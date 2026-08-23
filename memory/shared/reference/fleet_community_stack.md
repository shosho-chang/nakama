---
type: reference
visibility: shared
agent: shared
confidence: high
created: 2026-08-16
expires: permanent
tags: [fleet, fluent-community, fluentcart, wordpress, vps, sanji]
name_zh: 自由艦隊社群站存取入口
name_en: Fleet community site access entry point
description_zh: 自由艦隊社群（FluentCommunity + FluentCart）跑在 VPS 的 fleet.shosho.tw，不是 shosho.tw 主站；含 SSH / DB / wp-cli 存取方式與領域知識文件位置。
description_en: The Fleet community (FluentCommunity + FluentCart) runs at fleet.shosho.tw on the VPS, not the shosho.tw main site; covers SSH/DB/wp-cli access and where the domain knowledge lives.
---

# Fleet Community Stack — Access Entry Point

## Where it lives

自由艦隊社群站是 **`fleet.shosho.tw`**，跟 `shosho.tw` 主站是**同一台 VPS 上的兩個獨立
WordPress**。FluentCommunity / FluentCart / FluentCRM 全部裝在 `fleet.shosho.tw`，
`shosho.tw` 上一個都沒有。找錯站會白忙一場。

| 項目 | 值 |
|---|---|
| SSH host | `nakama-vps`（`~/.ssh/config`） |
| Site path | `/var/www/fleet.shosho.tw` |
| DB name | `db2_fleet_shosho` |
| Table prefix | `zcjf_` |
| Site user | `u2_fleet_shosho` |
| wp-cli | `/usr/local/bin/wp` |

wp-cli 必須以站台使用者身分執行：

```
ssh nakama-vps
cd /var/www/fleet.shosho.tw && sudo -u u2_fleet_shosho wp <command>
```

唯讀查詢加 `--skip-plugins --skip-themes` 較快；但要呼叫 plugin 的 Model 層
（`wp eval` / `wp eval-file`）時**不能加**，否則 class 不會載入。

## Environment notes

- **LiteSpeed object cache drop-in 啟用中**（`wp-content/object-cache.php`）。裸 SQL 寫入後舊快取
  不會失效。
- **`sql_mode` 是分層的**（2026-08-22 複驗，修正舊說法「不含 strict」）：伺服器 global
  **含** `STRICT_TRANS_TABLES`；是 WordPress 的 `wpdb::set_sql_mode()` 在 WP session 把
  strict 剝掉，FluentCommunity 才能把空字串塞進 `enum('active','blocked','pending')` 當
  「自行停用」狀態。⚠️ 走 `wp db query`／mysql CLI 的直寫跑在 **strict** 下，
  `SET status=''` 這類寫入**會直接失敗**——寫入務必走 plugin Model 層（`wp eval-file`）。
- 站上另有第三方 `fca-*` 系列 addon（`fca-content-manager` / `fca-events-pro` / `fca-hub` /
  `fca-multi-reactions` / `fca-pwa`），會讀 `fcom_*` 表，`fca-content-manager` 也會寫
  `fcom_space_user`。改動社群資料時記得它們的存在。
- **測試帳號：WP user 8 / 9 / 10**（`shosho.cs92g@g2.nctu.edu.tw`、`charlene.changtw@gmail.com`、
  `littlerainiebaby@gmail.com`）。批次操作要排除，它們的權限狀態刻意跟真實會員不一致。

## Domain knowledge

會員狀態三層模型、訂單憑證（`fct_ids`）授予/回收機制、快取地雷、操作紀律，全部寫在
**`agents/sanji/CONTEXT.md`**（Sanji 是 `CONTEXT-MAP.md` 指定的 Fluent Community bounded context，
agent 本身尚未落地）。動任何社群資料之前先讀那份。

相關決策見 [[fluentcart_single_grant_channel]]。
未來 agent 化的介面規劃見 `docs/capabilities/fluent-client.md`。
