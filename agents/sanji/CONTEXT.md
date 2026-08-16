# Sanji — Fluent Community 社群營運

Sanji 負責 `fleet.shosho.tw`（自由艦隊）的社群營運：會員狀態、空間/課程權限、與 FluentCart 的
金流連動。Agent 本身**尚未落地**（`agents/sanji/` 只有 stub），目前所有操作以 VPS 上的
wp-cli + plugin Model 層人工執行。本文件是這個 bounded context 的領域詞彙與運作模型。

> **版本基準**：FluentCommunity 2.7.5 / FluentCommunity Pro 2.7.6 / FluentCart（2026-08-16 驗證）。
> 下方所有行號都對應這組版本的 vendor 程式碼。**plugin 升級後行號會漂移，機制須重新驗證。**
> 存取方式見 [fleet_community_stack](../../memory/shared/reference/fleet_community_stack.md)。

---

## Canonical vocabulary

**Member Status（全站社群狀態）** = `fcom_xprofile.status`，決定這個人能不能進 portal。
schema 是 `enum('active','blocked','pending')`（`database/Migrations/XProfileMigrator.php:28`），
但程式實際使用**第四個值：空字串 `''` = 使用者自行停用帳號**（`app/Http/Controllers/ProfileController.php:172`）。
enum 沒有這個值，靠 DB 非 strict mode 才被容忍——**直接改 DB 時若 sql_mode 加上
`STRICT_TRANS_TABLES` 會炸**。
_Avoid_: 「會員狀態」單指這一層（它有三層，見下）

**Space Membership（單一空間會籍）** = `fcom_space_user` 的一列，帶 `status`（`active` / `pending`）
與 `role`（`admin` / `moderator` / `member` / `student`）。課程型 space 的 `member` 會自動轉成
`student`（`app/Services/Helper.php:1713`）。

**Community Role（社群角色）** = `fcom_meta` 的 `_user_community_roles`（serialize array）。
只有 `admin` / `moderator` 兩種值有意義；權限解析在 `app/Models/User.php:379-500`。
擁有此角色者可存取**所有**空間，不需要 space membership。

**Grant Channel（授予通道）** = 會替使用者建立 space membership 的機制。權限能否正確回收，
完全取決於「授予它的通道有沒有留下憑證」。

**Entitlement Receipt（訂單憑證）** = `fcom_space_user.meta` 裡的 `fct_ids` 陣列，例如
`{"fct_ids":[131]}`，記錄這筆會籍是被哪些 FluentCart 訂單養出來的。**這是整個回收機制的核心**。

**Revoke Hook（回收觸發點）** = FluentCart 認定為「權限應收回」的三個事件
（`fluent-cart/app/Listeners/IntegrationEventListener.php:406-410`）：
`order_fully_refunded`、`subscription_expired_validity`、`order_status_changed_to_canceled`。
**`order_partially_refunded` 不在其中** —— 部分退款永遠不會自動回收。

---

## 資料模型

| 表 | 用途 | 關鍵欄位 |
|---|---|---|
| `{prefix}_fcom_xprofile` | 社群 profile，一個 WP user 一列 | `user_id`、`status`、`username`、`is_verified` |
| `{prefix}_fcom_space_user` | 空間/課程會籍（pivot） | `space_id`、`user_id`、`status`、`role`、`meta`（含 `fct_ids`） |
| `{prefix}_fcom_spaces` | 空間、課程、space_group、sidebar_link 共用一張表 | `type`、`privacy`、`settings`（serialize） |
| `{prefix}_fcom_meta` | 泛用 meta，社群角色住這 | `object_type`、`object_id`、`meta_key`、`value` |

課程（`type = course`）與一般空間共用 `fcom_spaces` 與 `fcom_space_user`，**沒有獨立的課程表**。
`privacy` 合法值為 `public` / `private` / `secret`
（`Modules/Course/Http/Controllers/CourseAdminController.php:74`、`app/Http/Controllers/SpaceController.php:54`）。

---

## 授予 / 回收模型（order-scoped entitlement）

FluentCart 的設計是**「誰給的、誰收回」**，授予單位是 **product 的 integration feed**，不是使用者：

1. 每個產品的 feed 宣告 `space_ids` / `course_ids`，在 `event_trigger`（通常 `order_paid_done`）時授予
2. 授予時在 pivot 的 `meta.fct_ids` 蓋上 order id
3. 同一個空間被多張訂單授予時，`fct_ids` 會**累積**多個 order id
4. Revoke 時把該 order id 從 `fct_ids` 拔掉：還有其他 id → 保留會籍；空了 → 真正
   `Helper::removeFromSpace()`

實作在 `fluent-cart/app/Modules/Integrations/FluentPlugins/FluentCommunityConnect.php:177-232`。

**關鍵行為**：revoke 迴圈對「**沒有 `fct_ids` 的 pivot**」是**無條件移除**。所以只要空間有列在
feed 的授予清單裡，不管會籍當初是誰建的，退款時都會被清掉。

### ⚠️ 前提：所有授予都必須走 feed

這個模型只有在 **feed 是唯一授予通道**時才成立。任何繞過 feed 的授予（FluentCRM funnel 的
`add_to_fluent_community`、管理員手動加、public 空間自行加入）都**不會留下 `fct_ids`**
（`fluent-community-pro/app/Services/Integrations/FluentCRM/AddToSpaceAction.php:115` 直接呼叫
`Helper::addToSpace()`，不寫任何 meta），而且**如果該空間沒列在 feed 裡，退款時完全不會被碰到**。

2026-08-16 的退款漏洞就是這個成因，處置見
[fluentcart_single_grant_channel](../../memory/shared/decision/fluentcart_single_grant_channel.md)。

---

## 已知地雷

**`addToSpace()` 不刷存取快取，`removeFromSpace()` 會。**
`Helper::removeFromSpace()` 內部呼叫 `$user->cacheAccessSpaces()`（`app/Services/Helper.php:1794`），
但 `Helper::addToSpace()` **沒有**。批次加人後 `_fcom_space_ids` 會 stale，要自己補呼叫。
（使用者下次進 portal 時 `app/Hooks/Handlers/PortalHandler.php:872` 會自動修好，但不能依賴。）

**`_fcom_space_ids` 是物化快取。** 每個 user 的 usermeta 存了一份可存取空間 id 陣列，由
`User::cacheAccessSpaces()` 重算（`app/Models/User.php:462-482`）。**直接下 SQL 改
`fcom_space_user` 不會更新它。**

**public 空間會無條件進入可存取清單。** `cacheAccessSpaces()` 的查詢是
`whereHas(members...) orWhere('privacy', 'public')`（`app/Models/User.php:469-474`）。所以把人移出
public 課程後，他**仍然看得到、也仍然能自己加回去**——要真的擋住必須把空間改成 `private`。

**改空間 privacy 會讓全站快取失效。** 改完必須對所有 profile 重跑 `cacheAccessSpaces()`。
另外把課程從 `public` 改 `private` 時，UI 會一併清掉 `settings.public_lesson_view`
（`Modules/Course/Http/Controllers/CourseAdminController.php:310-315`），直接改 DB 要自己比照。

**FluentCRM 不看 funnel sequence 的 `status`。** `fc_funnel_sequences` 有 `status` 欄位，但
`FunnelHelper::getFunnelSequences()`（`fluent-crm/app/Services/Funnel/FunnelHelper.php:736-741`）
與 `FunnelProcessor`（`:448`、`:521`）抓 sequence 時**只用 `funnel_id` + `sequence` 排序，沒有任何
status 過濾**。把 sequence 設成 `draft` **不會停用它**——要停用只能刪除該列，或清空它的 settings
讓 action 自己走 early-return。

**`blocked` 不會因為重新購買而解除。** `User::syncXProfile()` 對既有 profile 不會重設 `status`
（`app/Models/User.php:754-765`，`status` 只在建立新 profile 時寫入）。封鎖過的人重新購買後仍是
`blocked`，要人工到 Members 頁改回 `active`。

**LiteSpeed object cache drop-in 啟用中。** 站上有 `wp-content/object-cache.php`。裸 SQL 寫入後
`wp_cache_*` 的舊值不會失效。plugin 自己的清法是
`wp_cache_flush_group('fluent_community')`（`app/Hooks/Handlers/DeactivationHandler.php:23-24`）。

---

## 操作紀律

**永遠走 Model 層，不要下裸 SQL 改會籍。** 用 `wp eval-file` 呼叫
`Helper::addToSpace()` / `Helper::removeFromSpace()`，理由是快取一致性 + 會觸發
`fluent_community/space/joined`、`space/user_left`、`course/enrolled`、`course/student_left` 等 hook。
這也是 FluentCart 官方 revoke 路徑使用的同一組函式。

**動 hook 之前先查監聽者。** `course/enrolled` 掛著
`CourseEmailNotificationHandler::initEnrolledNotification`（會排通知信）、
`AccessManagementCrmHandler::joinedInSpace`（CRM 打 tag）、以及 FluentCRM 的
`CourseEnrollmentTrigger`（automation trigger）。批次操作前務必確認這三者是否會對真實使用者發信。
（2026-08-16 當時三者皆為 no-op：課程是 `self_paced` 故 `CourseEmailNotificationHandler.php:129-132`
提早返回、無 `tagging_maps` 設定、無對應 funnel。**這些條件會變，每次要重驗。**）

**批次寫入前先 `wp db export` 相關表**，並在腳本裡做 user_id ↔ email 比對防呆。

**官方 API 表面**：`PATCH /members/{user_id}`（body `status`，只收 `active|pending|blocked`，需
`delete_any_feed` 權限，且不能改另一個 admin）——`app/Http/Controllers/MembersController.php:123-152`。
FluentCommunity 的 WP-CLI 指令只有 `migrate_from_bb` / `sync_x_profile` / `recalculate_user_points`，
**沒有**改狀態的指令。

---

## 相關文件

- [`docs/capabilities/fluent-client.md`](../../docs/capabilities/fluent-client.md) — Fluent 全家桶統一
  Python client 能力卡（Phase 2/3 planned，未開工）；未來 Sanji 落地時的介面規劃
- [`docs/case-studies/2026-04-22-Nakama-WP-+-Community-整合架構規劃.md`](../../docs/case-studies/2026-04-22-Nakama-WP-+-Community-整合架構規劃.md)
- [`CONTEXT-MAP.md`](../../CONTEXT-MAP.md) — Sanji 的 bounded context 定義
