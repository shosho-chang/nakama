---
type: reference
visibility: shared
agent: shared
confidence: high
created: 2026-08-30
expires: permanent
tags: [fleet, gamification, sanji, fluent-community, fluent-player, bunny, wordpress, vps]
name_zh: 自由艦隊遊戲化系統操作事實
name_en: Fleet gamification system operational facts
description_zh: 自由艦隊遊戲化（Sanji 記分系統）的線上操作事實：架構分層、部署指令、開關與白名單、vendor 縫隙與已踩過的地雷、影片觀看橋接、以及未驗證與待決策項目。
description_en: Operational facts for the Fleet gamification system (Sanji scoring): architecture layers, deploy commands, feature switches, vendor seams and landmines already hit, the video-watch bridge, plus what is unverified and undecided.
---

# 自由艦隊遊戲化系統 — 操作事實

**分享期 T0 ＝ 2026-08-24 09:43 CST**，系統在 `fleet.shosho.tw` 運轉中。
站台存取見 [[fleet_community_stack]]。營運方案見 `docs/plans/fleet-gamification-master-plan.md`；
逐條技術裁決見 `agents/sanji/CONTEXT.md`；交接總表見 `docs/plans/2026-08-30-gamification-handoff.md`。

## 架構的鐵則

**規則只存在 `agents/sanji/rules.py`。** plugin 是笨層——金額由 nakama 算好、經 REST 送過去。
任何分數表／等級曲線／位階／冪等鍵的變動只該碰 `rules.py`；plugin 不知道門檻，只拿等級帶四欄畫進度條。

| 層 | 位置 |
|---|---|
| 捕捉與呈現（笨層） | `wp/fleet-gamification/` |
| 規則與判定 | `agents/sanji/` |
| 帳本 | `zcjf_nakama_gam_events` / `_grants` / `_balances` |

## 部署與開關

| 項目 | 值 |
|---|---|
| Plugin 路徑 | `/var/www/fleet.shosho.tw/wp-content/plugins/fleet-gamification` |
| Sanji 服務 | systemd `nakama-sanji`（`python3 -m agents.sanji loop`，90s 輪詢） |
| 每日對帳 | systemd timer `nakama-sanji-reconcile.timer`（05:00） |
| 總開關 | `wp option update nakama_gam_enabled 0|1` |
| 打卡 space 白名單 | option `nakama_gam_space_allowlist` |
| 啟航宣言 space | option `nakama_gam_declaration_space` ＝ 2（slug `manifesto`） |
| Sanji 帳號 | WP user **126**（角色 `nakama_gam_service`，非 admin） |
| Portal 基底 | `https://fleet.shosho.tw/deck/`（**不是站根**） |

```bash
ssh nakama-vps 'cd /home/nakama && ./scripts/deploy_vps.sh'
```
deploy 會 pull main、按路徑重啟服務、plugin lint → rsync → contract probe（紅燈 exit 5）。

**Contract probe（33 checks，一分鐘）——vendor 更新前後各跑一次：**
```bash
cd /var/www/fleet.shosho.tw && sudo -u u2_fleet_shosho wp eval-file \
  wp-content/plugins/fleet-gamification/tools/contract-probe.php
```

⚠️ **`wp eval-file` 的 eval 語境不允許 `declare(strict_types=1)`**（`tools/` 下的腳本都不能寫）。
⚠️ **`fcom_posts.meta` 是 PHP serialize 不是 JSON**——用 `maybe_unserialize()` 或模型讀，
`json_decode` 會靜默回 null（2026-08-28 我因此誤報過站上資料）。

## ⚠️ merge ≠ deployed

`rules.py` 進 main 後 VPS 的 Sanji **不會自己換腦**，會持續用舊曲線蓋 level 欄。
金額不受影響、每日對帳會自癒，但 **`agents/sanji` 的 PR 合併後要儘快跑 deploy**。

## Vendor 縫隙（原始碼證實，勿當成 bug「修掉」）

1. **收藏沒有 hook**——`react_added`/`react_removed` 被 `if ($type == 'like')` gate 住。
   對策＝每日經 REST 增量掃描 `fcom_post_reactions`。
2. **`wp_login` 不能當每日登入訊號**（持久 session 不重登）。
   對策＝portal ticker 的 `fluent_community/track_activity` ＋ 一天一次 dedupe。
3. **`react_removed` 只帶 `$feed`**（不知道誰移除）→ 記 marker，每日對帳 recount。

## 兩顆基礎設施地雷（已拆，會再遇到）

- **LiteSpeed 會快取 REST GET**：只送 `Cache-Control` header **不夠**，必須
  `do_action('litespeed_control_set_nocache', ...)`。症狀是 Sanji 吃到殭屍空 events 靜默空轉。
  驗證法：間隔數秒連打兩次，看回應 `time` 有沒有動。
- **Cloudflare 擋 VPS 打自家域名**（403）：VPS 已加 `/etc/hosts` loopback `127.0.0.1 fleet.shosho.tw`。

## 影片觀看橋接（2026-08-28 上線，尚未端到端驗證）

FluentPlayer 內建**伺服器權威**的 progression 引擎，客戶端只送原始觀看區段、
伺服器自有片長並重算覆蓋率。該引擎**已無條件註冊**（`fluent-player/app/Hooks/actions.php:18`），
但其瀏覽器端 tracker 只在 LearnDash 整合裡 enqueue，本站沒裝 LearnDash → 從來沒有資料進去。

我們補的是兩頭，**不改任何 vendor 檔案**：
`wp/fleet-gamification/includes/class-video-progress.php` ＋ `assets/video-progress.js`。

- **發分判準：只認 `durationSource === 'server'`**。tracker 永遠送 `duration=0`，
  讓判定只可能是 `server` 或 `none`——客戶端在結構上無法把信任層級降級。
  Bunny media 有 provider 片長 → `server`；YouTube 沒有 → `none` → 永不達標。
  **YouTube 課自動落在計分範圍外，零特例程式碼。**
- `verdict` 形狀＝`['complete' => bool, 'reason' => 'ended'|'threshold'|null]`。
- 課程歸屬**伺服器端反查** lesson.message 的 block（REGEXP，避免 `"mediaId":989` 誤中 9896），
  不採信 tracker 送的 context。
- `video_watched` **刻意不在** `SanjiConfig.scored_sources`——先觀測數月再決定分數。

## Bunny → FluentPlayer 遷移（2026-08-28 完成）

課程「直播錄影」（space 21）24 課：**11 課 Bunny 影片已搬**成 FluentPlayer media（9896–9906）、
6 課 YouTube、7 課無影片來源。工具＝`wp/fleet-gamification/tools/bunny-media-migrate.php`
（dry-run／apply／rollback，改前備份到 `uploads/nakama-gam/`）。

**三層寫入缺一不可**：
1. `fluent_player_media` post — `settings.provider='bunny'`、**`settings.duration`＝片長秒數**
   （ProgressionHandler 優先讀它，這是伺服器權威的地基）、`preset_slug='course'`、`post_status='private'`
2. `lesson.message` 的 `<!-- wp:fluent-player/media {"mediaId":N,"isFcomFeatureMedia":true} /-->`
   ← **連結在這裡，不在 meta**
3. `lesson.meta.media` ＝ `{type:"fluent_player", content_type:"video", image:縮圖}` ＋ `video_length`

⚠️ **不要在 Bunny library 579407 開 Token Authentication。** 簽章程式碼是存在的
（`BunnyCDNService::generateSignedUrl()`），播放器 src 也確實帶簽章；但 lesson meta 裡的縮圖是
**靜態存下的未簽章網址**，開了 token auth 會 403。實際在擋盜連的是 **referer 白名單**
（實測：無 referer → 403、帶 `Referer: https://fleet.shosho.tw/` → 200、亂填過期 token 也 200）。

⚠️ `media.type` 確實會是 `'fluent_player'`——那是從 FluentPlayer 媒體庫挑既有 media 的路徑；
上傳／貼 URL 兩條路徑寫的是 `type:'oembed'` ＋ `player:'fluent_player'`。原生 80% 影片閘門
（`require_video_completion`）因此**是可以開的**，目前維持 `no`。

## 品牌色

**canonical 橘＝`#e98965`**（PANTONE 165 PC），定義在 `thousand_sunny/static/shosho/tokens.css`，
另有 9 處在用（`docs/design-system.md`、`docs/thumbnail-design-system.md`、4 個影片合成模板、2 個 skill）。

`wp/fleet-gamification/includes/class-voyage-page.php` 目前是 **`#e8913f`（drift，待收斂）**。

**用色紀律**：「橘只當線不當塊，不做大面積填色」——原本只寫在 `class-voyage-page.php` 的註解裡。
遊戲化 surface **不自己定底色與文字色**，繼承站台主題（`--fcom-primary-bg` / `--fcom-menu-text`）；
中性色一律 `rgba(125,125,125,.x)` 讓兩種主題共用一組值。

## GitHub 認證（2026-08-28 輪替）

VPS 的 `/home/nakama` **已改走唯讀 deploy key**（`/root/.ssh/nakama_deploy_ed25519`，
repo-local `core.sshCommand`），remote 為 `git@github.com:shosho-chang/nakama.git`。
原本內嵌在 remote URL 的兩顆 classic PAT 已撤銷。

⚠️ **GitHub 的「Last used」不追蹤 git-over-HTTPS**，只追蹤 API 呼叫。
一顆昨天還在 `git pull` 的 token 可能顯示「5 個月未使用」——**不可用它判斷 token 是否已死**。

## 快速查帳

```bash
wp db query "SELECT * FROM zcjf_nakama_gam_grants ORDER BY id DESC LIMIT 10"
wp db query "SELECT level, COUNT(*) FROM zcjf_nakama_gam_balances GROUP BY level ORDER BY level"
```

Related: [[fleet_community_stack]]
