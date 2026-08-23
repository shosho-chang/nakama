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
enum 沒有這個值。**容忍它的不是伺服器設定寬鬆，是 WordPress 主動剝除**——
`wpdb::set_sql_mode()` 把 strict 拿掉，WP session sql_mode 為
`NO_ZERO_IN_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION`；
而**伺服器 global 本來就含 `STRICT_TRANS_TABLES`**（2026-08-22 複驗）。
⚠️ 因此走 `wp db query` / mysql CLI 的直寫跑在 strict 下，`UPDATE ... SET status=''`
**今天就會失敗**——這是現在進行式，不是假設。
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
（`fluent-cart/app/Listeners/IntegrationEventListener.php:404-408`）：
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

實作在 `fluent-cart/app/Modules/Integrations/FluentPlugins/FluentCommunityConnect.php:174`（revoke 分支 191-241、核心迴圈 206-228）。

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
`Helper::removeFromSpace()` 內部呼叫 `$user->cacheAccessSpaces()`（`app/Services/Helper.php:1795`），
但 `Helper::addToSpace()` **沒有**。批次加人後 `_fcom_space_ids` 會 stale，要自己補呼叫。
（使用者下次進 portal 時 `app/Hooks/Handlers/PortalHandler.php:869` 會自動修好，但不能依賴。）

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

**LiteSpeed object cache drop-in 啟用中，且 `flush_group` 是死路徑。** 站上有
`wp-content/object-cache.php`，裸 SQL 寫入後 `wp_cache_*` 的舊值不會失效。
⚠️ plugin 的清法 `wp_cache_flush_group('fluent_community')`
（`app/Hooks/Handlers/DeactivationHandler.php:23-24`）**在本站永遠不會執行**——該行條件是
`wp_cache_supports('flush_group')`，而 LiteSpeed drop-in **沒有實作 flush_group**
（2026-08-22 複驗：`ext object cache: yes` / `supports(flush_group): NO`）。
**本站沒有「精準清 FluentCommunity 快取」這條路**，只能全站 `wp cache flush` 或等 TTL。
連 `Utility::getOption()` 讀 `fcom_meta` 都包在 object cache 裡——直接改 DB 的 option 也不會生效。
這強化了「production 讀寫一律走 REST」的裁決。

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
`delete_any_feed` 權限，且不能改另一個 admin）——`app/Http/Controllers/MembersController.php:124-153`。
FluentCommunity 的 WP-CLI 指令只有 `migrate_from_bb` / `sync_x_profile` / `recalculate_user_points`，
**沒有**改狀態的指令。

---

## Gamification Add-on（自研，grill 進行中）

**Gamification Add-on** = 自由艦隊的自研遊戲化系統，由「WP plugin（笨層）＋ nakama/Sanji（聰明層）」
組成。2026-08-22 grill 裁決：

- **智慧放 nakama**：plugin 只做 hook 捕捉、持有 ledger、暴露窄 API；所有規則（點數判定、
  streak、防刷、賽季結算）與打卡視覺判定都在 nakama 端，Sanji 是大腦。
- **自用工具，不商品化**——若未來要把 plugin 當產品賣給其他 FluentCommunity 站長，
  此架構須重新評估（規則引擎得搬回 PHP）。
_Avoid_: 在 PHP 內實作任何規則邏輯（那是 nakama 的職責）

**Ledger（事件帳本）** = gamification 的唯一真相源，**住在 WP MySQL 的 plugin 自有資料表**
（與 vendor 表零耦合）。裁決理由：wpvivid 全站備份讓社群資料與經濟資料原子一致（災難還原
不會 split-brain）；PHP hook 捕捉與觸發動作同庫、零丟失；portal 顯示讀本地不依賴 nakama 存活。
記人用 `user_id ＋ email snapshot` 雙鍵——email 是跨平台耐久身分，日後離開 WordPress 一句
dump 就能搬家。nakama 端（state.db）只放**可重建的衍生投影**（儀表板/分析用），不是第二真相源。
_Avoid_: 把帳本放 state.db（agent 營運庫炸掉不該傷會員資產）；雙寫鏡像（同步 bug 溫床）

**Ledger 存取路徑** = production 讀寫一律走 plugin 的窄 REST API（`/wp-json/nakama-gam/v1/`，
app password auth，沿用既有 WP 整合模式）。裁決理由：加點的副作用是 WP-native 的
（xprofile.meta badge 序列化、object cache flush、`do_action`），集中在 PHP 一個口做；
invariant 單點執行；契約可版本化。nakama 用 cursor 增量拉事件回 state.db 投影，
新分析查詢在 Python 端做，不加 PHP 端點。**直連 SQL 只保留給唯讀 ad-hoc 營運查詢**
（ssh + `wp db query`），production 迴圈禁用。
_Avoid_: Python 直寫 MySQL 改帳（會繞過 WP 序列化/快取/hook 副作用，上週已驗證的雷區）

**事件流向** = 單向 pull：PHP hook 捕捉寫本地 events 表（原生訊號＋`checkin_submitted`），
Sanji 每 1–2 分鐘 cursor 輪詢處理。WP 不知道 nakama 存在；nakama 重啟從 cursor 續跑，天然補課。
_Avoid_: webhook push（反向依賴＋retry/dead-letter 維護負擔；沒有秒級需求就不引入）

**帳本會計紀律** = append-only 事件流，三鐵則：①永不 UPDATE/DELETE 歷史——改錯帳開
**沖正事件**（負值、reason 指向原事件 id），補償走「船長特別獎」正向事件，光明正大；
②每筆事件帶 `idempotency_key`（如 `checkin:{user_id}:{date}`）＋ DB unique constraint，
重跑/重放/補課不重複入帳，防線在 DB 層；③每筆授予帶 `rule_version`——規則改版只影響未來，
永不回溯重算（Starbucks 教訓的技術配套）。餘額（貝里）與里程（XP）是衍生投影：cache 表
加速顯示、每日對帳重算校驗、壞了砍掉重建，帳本永遠是真相。
_Avoid_: 直接改 DB 數字「修帳」（透明度是社群信任的一部分，帳目要可稽核可申訴）

**回饋節奏（對外承諾）** = 兩層：①個人加點回饋即時（Sanji 分鐘級回覆，這是習慣迴圈核心，
不等批次）；②聚合統計（榜單/週狀態）每日 05:00 批次更新，對外公開此節奏。
**每日對帳排程（daily reconciliation）架構上必要**——streak 斷檔是「事件的缺席」，
只有排程掃描能偵測；它同時是 Sanji 停機的補課 safety net。對外承諾寫寬（「最晚隔日
05:00 入帳」），實際體驗分鐘級——承諾寬鬆給營運留 slack。

**Sanji runtime** = Agent SDK ＋ subscription quota，照抄 Nami 模式
（`gateway/handlers/nami.py` 的 `_sdk_auth_env()`：注入 `CLAUDE_CODE_OAUTH_TOKEN`
並清空 `ANTHROPIC_API_KEY` 強制走訂閱 OAuth；2026-08-18 已實測背書）。輪詢 loop 本身
零 LLM；判定批次才呼叫 `query()`。判定走兩層模型（便宜模型為主、曖昧升級，省 quota）。
quota 耗盡的故障模式被 cursor＋每日對帳自然吸收（延遲、不丟失，承諾不破）。
API 計費是明確 opt-in 的 fallback（規模化到非個人自動化時再翻）。

**Sanji 社群身分** = 正式成員帳號（專屬 WP user＋xprofile、`is_verified=1`、專屬徽章），
**排除在點數經濟與榜單之外**（與測試帳號 user 8/9/10 同一份排除名單）。發文/回覆走
plugin 端點內呼 FluentCommunity PHP Model 層，原生通知（鈴鐺/email）正常觸發。
名字、頭像、口吻＝品牌決策，歸修修。

**回覆通道** = 成功判定→**打卡貼文留言區公開回覆**（「+10 貝里｜連續 N 天」；會員收原生
鈴鐺通知，社會證明公開可見，帳目透明）。**DM 只用於例外**：退件（公開退件丟臉，紅線）、
異常提醒、選配週摘要。fluent-messaging 程式化發送介面**未驗證**，驗證前不承諾 DM 功能。
_Avoid_: 把成功回饋塞進 DM（打卡空間會死寂，對沉默多數零拉力）

**程式碼佈局與部署**（2026-08-22 裁決，細節授權 Claude 判斷）= PHP plugin 住 repo 根目錄
`wp/fleet-gamification/`（依 `video/` 先例：不同 runtime 的子專案住根目錄，ownership 歸
Sanji context、CONTEXT-MAP 註記）；Sanji 服務住 `agents/sanji/`、跑獨立 systemd
`nakama-sanji`（不進 gateway——那是 Slack 專用容器）；部署一律走 `scripts/deploy_vps.sh`
擴充（diff-driven rsync plugin ＋ restart 服務＋部署後煙霧測試），不開第二條 deploy 路。

**規模與測試約束** = 系統目標壽命 10 年、未來同時在線**千人級**；持續修改與擴增是常態。
**目前不設 staging**——創始船長世代兼任測試員角色，plugin 屬加法式改動（自有表/端點，
不碰 vendor 行為），爆炸半徑=「gamification 失效」而非「社群壞掉」。規模化警戒線：
千人級活躍時打卡判定量（~30K 次/月）會超出 subscription quota 合理範圍，屆時觸發
API 計費切換（環境變數即可翻）。

**Genesis（創世點）** = ledger 從上線日 T0 起算，**不回填任何歷史資料**。裁決理由：那些行為
發生時遊戲還不存在——按讚者不知道在給分、發文者不知道在賺點，回溯計分獎勵的是未被誘因
驅動的行為，數字是假的；且新系統上線時每個人都必須經歷 Onboarding 的「第一次完成迴圈」
（Octalysis p464），帶著歷史積分登入的人會跳過那個「我做到了」的錨點。
副作用（皆為正向）：退款離開者不會因歷史讚數上榜；原生的計分實作與我們無關（註：兩份 `recalculateUserPoints()` 演算法**完全相同**，真正的差異在寫入守門——CLI 是只增不減的棘輪、Pro 是雙向精確同步；而排行榜 SQL 又是第三種語意。早期描述為「兩套互相打架的演算法」有誤）；
`xprofile.total_points` 維持 0、原生 leaderboard 模組維持關閉。
**資歷用徽章表彰，不用點數**——「創始船長」徽章（117 人持有）是正確載體：榮譽是身分，不是餘額。
_Avoid_: backfill 歷史 reactions/貼文/課程完成紀錄

**經濟參與 vs 榜單露出**（2026-08-22 修正）= 兩件事分開，不是同一份名單：
- **修修（user 2）完整參與**里程/等級/streak 並公開上榜——實踐制下他沒有結構性優勢
  （冥想 30 天對誰都是 30 天），且「船長也在打卡」是 CD1 可信度的具體實踐（Octalysis p113
  「主張者是否根據它做出帶犧牲的決策」）。**唯一排除：不兌換實體商品**（自己出的錢）。
  ⚠️ 早期「排除修修」的說法是從**原生讚數制**排行榜帶過來的（那套他有結構性優勢，
  原生 `fcom_leaderboard_excluded_user_ids` 即為此而生），該理由不適用於實踐制，已作廢。
- **Sanji／測試帳號不露出榜單**，理由不同：Sanji 是機器人、沒在實踐，上榜是純雜訊。
- **排除 = 呈現層過濾，不是捕捉層過濾**：測試帳號照常寫 ledger、照常判定回覆
  （測試路徑 = 生產路徑，零分歧），只在榜單與公開統計濾掉。

**測試策略（無 staging）** = 不建 PHPUnit 基建。部署後確認站台正常 → 開**隱藏測試 Space**
→ 修修用自己帳號跑完整行為 → 通過才對全社群開。保留 `gam_enabled` feature flag 一鍵止血
（捕捉層停寫、REST 回 503，社群不受影響）。
推導出的架構需求：**捕捉層必須有 space allowlist**——只有名單內 space 產生 gamification
事件（生產上本來就需要：只有打卡 space 該給分，測試 space 只是第一個使用者）。

**Schema migration 政策** = 版本號驅動，仿 repo 既有 `migrations/` 慣例：plugin 存
`gam_db_version` option ＋ 一支一號的 migration 檔，啟動時比對版本、缺號依序補跑。
每支必須 **idempotent**（部署可能重入）。**只加不改優先**——加欄位(nullable)/加表/加索引
安全；改型別或刪欄位走「新欄位→雙寫→回填→切讀→廢棄」多步驟，不在單一 migration 完成。
兩條配套：①**事件表不改語意只加欄位**（append-only 的延伸；歷史事件意義被凍結，要改語意
就發新 `event_type`、舊的留著）；②**投影表可隨時砍掉重建**（衍生物）——schema 演進的
風險因此幾乎全被關在投影層，事件表保持穩定。
_Avoid_: 抄 vendor 的堆積式 migration（`FeedSpaceUserMigrator.php` 那種
`if (!in_array('col', $columns)) ALTER` 疊加寫法——無版本號、無順序保證、不知跑到哪、無法回頭）

**Observability** = 十年裡最危險的不是炸掉（很明顯），是**靜默失效**（hook 改名後捕捉層
不再收事件／Sanji 掛掉沒人判定／quota 用完判定全失敗——而社群表面正常，可能兩週後才從
會員抱怨發現）。三層：
①**斷流偵測**（最重要）：每日對帳順便檢查——過去 24h「事件寫入數＝0」或「判定數＝0」
而該時段有社群活動 → 告警。這條能抓到上述三種靜默失效的絕大多數。
②**對帳落差告警**：每日重算投影 vs 現存投影，落差應為 0，有落差就報（兼抓 idempotency 破洞）。
③**告警走 Slack、歸 Franky**：掛進 Franky 既有 health check 巡檢，用既有 gateway Slack 基建。
_Avoid_: 另建告警系統；把 Bridge 儀表板卡片當成監控（那是「想看數據」不是「知道壞了」，
排 Phase 3）

**判定漏斗（HITL）** = 七層由便宜到昂貴，目標是修修每週只看到個位數：
①**機械去重**（perceptual hash 比對歷史投稿，零 LLM）→ ②**Haiku 初判**（解決九成）
→ ③**信任分**（ledger 查歷史：長期乾淨紀錄的老會員曖昧案直接放行、新人從嚴；零 LLM
成本且天然對準風險——會為 10 點造假的不會是已真實打卡兩個月的人；附帶效果＝老會員享有
更順暢體驗，即 SAPS 的 Power 級特權 p483）→ ④**強模型複判** → ⑤**問本人**（Sanji 公開
留言請補件——多數「不確定」是資訊不足而非判斷困難，把問題還給唯一知道答案的人）
→ ⑥**暫記 provisional**（先給分、標記，會員體驗不卡住、streak 不斷，事後不通過走沖正）
→ ⑦**人工佇列 → 48h fail-open**。
平行機制：**隨機抽查**（每週抽 X% 深度複核）——逐案判定看不出系統性作弊，抽查是統計層
防線，存在本身即嚇阻（Octalysis p295 點名 Samsung Nation 敗因＝缺 exploit flow control）。
實作順序：①②⑥⑦ Phase 1 必要；③⑤ Phase 2（需累積歷史才有信任分）；④與抽查看量再說。

**fail-open 原則** = 待判定逾 48h 自動放行並留痕（`auto_approved_by_timeout`），佇列超過
上限則 Sanji 降級為寬鬆模式＋Slack 告警。裁決理由：這不是金融系統，**錯放的代價遠低於
錯擋**——誤放一筆 10 點 vs 讓真心打卡的人被晾三天的信任損失。裁決介面就是 Slack
（Franky 既有管道），不為此蓋 Bridge UI（會讓裁決變成要開電腦的事，然後就不會做了）。
**核心原則：任何需要修修每日介入才能運作的機制，都是設計失敗。** Sanji 可以請求裁決，
但系統不能因為修修沒回應而停擺。

## Gamification 行為設計（2026-08-22 grill，進行中）

> 這一節是**行為/產品設計**，與上方的架構裁決分開。設計框架＝周郁凱 Octalysis
> （教材：`G:\OneDrive\Documents\PDFgear\PressPlay生鮮 Sales and Marketing Course 2025.pdf`，
> 1244 頁，頁碼引用皆指該檔）。

> ### ⛔ 設計紀律：Greenfield，不得以現況錨定
>
> 修修 2026-08-22 兩度明令：**把這個社群當作從無到有設計**。這不是語氣，是硬約束。
>
> **具體禁止**：拿現有社群的參與率、沉默人數、活躍衰退曲線、既有會員行為當設計輸入或
> 論證依據。理由：那些數字是**沒有這套系統時**的產物，用它們推導只會設計出一套補救裝置，
> 而不是一套本來就該長這樣的系統。
>
> **允許**：把現況當「上線後要驗證的假設」與「遷移計畫的輸入」——但那是設計定案**之後**
> 的事，不是設計過程的材料。
> _Avoid_: 「因為目前有 N 人沉默，所以機制要……」這種句型

**北極星（Strategy Dashboard 第 1 步，2026-08-22 Q13 定案）＝雙層**：
- **使命指標（最高）**：會員是否真的養成好習慣、身心健康變好。
- **商業指標（使命的可量測投影）**：續約率。修修原話：「如果真的達到這個北極星指標，
  那用戶自然而然就會續約。所以，或許續約率就是體現在這一點的呈現。」
- 位階是單向的：續約率是使命的**影子**，不是目標本身。兩者解耦的瞬間（會員因沉沒成本
  或社交綁定續約、但已停止練習）＝ Black Hat 滲入的警報。

| 層 | 指標 | 量測時點 |
|---|---|---|
| 使命 | **無獎勵情境下的自發練習訊號**：賽季外的自發打卡／練習分享（不在計分範圍——正因無分，才是習慣內化的證據） | 季後 |
| 商業（北極星投影） | 季度 cohort 續約率／年度續約率 | 每季開船日／每年 1/1 |
| 槓桿 | 季轉年升級率 | 每年 1/1 |
| 先行（Sanji 週報） | 挑戰報名率、打卡存活曲線、結算週直播出席、發文互動率 | 每週 |

- 指標的自我保護作用：Black Hat 機制能衝高活動量但毒化續約意願（Samsung Nation
  p293-296——一堆 activity、零商業影響）。北極星選續約率＝讓指標本身替 White Hat 紀律站崗。
- 使命層量測是 overjustification（p72）的反向測試：**點數停了練習還在 → 習慣養成；
  點數停了練習就停 → 只養出了刷分場**。（此量測方式為 Claude 補充提案。）
- 先行指標中的直播出席／發文互動屬 🔴 不計分項——**不計分 ≠ 不量測**，
  進儀表板不進計分公式（與計分憲法一致）。

**玩家類型（Strategy Dashboard 第 2 步，2026-08-23 定案）**。五原型 × 主導動力 × 覆蓋檢查：

| 原型 | 規劃占比* | 主導動力 | 已覆蓋機制 | 缺口處置 |
|---|---|---|---|---|
| 潛水型 | 40–50% | CD7／CD1 | 登入分、研究文、mini course | 開船週召集＋驚喜引擎 |
| 實踐型 | 30–40% | CD2／CD8 | 挑戰迴圈全套 | 已檢查，無 |
| 貢獻型 | 10–15% | CD5／CD3 | 被讚/被收藏計分、（未來）主辦資格 | 已檢查，無 |
| 全能型 | 3–5% | 全譜 | 挑戰＋貢獻＋等級＋privilege | Endgame 兩題 PARKED |
| 間歇型 | 疊加維度，任一年 10–20% | CD8／CD1 | 等級永存（年輪）、無落隊狀態 | 開船週召集 |
| 刷分者 | <1% | — | HITL 七層漏斗＋隨機抽查 | 已檢查，無 |

- *占比是**規劃假設**（付費自選＋報名承諾儀式會把免費社群 90-9-1 的潛水占比大幅壓低），
  **不是**由現況推得（greenfield 紀律）；上線後由實測取代。
- **占比是活指標，不是一次性估算**：Sanji 每季結算依 ledger 自動分型
  （例：實踐型＝有報名且打卡 ≥40% 天數；潛水型＝僅登入；間歇型＝上年活躍今年沉寂），
  季報呈現**分布＋遷移流向**。系統成功定義之一＝遷移：潛水→實踐（活化橋）→貢獻→全能。
- 動力譜掃描：CD1/2/4/5/6/8 皆厚，**CD3（創造）最薄**——Octalysis 的 Endgame 黃金角落。
  記為未來賽季補強方向（如會員自創挑戰變體）；現有沾邊：成就稱號自選展示、下季主題投票。

**開船週召集（Sanji 季度儀式，2026-08-23 定案）**。每季 W1，季度公告對兩種人
**個人化成邀請函**：①從未參加過挑戰者（潛水型活化橋）——本季主題＋導論課連結，邀請不催促；
②缺席 2+ 季者（間歇型回歸路徑）——核心訊息「你的紀錄都還在」＋提及其歷史，零罪惡感語言。
White Hat 硬護欄（規則層）：
1. 每人每季**最多一則**個人化召集；退訂永久有效
2. 禁用句型：罪惡感（「大家都在等你」）、損失威脅、比較（「別人都 Lv.X 了」）
3. **只在開船週發**——季中對沒報名者絕對靜默（不動既有裁決：沒報名＝這季選擇不玩）
4. 截止前最多一則**全站**提醒，無個人倒數轟炸
與既有裁決相容：「不對沒報名的人做任何事」管的是**季中**（門關著）；開船週門開著，
邀請正當——且這是潛水型與間歇型在全設計中唯一的主動觸點，一年四次。
發送通道（DM vs FluentCRM email）為實作題，fluent-messaging 程式化發送驗證後再選。

**核心重複行為＝全社群同步的限時挑戰**（Octalysis Scaffolding 階段，p467）。
裁決理由：社群缺的不是「養成習慣的工具」（那個人習慣 app 就能做，不構成付費理由），
是**讓人這週回來的共同理由**。實證背書：既有五個主題 space「永遠開著但無共同節奏」，
八個月共 21 篇貼文＝已驗證的死法。
_Avoid_: 第一年做「個人自選習慣＋同步挑戰」混合制——兩套規則、兩套點數平衡、兩倍判定
複雜度，且會讓 Sanji 要判無數種證據格式。純同步挑戰讓每月只有一種格式要判。
個人自選習慣隨時可後加，一開始做兩套會兩套都做不好。

**賽季（Season）** = 一季（3 個月），內含 2 個月挑戰 ＋ 1 個月間隔（間隔期設計待決）。

**挑戰（Challenge）** = 賽季內為期 **2 個月**的全社群同步習慣養成，同時開始、同時結束。
2 個月的選擇有實證基礎：習慣自動化中位數約 66 天（Lally 2010）——一個月跨不過那個門檻，
完賽徽章的意義會從「我真的變了」降級成「我參加了」。

**挑戰主題輪替** = 睡眠、運動（如日行 7000 步）、冥想練習、呼吸……由基礎好習慣起手。

**Mini Course** = 每個挑戰配一支約 1 小時、分小節的課程，講該練習的**目的與背後科學原理**。
這是 CD1（Epic Meaning）在健康領域最有效的形式——理解機制的人撐得比被指派的人久。

**完賽計分模型** = 每個人帶走一張個人成績單，不是只有過/沒過：
```
基礎分：每完成一天 = 1 分（永不歸零）
連續獎：每連續 N 天 = +X 分（**當場入袋**，斷了只是重新數，已得的拿不走）
全勤獎：全期滿勤 = +Y 分（唯一的全有全無，屬錦上添花的頂冠）
```
關鍵紀律：**bonus 必須當場入袋，不可終局結算**。終局結算＝第 23 天斷掉時，已投入 22 天卻
發現最大獎永遠拿不到 → 退出。當場入袋把心理懸崖變成台階，保住 streak 的動力、拿掉退出觸發器。
_Avoid_: 「全勤才算完成」式的成敗定義（漏一天就自認失敗、第二週挑戰就死了；且習慣科學上
漏一天不影響自動化養成）

**系統裡不存在「落隊」狀態**（2026-08-22 修修指正，Claude 原提案作廢）。計分模型本身已讓
「重新加入」成為多餘概念：停了十天只是那十天沒得分，第 11 天做了就照常給分，沒有資格被
關閉、沒有分數被收回。「重新開始」若有價值也只是**心理層面**，應住在回饋層
（Sanji 回「歡迎回來 🌊 累積 N 天」），**不得寫進規則層**。
_Avoid_: 定義「落隊」門檻、設計「重新上船」機制——那是在解一個計分模型已經解掉的問題

**報名（Sign-up）＝參加挑戰的前提**。作用是**承諾儀式**而非排除機制：公開主動報名顯著提高
後續執行率，自動加入的人沒有做出任何承諾。對映 Octalysis 四階段（p462-469）的
Discovery → Onboarding 那道門。
衍生效果：**沒報名的人不是「落隊」，是這一季選擇不玩**——完全正當有尊嚴的狀態，系統不該
對他們做任何事。而「報名了卻停下來」的人是自己舉過手的，對這群人主動關懷是回應其宣告，
不是監控。這重新切開了「要不要主動聯繫會員」的分歧。

**報名前置：Introduction Course（知識閘門）** = 修修裁決：參加挑戰前必須先具備知識、知道
自己為什麼要做。實作用原生課程模組即可，**不需自建**：
- 閘門條件 = `CourseHelper::getCourseProgress($courseId, $userId) == 100`（公開靜態方法）
- 批次查用 `getBulkCourseProgress()`；完課事件走 `fluent_community/course/completed`
- `sequential_lesson_order` ＋ Pro `SequentialLessonLockHandler` 可強制順序解鎖，防跳看
- Pro 有現成 **Quiz 模組**，可承接「回答幾個簡單問題」
**報名宣言**：答幾題簡單問題＋自己寫一段簡短宣言。具體設計修修指示延後再議。

**閘門的演進路徑**（2026-08-22 修修裁決：一開始不要弄複雜）：
- **Phase 1**：每個挑戰配一支 **5–10 分鐘 introduction** ＋幾題問題，看完即可報名。
  用原生 course module ＋ Pro Quiz 模組實作，零自建。
- **Phase 2**：長成約一小時、分小節的完整 mini course（講科學原理）。
- 採**每挑戰各有自己的導論**（B-lite）而非一次性總論——知識是主題專屬的，睡眠的導論
  無法讓人知道為什麼要日行 7000 步。副作用（正向）：導論是必修不是選配，
  完成率是數量級的差別，這正是付費社群 vs 免費內容的分野。
- 若未來要做「艦隊總論」給新會員，那屬**入社群 onboarding**，與**挑戰報名**是兩條線，
  不可混進同一道閘門。

**計分憲法：三層可刷性分類**（2026-08-22 裁決）。判準是**能不能單方面製造**，不是「是不是社群行為」：
- 🟢 **他人／系統驗證 → 放心給分**：被按讚、**被收藏**、完成單課、完成整門課、看完影片、
  通過測驗、挑戰打卡（Sanji 判定）
- 🟡 **自己生成但有天然節流 → 給分但設硬上限**：邀請新人（需對方真的註冊）、投票（每題一次）、
  **每日登入（PTT 式，一天只記一次）**
- 🔴 **自己生成且無節流 → 不給分**：發文數、留言數、追蹤數
  → 但**仍是營運觀測指標**，該進儀表板，不該進計分公式
_Avoid_: 獎勵發文/留言的**數量**（Samsung Nation 死因 p293：low-value repetitive actions →
pointsification）。修修的修正很關鍵：**FluentCommunity 原生設計是「你發文、別人給讚才算你的分」
——他人驗證，無法單方面刷**，這跟獎勵發文數是兩件事，早期 Claude 把兩者混為一談。

**被收藏（bookmark）是被埋沒的品質訊號**：八個月只有 29 次，對比 1,628 次按讚（稀有 56 倍）。
讚是「路過點頭」、受人緣與發文時間影響；**收藏是「我要回來再看」，幾乎只受一件事影響：真的有用**。
要獎勵「有益的文章」，收藏是比讚強得多的訊號。

**兩條計分軌道**（互不干擾，解決主理人優勢與新人絕望兩個問題）：
| 軌道 | 來源 | 週期 | 呈現 |
|---|---|---|---|
| **挑戰積分** | 打卡＋連續獎＋全勤獎 | **每季歸零** | 挑戰排行榜（人人從 0 起跑，新人第一季即可奪冠） |
| **生涯里程** | 被讚／被收藏／完課／測驗／邀請／每日登入 | **永不歸零** | **等級與稱號，不做排名** |
關鍵：**排名（ranking）與等級（leveling）是兩回事**（修修指正）。巴哈姆特式的等級是年輪，
沒人期待超越十年老手也不會因此不玩；競爭發生在每季歸零的挑戰榜。主理人被讚多只影響
等級（應該的），不影響競爭場。
_Avoid_: 把生涯累積做成排行榜（新人數學上永遠追不上＝原生 all-time 榜現況：457 vs 第十名 41）

**權重裁決：實踐與貢獻等值**（2026-08-22）。「自己做到」與「幫助別人」是兩條平行且同等
值得的貢獻路徑——一年模擬落點：貢獻型 ≈ 850、實踐型 ≈ 860、潛水型（每天登入＋看完課）≈ 560。
潛水型明顯較低但不是零（他確實在學）。

**提議中的分數表**（數字待實跑校準，比例是重點）：
- Track A 挑戰積分：打卡/天 **1**｜連續 7 天 **+3**（入袋）｜全勤 **+20**（終局）→ 滿分 104
- Track B 生涯里程：每日登入 **1**｜被讚 **3**｜**被收藏 15**｜完成單課 **5**｜完成整門課 **30**｜
  通過測驗 **5**｜**賽季挑戰積分 1:1 轉入**（讓兩軌相連、實踐計入年資）

**邀請新人：不給積分**（修修裁決）。理由：付費產品裡獎勵招募，尺度過了有直銷味——朋友加入
要付一萬多，推薦動機一旦被積分化，會員感覺得到。
**替代方向：分潤計畫（PARKED ~2029+，邊界已裁決）** — 對標樊登讀書會 business model。
修修 2026-08-22（Q13）：分潤**至少三年後**才會發生；現階段目標＝社群活躍、機制建立、
organic 成長。角色分工一句話：**點數是維持參與度與活躍的機制，分潤是擴大的機制**。
全案細節延後，但三條邊界今天裁死：
- **貨幣絕緣（憲法級）**：分潤走**法幣軌**，由 **FluentAffiliate** 負責（站上已裝
  `fluent-affiliate` 1.6.0 ＋ Pro 1.2.0，**inactive**，2026-08-22 驗證）；**貝里永不兌現金、
  分潤永不發貝里**。理由：①貝里一旦有隱含匯率，「打卡一天」瞬間變成「賺 NT$X」，
  overjustification（p72）污染的不只分潤線，是整個點數經濟；②閉環點數與可兌現點數是兩種
  不同的法律／會計生物——兌換設計剛用「解鎖購買資格制」把負債結構性消滅，不能從分潤
  後門放回來；③gamification ledger 永不碰金流，永不需要金流級對帳／發票／稅務 audit。
  原「第三種貨幣能否互換」的開放問題**已關閉：不可互換**。
- **梯次制不為分潤讓路**：永久排除「為了分潤常態開放報名」——那會摧毀同步挑戰、
  cohort onboarding 與集體結算的全部行為設計。
- **預設候選（日後分潤 grill 的起點，非定案）**：開船週成交＋全年攬客——推薦連結全年
  有效、朋友點擊即進 waiting list（歸因記在推薦人），開船週成交、季結分潤。
  低成本前置：waiting list 表單從第一天就放「誰介紹你來的」欄位（歸因無法回溯補記）。

**會員方案：季度會員 ＋ 年度會員**（修修 2026-08-22 補充）。硬性產品承諾：
**季度會員必須至少完整體驗一次挑戰**。這使訂閱週期與賽季週期成為同一件事——
賽季不只是機制，是產品交付單位。

**賽季日曆（修修 2026-08-22 裁決，13 週制）**：
| 週次 | 內容 |
|---|---|
| W1 | **開放加入（僅此一週）** ＋ onboarding：導論課、答題、寫宣言 |
| W2 | 直播——介紹社群功能、歡迎新夥伴 |
| W3–W11 | **挑戰進行（9 週 ≈ 62 天）** |
| W12–W13 | 結算、公布成績、慶祝直播 |

- **入會梯次：1/1、4/1、7/1、10/1，各開放一週**。其餘期間收 **waiting list**。
- **年度會員只在 1/1 開放**。
- 62 天挑戰期剛好貼合習慣自動化中位數（66 天，Lally 2010）——日曆是為習慣週期設計的，
  不是把挑戰塞進行事曆。
- 一次解三個問題：梯次制自然形成 **cohort**（同批人一起 onboard、一起開跑）；
  季度會員必吃完整挑戰（產品承諾成立）；空檔月消失（W12-13 結算 ＋ W1-2 迎新皆是內容）。
- Waiting list ＝ 把「隨時可買」變成「等下一班船」，**稀缺性（CD6）從行銷話術變成真實結構**。

**社群既有節目層**（修修 2026-08-22 補充，Claude 原盤點漏掉）：每月 2 場直播、每週身心健康
研究、每季各地實體聚會、全社群線下活動（如電影包場）。**挑戰只是疊在這層之上，不是唯一
內容來源**——所以不存在「賽季空窗期」。

**⛔ 硬約束：不得增加修修的產出負荷**。修修原話「其實有點累」——現有負荷已含 2 場直播/月
＋每週研究＋每季聚會＋YouTube＋podcast。任何**需要他持續產出新內容才能運作**的機制，
**預設拒絕**。Gamification 的定位是**測量並放大他已經在做的事**，不是要求他再多做一件。
（延伸議題 PARKED：哪些既有製作負擔可以讓 Sanji 吃掉——行為設計收尾後單獨拉一輪。）

**計分判準第二條：內在動機已足夠者，不計分**（修修 2026-08-22 裁決）。
Octalysis **p72 Overjustification Effect**：對本來就有內在動機的行為加外在獎勵，會**削弱**
該動機。所以：
- **實體聚會、線下活動 → 不計分**。理由三條：①本身即有吸引力（加分等於暗示它是苦差事）；
  ②難以記錄；③**會系統性懲罰去不了的人**（住得遠／輪班／行動不便／健康受限）——在健康
  主題社群裡用出席計分，等於讓身體狀況最差的會員分數最低，與社群目的直接牴觸。
- **直播出席 → 不計分**。出席 ≠ 參與，掛著不互動即可得分＝自己生成且無有效節流（🔴 層）。

**兩條計分判準（合併）**：
| 問題 | 若答「是」 |
|---|---|
| 能不能單方面刷？ | 🔴 不計分 |
| **本來就有內在動機嗎？** | 🔴 不計分（加了反而削弱） |
第二條更難察覺——被它否決的往往是**最有價值的行為**，正因有價值所以人本來就想做，
因而最不需要點數。**點數應保留給需要「啟動能量」的行為**（早上六點爬起來冥想五分鐘，
沒有人天生想做）。

**貨幣模型：賽季制貝里（C 案）**。貝里賽季內賺、賽季內或季末商店花掉、**季末歸零**；
生涯里程永久累積決定等級稱號。裁決理由：**用結構解決負債問題而非用政策**——貨幣活不過
一季，故無長期負債累積、無過期政策爭議、永遠不會有 Starbucks 式的規則調降信任爆炸
（panel Fable 標為「致命」的「央行缺席」問題因此蒸發）。季末歸零同時製造真實急迫感（CD6）。
代價：買不起跨季大件——**但這是保護**：獎品應是「一季努力的具體回報」，而非攢三年的遠方目標。
與賽季日曆天然吻合：W12–13 結算週＝季末商店開張 → 下單 → 歸零迎新賽季。

**獎勵設計：SAPS 模型（p483）＋ 實體改為「解鎖購買資格」**
- 框架排序：**Power > Access > Status > Stuff**。p484 明載 booster（Power）通常是**最有效**的
  獎勵類型——兌換行為本身驅動更多期望行為；實體商品拿到手後與系統的關係就結束了。
  **最有效的獎勵剛好是零成本的那些。**
- **實體商品不做免費兌換**（修修算過：100 人規模年 COGS 約 12 萬，1,000 人規模 120 萬，
  加海外運費與打包人力不可行；且海外會員收到的是運費帳單而非禮物）。
  改為**用貝里解鎖「限量商品的購買資格」，仍照價付款**——獲利不受侵蝕、稀缺性更強
  （價值不在免費而在「只有夠格的人買得到」）、運費由購買者自付、一年一次 drop 人力可控。
  框架依據：免費贈品是 Stuff（最弱），「只有你能買」是 Status（最強）。
- 既有實體：創始船長人手一件 T-shirt（修修影片中所穿、非賣品）；未來低成本 merch 如馬克杯。

**⚠️ 獎勵不得是「會員本來就有的權益」**——把既有權益重新包裝成獎勵＝先拿走再發回來，
會員立刻感覺得到。**獎勵要的不是新內容，是新的「取得方式」**：
| 維度 | 需新內容？ | 例 |
|---|---|---|
| 時間差 | ❌ | 新內容提前 48 小時（邊際成本嚴格為零，不剝奪任何人） |
| 策展 | ❌ | 賽季精華包、主題合輯（Sanji 產） |
| 參與權 | ❌ | 下季主題投票、指定直播問題 |
| 加成 | ❌ | streak 護盾、雙倍週 |
| 外觀 | 一次性 | 稱號、徽章、頭像框、名字顏色 |

**內容庫不足的解方：挑戰本身就是內容工廠**。62 天 × N 會員的打卡、轉變故事、逆轉回歸，
是會員產生、Sanji 策展的資產。第二季起「上季精華回顧」即為現成 Access 級獎勵，修修零投入。
外包設計預算建議**優先投 Status**（徽章/頭像框/名字色階一次設計無限使用，且十年裡每天被看見；
馬克杯只有買的人看得到）。

**等級系統（2026-08-22 定案，經模擬驗證）**
- **遞增式曲線，15 階，Lv.15 = 20,000 生涯里程**。門檻表（圓整，人記得住）：
```
Lv.2      10          Lv.9    2,400  (1.60x)
Lv.3      30 (3.0x)   Lv.10   3,500  (1.46x)
Lv.4     100 (3.3x)   Lv.11   5,000  (1.43x)
Lv.5     250 (2.5x)   Lv.12   7,000  (1.40x)
Lv.6     500 (2.0x)   Lv.13  10,000  (1.43x)
Lv.7     900 (1.8x)   Lv.14  14,000  (1.40x)
Lv.8   1,500 (1.7x)   Lv.15  20,000  (1.43x)
```
- 設計性質：**倍率由 3.3x 單調收斂到 1.4x**——前段爆發成長（Beginner's Luck），
  後段穩定純指數，讓「再升一級要多久」有可預期的直覺（永遠約是上一級的 1.4 倍時間）。
  原始推導曲線 `3×(n-1)^3.2`，圓整後形狀不變（模擬驗證），日後加階可依此基準延伸。
- 模擬結果（`scratchpad/level_sim2.py`，五種原型 × 14 年）：全能型 Y1=8 → Y10=14 → **Y13=15**；
  實踐型 Y10=13；貢獻型 Y10=12；潛水型 Y10=10；間歇型（10 年只活躍 3 年）Y10=8。
  **Lv.15 需核心成員投入約 13 年**——刻意留下超出十年設計視野的頂部空間。
- **稱號並行制**：等級稱號（基礎身分）＋ 成就稱號（特定事蹟，如四季全勤、引路人、首季開拓者），
  **成就稱號可自選展示**——那個選擇本身是零成本的 CD3＋CD4，也讓同等級的人看起來不一樣。
- **命名歸修修**（等級名須落在艦隊世界觀；原生 plugin 的 "Space Initiate/Pathfinder" 是通用罐頭）。
- ⏸ PARKED：Lv.14 之後的停滯（實踐型 Y11–13 卡同一級）。候選解：加階到 20，或
  **封頂後改累積星等**（每 20,000 里程一顆★，`Lv.15 ★★★` 無上限）。修修指示之後再想。

**登入計 1 分／天定案**（修修裁決，推翻 Claude 傾向的「登入不計入等級」）。理由：
①**登入是潛水型會員唯一能取得積分的管道**——即便只是上來看看，某篇文章可能就觸發了日後的改變；
②十年全勤也才 3,650 分；③**在付費社群裡，潛水者是資助者不是純成本**——十年訂閱費付了直播、
製作與社群本身的存在，用「不發文」把他壓在底層在商業上也是錯的判斷。
指數曲線讓此決策安全：潛水型 Lv.10 vs 全能型 Lv.15 看似差 5 級，**實際差 5.7 倍里程**
（3,500 vs 20,000）——曲線已完成拉開差距的工作，不需靠削減登入分。

**等級解鎖「參與權」（方向定案 B，細節 PARKED）**：等級不給更多內容，給**在社群裡做事的資格**。
- ⛔ **紅線：等級不得解鎖內容存取**。會員付一樣的錢卻因等級低看不到東西，那不是遊戲化，
  是把已售出的權益收回。
- ✅ 給「做事的資格」：Lv.12 能主持讀書會，不會讓 Lv.3 少看到任何東西——前者是社群的擴張，
  後者是權益的分割。
- 對映 Octalysis Endgame（p469：mentor new players / moderate / ambassador）。
- **十年尺度的關鍵副作用：自動長出接班梯隊**。當 Lv.10+ 能主持讀書會、帶打卡、接待新人，
  社群就不再只靠修修一人撐——那是它能跑十年的唯一方式，也是「有點累」這個問題的解答方向。

**privilege 的四道篩選關**（修修 2026-08-22 指示細節再想，此為篩選判準）：
①對修修零持續成本 ②天然稀缺（本質上給不了所有人，非刻意扣住）
③是「做事的資格」非「消費的權利」 ④最好還能減輕他的負擔
- 修修初步構想：exclusive 群組、exclusive meetup。
- ⚠️ **群組的框架決定成敗**：同一個私密頻道，「VIP 貴賓室」製造圈內外對立與怨氣；
  「工作小組／負責帶新人的人在此協調」則是責任的房間，產生嚮往。命名與用途是關鍵。
- ⚠️ **meetup 必須翻轉**：解鎖的不是「參加專屬聚會」（那違反零負荷硬約束），
  而是**「主辦聚會的資格」**——修修零投入、主辦比出席更有份量、且這是**千人規模時
  地方分會結構的來源**（大型社群唯一的擴張方式）。

**雙數字經濟：經驗值（XP）＋ 貝里**（修修 2026-08-22 定案，以資深遊戲設計直覺推翻 Claude
提議的「單一數字鏡像錢包」）。
- **鑄幣：XP 一律為 10 的倍數，貝里 = XP ÷ 10**（保證貝里恆為整數）。一次事件、兩本帳。
- **為何必須不同量級**：同尺度會讓玩家把兩者當成同一個東西，於是花錢產生「進度倒退」的
  錯覺（即使等級沒掉）。且兩者心理功能不同——**XP 要高頻細碎永遠在動**（持續進步感）；
  **貝里要成塊、累積到決策點**（儲蓄與選擇感）。同尺度會讓後者消失。
- 附帶好處：兩邊可**獨立調校**（改升級節奏動 XP 不動經濟；改物價動貝里不動等級曲線）。

| 行為 | XP | 貝里 |
|---|---|---|
| 每日登入 | 10 | 1 |
| 打卡一天 | 10 | 1 |
| 連續 7 天獎 | +30 | +3 |
| 全勤獎 | +200 | +20 |
| 被讚 | 30 | 3 |
| **被收藏** | **150** | **15** |
| 完成單課 | 50 | 5 |
| 完成整門課 | 300 | 30 |
| 通過測驗 | 50 | 5 |

**等級門檻（×10 版，曲線形狀不變，模擬驗證軌跡一致）**：
```
Lv.2     100    Lv.6   5,000    Lv.10  35,000    Lv.13  100,000
Lv.3     300    Lv.7   9,000    Lv.11  50,000    Lv.14  140,000
Lv.4   1,000    Lv.8  15,000    Lv.12  70,000    Lv.15  200,000
Lv.5   2,500    Lv.9  24,000
```
十年落點：全能型 166,500 XP（Lv.14，Y13 才到 Lv.15）／實踐型 101,000（Lv.13）／
貢獻型 86,000（Lv.12）／潛水型 42,000（Lv.10）／間歇型 15,000（Lv.8）。

**排行榜不是第三個數字，是一個查詢**：ledger 一筆 earning 事件一列（含 source／season／
timestamp），本季挑戰榜 = `SUM(xp) WHERE season=本季 AND source∈挑戰類`；本週榜換時間範圍即可。
系統中因此**沒有「挑戰積分」這個要同步、要歸零、要對帳的東西**。

**貝里不過期**（推翻 Claude 前一輪的「季末歸零」）。原理由「長期負債累積」已不成立——
負債的前提是兌換有 COGS，而實體已改為「解鎖購買資格、照價付款」、數位獎勵零邊際成本，
**故根本沒有負債**（有人存十年五萬貝里全換 streak 護盾與名字顏色，成本為零）。
急迫感改用**商店輪替**製造：每季限定外觀季末下架，錢還在但東西買不到——一樣有 FOMO(CD6)，
沒有任何人被沒收。囤積由 booster 是消耗品＋外觀季節限定自然解決。
定價量級（認真參與者一季賺 250–300 貝里）：booster 50–100／季節外觀 150–250／
限量商品購買資格 250–300。

**驚喜引擎（CD7，2026-08-23 定案）**。修修指示把「驚喜」的觸發條件正式寫進設計。六原則：
1. **觸發條件與獎池 pre-defined、git 版控、對外保密**——公布＝變成合約（CD2 的期望獎勵），
   保密＝delight（CD7）。新觸發／新獎勵一律修修審核入庫，**Sanji 只執行不發明**。
2. **心理學紅利：非預期獎勵幾乎不觸發 overjustification**——侵蝕內在動機的是「預期中的
   條件式獎勵」（p72 的實驗設計正是如此）。**驚喜是整個系統裡加獎勵最安全的地方**，
   比新增任何公告計分規則都安全。
3. **洩漏安全判準**：每個觸發即使被會員破解流傳，被 farm 的行為**仍必須是我們要的行為**
   （例：farm「連續在場 30 天」＝真的在場 30 天，無所謂）。
4. **稀缺紀律**：驚喜的價值＝稀有。每人每年 1–2 次上限；變成月例＝變成薪水。
5. **帳本紀律**：`surprise_grant` 事件、idempotency `surprise:{trigger}:{user}:{period}`、
   帶 rule_version——對內完全可稽核，對外不公布規則。與「船長特別獎」（沖正補償用）分開記。
6. 獎池只用零成本項（小額貝里、稱號、booster、精華包收錄）——SAPS 紀律不因驚喜鬆動。

起始觸發集（草案，修修增刪；對外保密）：
| # | 觸發 | 服務對象 | 獎勵方向 | 上限 |
|---|---|---|---|---|
| S1 | 連續登入 30 天、從未發過文 | 潛水型 | 小額貝里＋「看見你每天都在」 | 一生 1 次 |
| S2 | 人生第一篇貼文 | 潛水→貢獻橋 | Sanji 具名歡迎留言（**不給分**——發文數是 🔴） | 一生 1 次 |
| S3 | streak 斷後自力回到連續 7 天 | 實踐型 | 「逆轉」類稱號 | 每季 1 次 |
| S4 | 單篇打卡被收藏 ≥5 | 貢獻型 | 選入賽季精華包＋通知 | 每季 |
| S5 | 全季打卡時段高度固定（如清晨） | 實踐型 | 趣味稱號（「晨型人」） | 結算時 |

**訊息治理：Sanji 不自創文案**（修修 2026-08-23 裁決：用詞與獎勵皆先 pre-defined）。
- 所有對外訊息取自 **template registry**（`agents/sanji/templates/`，git 版控、修修審核）；
  獎勵只能從 pre-defined 獎池選。**LLM 的裁量權在「判定與選擇」，不在「措辭與發明」。**
- 三層：①**例行回饋**（打卡回覆）＝approved 變體池輪替＋slot 填值（變體維持人味，用詞零自由）
  ②**儀式訊息**（召集信、結算通知、驚喜通知）＝整篇 template＋slots
  ③**例外溝通**（退件、補件）＝reason code → 對應 canned template；
  **無對應 code 的案例升級給修修，不 freestyle**。
- 效果：品牌口吻恆定、杜絕幻覺文案事故、修修對每個字有事前控制權。
_Avoid_: 讓 Sanji 每次即席生成回覆文字（十年 × 數萬則訊息，總有一則會出事）

**⚠️ 註記：privilege 四道篩選關不是 Octalysis 框架內容**，是 Claude 綜合而成——
③「做事的資格非消費的權利」最接近框架（SAPS p483-485 ＋ p484 booster 洞察）；
②天然稀缺只沾到 CD6 的引申；①零持續成本與④減輕負擔**完全來自本專案自身的營運約束**。
框架真正提供的是 SAPS 分級與 Endgame 期望行為清單（p469）。

**Vendor 升級紀律** = 捕捉層掛在 vendor hooks 上，十年內 FluentCommunity 會改版數十次，
所以：①維護 **contract probe** 腳本——驗證我們依賴的每個 hook 名稱/簽名、關鍵表結構是否
仍存在，一分鐘可跑完；②**關閉 FluentCommunity 自動更新**，改手動節奏（對新版檔案跑
probe → 綠燈才更新 → 更新後再跑 probe ＋煙霧測試）；③**監控歸 Franky**（既有職責：套件
更新/CVE/health check）——偵測新版釋出、自動跑 probe、回報，修修只在紅燈時介入。
_Avoid_: 讓 FluentCommunity 自動更新（hook 改名會讓捕捉層**靜默失效**，要等每日對帳
才發現漏帳；十年尺度下必然發生）

## 相關文件

- [`docs/capabilities/fluent-client.md`](../../docs/capabilities/fluent-client.md) — Fluent 全家桶統一
  Python client 能力卡（Phase 2/3 planned，未開工）；未來 Sanji 落地時的介面規劃
- [`docs/case-studies/2026-04-22-Nakama-WP-+-Community-整合架構規劃.md`](../../docs/case-studies/2026-04-22-Nakama-WP-+-Community-整合架構規劃.md)
- [`CONTEXT-MAP.md`](../../CONTEXT-MAP.md) — Sanji 的 bounded context 定義
