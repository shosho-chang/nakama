# Gamification Add-on 架構 Panel Review（2026-08-22）

**審查對象**：`agents/sanji/CONTEXT.md` 的 Gamification Add-on 架構裁決（Q1–Q7，約 15 條）
**方法**：`multi-agent-panel` skill v2 — 三個 parallel subagent 跑在 subscription quota，
各持不同 lens 且互不重疊，皆**未看過 drafter 的推理過程**（冷讀 artifact）。
**背景**：這批裁決全部是「同一個 AI 提出強推薦、使用者採納」，正是確認偏誤最危險的形態。

| Reviewer | Model | Lens |
|---|---|---|
| 一 | Fable 5 | 對抗性架構批判：十年失效模式、千人級壓力、裁決間矛盾、被忽略的第三選項 |
| 二 | Opus 5 | 事實查核與可實作性：逐條驗證 vendor 行為宣稱（SSH 上正式站對原始碼） |
| 三 | Sonnet 5 | 維運現實：一人維護的時間負擔、會腐爛處、複雜度預算、既有基建重用 |

---

## ⭐ 最高信號：兩個 lens 獨立撞到同一個洞

Fable（策略）與 Sonnet（維運）**審查角度完全不同、看不到彼此報告**，卻同時指向：

> **架構把「點數怎麼進來」設計到近乎偏執，「點數怎麼出去」完全空白。**

- Fable：「會計做滿了，**央行缺席**」——無過期政策、無 faucet 預算、無兌換定價治理
- Sonnet：「**兌換/出貨完全沒有架構裁決**」——地址、庫存、出貨、扣點沖正、缺貨客訴全無，
  且這是最容易變成隱藏工時黑洞的環節

**兩者都下同一結論：這輪 grill 不該收尾。**

→ **後續處置**：2026-08-22 行為設計 grill 已正面處理。實體商品改為「解鎖購買資格、
照價付款」，數位獎勵零邊際成本，**負債問題因此結構性消失**（見 CONTEXT.md 雙數字經濟節）。

---

## Reviewer 二（Opus）— 事實查核結果

**30 項宣稱逐條查核，行號分毫不差者 25 項**（含 `User.php:462-482` cacheAccessSpaces、
`User.php:754-765` syncXProfile 不重設 status、`AddToSpaceAction.php:115`、
`FunnelHelper.php:736-741` 無 status 過濾、`CourseEmailNotificationHandler.php:129-132`、
`fluent_community/xprofile/badge` 零 listener、創始船長徽章 117 人、
`order_partially_refunded` 未被監聽…）。

### 已套用的修正（Claude 已複驗爭議項後改入 CONTEXT.md）

| # | 問題 | 修正 |
|---|---|---|
| 1 | 5 處行號漂移 | Helper 1794→**1795**、PortalHandler 872→**869**、MembersController 123-152→**124-153**、IntegrationEventListener 406-410→**404-408**、FluentCommunityConnect 177-232→**174**（revoke 191-241、迴圈 206-228） |
| 2 | **`wp_cache_flush_group` 是死路徑** | LiteSpeed drop-in **未實作 flush_group**（複驗：`supports(flush_group): NO`）。本站沒有精準清 FC 快取的路，只能全站 flush 或等 TTL。連 `Utility::getOption()` 讀 option 都包在 object cache 裡 |
| 3 | STRICT_TRANS_TABLES 因果錯誤 | 容忍 `''` 的是 **WP 主動剝除**（`wpdb::set_sql_mode()`），**伺服器 global 本來就含 strict**（複驗）。故 `wp db query` 直寫 `status=''` **今天就會失敗**，非假設 |
| 4 | 「兩套互相打架的計分邏輯」措辭有誤 | 兩份 `recalculateUserPoints()` **演算法完全相同**；差異在寫入守門（CLI 棘輪 vs Pro 雙向同步），排行榜 SQL 是第三種語意 |

### ⚠️ 尚未處理的重大實作發現（實作階段必須面對）

1. **沒有程式化建立留言的 API**（最大實作缺口）。全 codebase 唯一建立留言處是
   `CommentsController.php:142` 的 `Comment::create()`，包在吃 `Request`、驗權限的 controller
   method 裡。要複製的副作用 ≥11 項，**最後一項 `do_action('comment_added')` 是鈴鐺通知的唯一來源**
   ——漏 fire 則「公開留言回饋」設計的原生通知全部落空。
   另有 `CommentsController.php:96-105` 重複留言防呆會擋掉同 user 在同 post 送出**完全相同文字**
   （Sanji 沖正後補發同樣內容會被靜默拒絕）。
   → Claude 已複驗屬實。**應列為 contract probe 第一等公民。**
2. **DM 介面其實已存在，可劃掉「未驗證」**：`fluent-messaging` 的
   `ChatHelper::sendMessage($body, $recipientId, $senderId)` @ `ChatHelper.php:884`——
   乾淨的公開靜態 API，自動找/建 thread、fire hook、失敗回 WP_Error。
   **這反轉了「留言便宜、DM 貴」的隱含假設**（結論可能不變，因社會證明的論證獨立成立）。
3. **plugin 載入順序**：`fleet-gamification` 字典序在 `fluent-community` **之前**（fle < flu），
   主檔被 include 時 vendor 類別**尚未可 autoload**。所有 vendor 類別存取須延遲到
   `plugins_loaded` 或 `fluent_community/portal_loaded`。實作第一天就會撞到。
4. **`feed/created` 觸發時間 ≠ 投稿時間**：5 個 call site 有 3 個非即時——
   內容審核**核准時**（本站 `content_moderation` = yes，模組是開的）、**排程發布時**
   （`SchedulePostHandler.php:89` 會覆寫 `created_at`）。對日期敏感的 streak/打卡是硬傷：
   `checkin:{user_id}:{date}` 的 `{date}` 取 `$feed->created_at` 還是 hook 觸發時間**必須明寫**。
5. `space/joined` / `course/enrolled` **參數數量不一致**（`Helper.php:1734` 三參 vs `:1758` 四參）
   ——捕捉層須用 3 參簽名或 `func_get_args()`。
6. `course/completed` 可對同一 user+course **重複 fire**（`CourseHelper.php:274-278`）——
   idempotency key 須為 `course_completed:{user_id}:{course_id}`，不可帶時間戳。
7. **`fcom_user_activities` 已是一份在跑的事件帳本**（comment_added 973 / feed_published 199 /
   course_completed 14）。可當**斷流偵測的獨立第二來源**逐筆對帳——現行設計只能抓全滅，
   拿它比對可抓「只漏某一類事件」的部分失效，那正是 hook 改名最典型的樣態。
8. **站上五個第三方 FCA add-on**（fca-content-manager / events-pro / hub / multi-reactions / pwa）
   全部 `Requires Plugins: fluent-community`——vendor 升級紀律的 probe 範圍必須涵蓋它們。
   且 `fca-multi-reactions` **把所有 reaction 一律以 `type='like'` 寫入原生表**，真實類型存在
   add-on 自有欄位（若規則想區分反應類型，原生 hook 拿到的永遠是 like）。
9. **`fcom_space_user.user_id` 是 `varchar(194)`**（xprofile 是 `bigint unsigned`）——
   自建表若與其 JOIN 會因型別不一致放棄索引，千人級規模下變慢查詢。
10. **別把每日對帳掛在 `fluent_community_daily_jobs`**（`boot/app.php:44-58`）——它靠
    「site admin 造訪 portal」才補排。安全網不能有這種依賴，應自排 cron / systemd timer。
11. 測試用「隱藏 Space」對 admin 不隱藏（`User.php:466-467` admin 直接取得全部 space）——
    **space allowlist 是唯一的隔離手段，不是雙保險之一**。
12. WP-CLI 另有 `download()` 指令（`Commands.php:166`）：硬編 BuddyBoss 抓取、會往 webroot 寫檔的
    開發者殘留。

---

## Reviewer 一（Fable）— 對抗性批判摘要

**致命級**
1. **經濟憲法缺席**（見上方交叉印證）→ 已由行為設計 grill 解決
2. **「不設 staging」的核心論據不成立**：「爆炸半徑＝gamification 失效而非社群壞掉」只在
   runtime hook 層成立；**PHP load 層**一個 parse error 就是整站白屏，而 `gam_enabled` flag
   救不了（flag 檢查跑在會炸的 PHP 之後）。三條裁決形成矛盾閉環：不設 staging（因爆炸半徑小）
   × rsync 直上 production × 爆炸半徑其實是整站。
   → **建議**：deploy 的 plugin 段落須 `php -l` 全檔 lint gate → rsync 暫存目錄後原子切換
   （symlink swap）→ 部署後打站台首頁與健康端點，非 200 自動 rollback。

**高級**
3. **fail-open 的立論在自己的漏斗裡不成立**：第⑥層 provisional 已先給分，沒有人被晾三天，
   故 48h 自動轉正實際上只是放棄追索權，代價是「Sanji 停機 ＝ 全社群可觀測的免費點數窗口」。
   → 建議：逾時維持 provisional（會員無感），累積至每週人工批次；自動轉正只給高信任分者。
4. **退件紅線押在未驗證能力上**：「公開退件是紅線 → 必須走 DM」，但同條裁決承認 DM 未驗證。
   若驗證失敗則**架構上不可能退件**。→ 已由 Opus 發現 `ChatHelper::sendMessage()` 存在而解除。
5. **千人級先斷的是尖峰延遲不是 quota**：30K 判定/月 ≈ 1,000/天，聚在晨間與睡前 2–3 小時窗，
   尖峰 3–6 件/分鐘持續數小時；單線程每件 10–30 秒 → **吞吐上限剛好落在需求區間內**。
   → 建議現在就把「輪詢（讀 cursor）與判定（消費 queue）解耦」寫進架構約束，屆時改一週、
   不寫則重構一個月。
6. **Sanji quota 與修修互動式工作搶同一個訂閱**：故障模式不是「Sanji 延遲」而是
   「修修自己的工具被限流」。→ 建議 Sanji 判定這種高頻背景負載從第一天就走 API 計費
   （Haiku vision 單價低，100 人規模每月個位數美元），把訂閱 quota 留給互動式工作。

**中級**
7. Append-only 帳本內嵌 email PII 與刪除權（PDPA §11）相撞，且十年間 email 會漂移。
   → 建議事件列只放 `user_id`，email 放**可 redact 的獨立對照表**。
8. contract probe 範圍比實際依賴面窄——設計依賴 vendor **internal class API**
   （`Helper::` / Model method），比 hook 更常變。probe 須涵蓋每個呼叫的 class::method。
9. wpvivid「原子一致」論據掩蓋新 split-brain：還原備份後 nakama cursor 與投影**領先**真相源。
   → 解藥是 idempotency 重放，但**前提是投影保留逐筆原始事件而非只存聚合值**（無裁決保證）。
10. 「PHP 零規則邏輯」宣言被 space allowlist / 排除名單 / invariant 打破。
    → 建議重述為可執行判準：**policy（給多少分、算不算數）歸 nakama；
    mechanism 與 guardrail（哪些 space 捕捉、誰被排除、唯一性約束、副作用）歸 PHP**。
11. `checkin:{user_id}:{date}` 把「一天」的定義凍進 DB unique constraint——日後想加
    「凌晨 3 點前算前一日」寬限規則，key 語意會與歷史分裂。→ `{date}` 應定義為
    nakama 判定的 **logical_date**，且時區與日界線須在 T0 定死。
12. **假二選一**：「建 staging vs 不建」漏掉第三條路——**用 wpvivid 備份還原到本機 Docker，
    migration 先對真實資料副本排練**。附帶紅利：**從未還原演練過的備份等於沒有備份**，
    而整個 ledger 選址論據都押在 wpvivid 上，其可還原性目前零驗證。
13. app password 是「窄 API 配寬憑證」——為打四個端點卻握有全 WP REST 能力。
    → 綁在只有 gam custom capability 的專用 user 上（Sanji 帳號正好要建）。

**已檢查無異議**：單向 pull vs webhook、Genesis 不回填、修修入榜修正（自我修正做得最好的一條）、
schema migration 政策本體、Observability 三層、ledger 放 WP 而非 state.db。

---

## Reviewer 三（Sonnet）— 維運現實摘要

**已由 Claude 逐條複驗屬實：**
- `deploy_vps.sh:109` 的 `NEED` 只認三個服務；`agents/*` 分支只重啟 thousand-sunny + gateway，
  **`nakama-sanji` 不會被自動重啟**（`agents/usopp/*` 有專屬分支可照抄）
- `agents/franky/health_check.py:69-72` 有大寫警語：registry entry 必須有實際
  `record_success()` caller，否則 probe 永遠 `hb=None` 走 skipped、**false-green**
- **前一個 commit `f53df5dd` 正是「YouTube 憑證過期沒 auto-refresh」的修復**——
  憑證過期在本 repo 是上週才發生過的事，故 Sanji 的 `CLAUDE_CODE_OAUTH_TOKEN` 是否
  auto-refresh **必須列為上線必要條件**，非可延後項
- `migrations/` 已有 `NNN_name.sql` + `.down.sql` 慣例，PHP 側 runner 應貼齊以降低雙心智模型負擔

**其他發現**
- 賽季結算/等級只被點名未設計；事後申訴流程未定義（建議輕量：申訴回覆打卡留言串 @Sanji
  → 升級強模型複判 → 仍不通過轉 Slack）
- Phase 1 無信任分，佇列量會超出「個位數/週」目標——需設定「幾週後評估是否提前上 Phase 2」
- WP App Password 無輪替/到期監控（沿用既有模式即可，但須列入斷流偵測的告警觸發原因）
- space allowlist 需人記得跟著新 space 同步；斷流偵測應覆蓋「特定 space 有活動但零事件」
- 事件流不走 `shared/events.py`（PHP 端連不到，合理），但須在 runbook 標明此例外
- 判定漏斗七層對「個位數/週」是過度工程——Phase 1 只做 ①②⑥⑦，
  **不要先把三層用不到的介面搭好**

**維運負擔總估**：穩態約 **1–2 小時/週**（六到七成可自動化）；
上線頭 2–4 週約 **3–5 小時/週**（新 systemd service 部署摩擦一次性 2–4 小時、
Phase 1 無信任分致佇列量偏高、Haiku 判準未經真實流量驗證、Genesis 歸零帶來的解釋型客服）。

---

## Panel 方法本身的紀錄

v2（subagent 版）**成功產出了單一 Claude 沒抓到的東西**——尤其是兩個 lens 對「兌換空白」的
獨立收斂，以及 Opus 對 `wp_cache_flush_group` 死路徑、plugin 載入順序、無留言 API 的發現。

但要記住 `multi-agent-panel` SKILL.md 裡的誠實聲明：**同家族 subagent 的去偏效果弱於跨廠商**，
三位一致同意時要留意那可能是共同盲點而非高信心信號。本次三份報告皆經 Claude 複驗爭議項後
才採納（skill Step 4 紀律）。
