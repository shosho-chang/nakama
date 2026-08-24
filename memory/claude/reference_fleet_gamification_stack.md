# Reference — 自由艦隊 gamification 系統（上線後的操作事實）

**Type:** reference
**Created:** 2026-08-24（分享期 T0 當日）
**Confidence:** high（全部為 production 實測值）

## 現況

**分享期 T0 ＝ 2026-08-24 09:43 CST**，系統在 `fleet.shosho.tw` 正式運轉中。
設計裁決見 `agents/sanji/CONTEXT.md`；營運方案見 `docs/plans/fleet-gamification-master-plan.md`。

| 項目 | 值 |
|---|---|
| Plugin | `wp/fleet-gamification/` → `/var/www/fleet.shosho.tw/wp-content/plugins/fleet-gamification` |
| Sanji 服務 | systemd `nakama-sanji`（`python3 -m agents.sanji loop`，90s 輪詢） |
| 每日對帳 | systemd timer `nakama-sanji-reconcile.timer`（05:00） |
| 總開關 | `wp option update nakama_gam_enabled 0|1` |
| 打卡 space 白名單 | option `nakama_gam_space_allowlist`（現＝`[22]` 測試船塢，secret） |
| Sanji 帳號 | WP user **126**（角色 `nakama_gam_service`，非 admin）；xprofile active+verified；頭像 attachment 9892 |
| Portal 基底 | `https://fleet.shosho.tw/deck/`（**不是站根**；`Helper::baseUrl()` 為準） |
| 航海日誌 URL | `/deck/u/{username}/voyage`（SPA 面板；`/?fleet_voyage={username}` 為獨立頁／`&embed=1` 為 fragment） |

## 目前計分範圍（分階段上線）

`SanjiConfig.scored_sources` 預設只含 **`like_received`（被讚 10 XP／1 貝里）**與
**`bookmark_received`（被收藏 100 XP／10 貝里）**——修修 2026-08-24 裁決「先鼓勵分享」。
登入分（`presence_day`）**照捕捉但不入帳**；日後開啟＝改 env `GAM_SCORED_SOURCES`，不回溯。

## 三個 vendor 縫隙（原始碼證實，勿「修掉」對策）

1. **收藏沒有 hook**——`react_added`/`react_removed` 被 `if ($type == 'like')` gate 住。
   對策＝每日 `GET /reactions` 增量掃描 `fcom_post_reactions`。
2. **`wp_login` 不能當每日登入訊號**（持久 session 不重登）。
   對策＝portal ticker 的 `fluent_community/track_activity`（45–75s/次）＋ dedupe key 一天一次。
3. **`react_removed` 只帶 `$feed`**（不知道誰移除）→ 記 marker，每日對帳 recount。

## 兩顆基礎設施地雷（已拆，會再遇到）

- **LiteSpeed 會快取 REST GET**：只送 `Cache-Control` header **不夠**（LSCache plugin 會覆寫），
  必須 `do_action('litespeed_control_set_nocache', ...)`。症狀是 Sanji 吃到殭屍空 events 靜默空轉。
  驗證法：間隔數秒連打兩次，看回應 `time` 有沒有動（單次 purge 後測會誤判成已修好）。
- **Cloudflare 擋 VPS 打自家域名**（403）：VPS 上已加 `/etc/hosts` loopback
  `127.0.0.1 fleet.shosho.tw`（origin 憑證為合法 Let's Encrypt，已驗）。

## 操作備忘

- 部署：`ssh nakama-vps 'cd /home/nakama && ./scripts/deploy_vps.sh'`
  （會 pull main、按路徑重啟服務、plugin lint→rsync→contract probe，probe 紅燈 exit 5）
- Contract probe（22 checks，一分鐘）：
  `wp eval-file wp-content/plugins/fleet-gamification/tools/contract-probe.php`
  **vendor 更新前後各跑一次**；FluentCommunity 已關自動更新。
- ⚠️ `wp eval-file` 的 eval 語境**不允許 `declare(strict_types=1)`**（tools/ 下的腳本都不能寫）。
- 快速看帳：`wp db query "SELECT * FROM zcjf_nakama_gam_grants ORDER BY id DESC LIMIT 10"`

Related: [[feedback_verify_ui_in_real_browser]]、[[fleet_community_stack]]
