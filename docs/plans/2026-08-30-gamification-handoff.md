# 自由艦隊遊戲化 — 交接總表

> **建立：2026-08-30** · 交接對象：Codex（或任何接手的 agent／人）
> **狀態**：分享期運轉中（T0 ＝ 2026-08-24 09:43 CST），尚未對會員公告玩法。
>
> 這份文件回答三件事：**系統長什麼樣**、**做到哪裡**、**還有什麼沒做完或沒決定**。
> 操作事實（部署指令、開關、地雷）在 `memory/shared/reference/fleet_gamification_stack.md`
> —— 那是跨 agent 記憶，Codex 讀得到；**Claude 專屬的 `memory/claude/**` Codex 讀不到，
> 不要把知識留在那裡。**

---

## 1. 這是什麼

自由艦隊（`fleet.shosho.tw`，FluentCommunity 付費社群）的記分／等級系統。會員的行為
（被讚、被留言、被收藏，未來加上挑戰打卡與課程）換算成 **XP＝海里**，累積成 **16 座島的等級**。

**北極星不是留存率，是「會員有沒有真的養成好習慣」**；點數的唯一正當用途是替
「值得做但需要推一把」的行為墊付啟動能量。完整營運方案見
[`docs/plans/fleet-gamification-master-plan.md`](fleet-gamification-master-plan.md)（v1.2）。

**兩個憲法層鐵則**（改動前必須理解，違反就是設計事故）：

1. **他人／系統驗證才給分。** 自己就能生成的行為（發文數、自己標記完成）不給分，
   否則養出刷分場。這是「自己標記課程完成 ＝ 0 分」的來源。
2. **公告日 ＝ 規則凍結日。** 玩法公告之後，門檻與權重**只降不升、永不回溯**；
   已公告的福利只加不減。**目前尚未公告，所以現在還是可以自由調整的視窗。**

---

## 2. 架構與資料流

```
FluentCommunity 事件（讚／留言／收藏／打卡／完課）
        │  ← capture 層：hook 進來就寫，不做任何判斷
        ▼
zcjf_nakama_gam_events          （raw stream，捕捉一切）
        │  ← Sanji 90s 輪詢 REST 取新事件
        ▼
agents/sanji/rules.py            ← 唯一的規則所在地
   grant_for_event() → 算 XP、算等級帶、產冪等鍵
        │  ← 只有在 SanjiConfig.scored_sources 裡的來源才入帳
        ▼
zcjf_nakama_gam_grants           （append-only 帳本，冪等鍵擋重複）
        ▼
zcjf_nakama_gam_balances         （投影：xp_total / level / level_label）
        ▼
航海日誌（profile tab）＋ Sanji 的公開祝賀留言
        ▲
        └── 每日 05:00 對帳：補漏、recount、等級回沖
```

**分層鐵則：規則只存在 `rules.py`，plugin 是笨層。** plugin 不知道等級門檻，
只拿 `level_fields()` 給的四欄（level / label / min_xp / next_xp）畫進度條。
要改分數或曲線，**只碰 `rules.py`**。

**分階段上線靠 `scored_sources` 白名單**：事件照捕捉、cursor 照走，不在白名單的來源
只是不入帳。日後加開**不回溯**。目前白名單＝`like_received` / `comment_received` / `bookmark_received`。

---

## 3. 檔案地圖

| 路徑 | 是什麼 |
|---|---|
| `agents/sanji/rules.py` | **分數表、16 島曲線、位階、冪等鍵。純函式零 I/O** |
| `agents/sanji/loop.py` | 90s 輪詢、判定漏斗、公開回覆、`LevelStamper` |
| `agents/sanji/reconcile.py` | 每日對帳（補漏／recount／等級回沖） |
| `agents/sanji/settings.py` | `SanjiConfig`，含 `scored_sources` |
| `agents/sanji/store.py` `judge.py` `templates.py` `wp_client.py` | 儲存／LLM 判定／文案／WP REST client |
| `agents/sanji/level_curve_sim.py` | 等級曲線校準器（`python -m agents.sanji.level_curve_sim`） |
| `agents/sanji/CONTEXT.md` | **逐條技術裁決與 vendor 縫隙——工程上最重要的一份** |
| `wp/fleet-gamification/includes/class-capture.php` | 捕捉層，每個 hook 都註明 vendor file:line |
| `wp/fleet-gamification/includes/class-ledger.php` | events / grants / balances 寫入 |
| `wp/fleet-gamification/includes/class-rest.php` | `nakama-gam/v1` 端點 |
| `wp/fleet-gamification/includes/class-portal-ui.php` `class-voyage-page.php` | 航海日誌 UI |
| `wp/fleet-gamification/includes/class-video-progress.php` | 影片觀看橋接（2026-08-28） |
| `wp/fleet-gamification/assets/video-progress.js` | SPA-safe 觀看區段 tracker |
| `wp/fleet-gamification/tools/contract-probe.php` | **33 項 vendor 契約檢查** |
| `wp/fleet-gamification/tools/bunny-media-migrate.php` | Bunny → FluentPlayer 遷移（dry-run／apply／rollback） |
| `tests/test_sanji_handler.py` | 目前唯一的測試 |
| `docs/plans/fleet-gamification-master-plan.md` | 營運方案 v1.2 |
| `memory/shared/reference/fleet_gamification_stack.md` | 操作事實（Codex 讀得到） |

**人看的交付物**在 `E:\Projects\自由艦隊\遊戲化專案\`（方案、航道圖、島嶼圖標 SVG）。
canonical 永遠在 repo，該資料夾放輸出副本。

---

## 4. 做到哪裡（已完成）

| PR | 內容 |
|---|---|
| #1195 | 架構與行為設計完整裁決（`agents/sanji/CONTEXT.md`） |
| #1197 | plugin ＋ Sanji 服務，Phase 1 核心 |
| #1200 | LSCache 硬止血（`litespeed_control_set_nocache`） |
| #1204 | 航海日誌：profile tab、等級曲線 v2、活動明細與類型篩選 |
| #1206 | 偉大航路等級表 ＋ 船長身份軌 |
| #1208 | master plan v1.1 |
| #1209 | 留言計分（被留言 30 XP，一文一人一次） |
| #1210 | 航海日誌明細按內容彙整 |
| #1215 | Bunny 影片搬進 FluentPlayer 的遷移工具 |
| #1216 | 影片觀看橋接（接上 FluentPlayer 伺服器權威判定） |

**線上運轉中**：append-only 帳本、每日對帳與告警、Sanji 判定與公開祝賀、
航海日誌（等級／海里進度／明細／類型篩選）、船長身份儀式（啟航宣言）、
歷史認列（上線前 1,624 讚 ＋ 527 留言全數入帳）。

**2026-08-28 額外完成**：11 課 Bunny 影片搬進 FluentPlayer；GitHub PAT 撤銷、
VPS 改走唯讀 deploy key。

---

## 5. ⚠️ 未驗證（接手第一件事）

**影片觀看橋接是端到端未驗證的。**

伺服器端全綠：class 載入、三個 hook 掛上、tracker 檔案 HTTP 200、contract probe 33/0 FAIL。
**但 `video_watched` 事件數是 0，`flp_visits` 也是 0**——代表部署後沒有任何人播過課程影片，
所以 tracker 在真實 DOM 上會不會動**仍然未知**。

驗證方式：登入後開一課（例如 lesson 141「part 1: 年度反思」）播放、拉到接近結尾、暫停，然後：
```bash
wp db query "SELECT * FROM zcjf_nakama_gam_events WHERE event_type='video_watched'"
```

三種結果的意義：
- **有事件** → 通了，接下來數月純觀測
- **沒事件、console 無錯** → 多半是 Vidstack 事件名或 `currentTime` 讀法與推測不符，調 tracker
- **console 有錯** → 照錯誤修

失敗模式是「events 表安靜」而非頁面壞掉（tracker 整支包在 IIFE 裡），
所以**現在的 0 筆無法區分「沒人看」與「壞了」**。

---

## 6. 待決策

| 事項 | 現況 | 備註 |
|---|---|---|
| **完課分數** | `rules.py` 仍是舊值 50／500／50；**暫定改為 0／100／50 但尚未落地** | 自己標記完成無他人驗證，過不了憲法第一條。站上那門 24 課存檔課照舊值值 1,700 海里（≈Lv.6） |
| **位階線** | 七個裡兩個是異物 | 「霸王色」是霸氣的一種＝能力不是位階；「傳說船長」原作沒有。**開場提案：砍到五個全部原作實有**（超新星 Lv.5 → 最惡世代 Lv.8 → 王下七武海 Lv.11 → 四皇 Lv.14 → 海賊王 Lv.16） |
| **位階 UI** | `rules.tier_for()` **全 repo 零 caller** | 定稿了但會員看不到。接進 `level_fields()` 加一欄即可 |
| **影片分數** | 觀測中，不計分 | 等數月真實資料再定；80% 閘門（`require_video_completion`）目前 `no` |
| **品牌橘收斂** | `class-voyage-page.php` 是 `#e8913f`，canonical 是 `#e98965` | 一個檔四處色碼；修修已裁定以 `#e98965` 為準 |
| **福利階梯** | 16 條只在方案文件，程式零實作 | 只有 4 條需要程式（徽章 ×2、稱號欄、提前 48h、黃金鐘），其餘 11 條是營運承諾 |
| **島嶼圖標** | v1 已畫（16 枚 SVG，兩種狀態共用 symbol） | 在 `E:\Projects\自由艦隊\遊戲化專案\島嶼圖標 v1.svg`，優化中 |

---

## 7. 工作規範（動這個系統時）

**Worktree**：主倉庫 `E:\nakama` 是 control plane 不是 write surface。任何會寫檔的任務先開
sibling worktree。禁止 `git add .`——只 stage 明確列出的路徑。

**PR**：conventional commit（`feat(gam):` / `fix(gam):`），CI 綠後 squash merge。
`docs/design-system.md` 定義的美學系統，**任何 UI surface 出手前必讀**。

**部署**：`ssh nakama-vps 'cd /home/nakama && ./scripts/deploy_vps.sh'`。
⚠️ **`agents/sanji` 的 PR 合併後要儘快 deploy——merge ≠ deployed**，
Sanji 不會自己換腦，會持續用舊曲線蓋 level 欄。

**Vendor 更新**：FluentCommunity／FluentPlayer 更新**前後各跑一次 contract-probe**。
任何 FAIL ＝ 不要更新／檢查對應的捕捉或橋接程式。FluentCommunity 已關自動更新。

**記憶**：Codex 寫 `memory/codex/**` 與 `memory/shared/**`（後者 bilingual frontmatter 強制）。
memory commit **絕不進 feature branch**，走獨立 worktree。

**改分數／曲線的紅線**：門檻**只准調低或插入、永不調高**（調高會讓既有成員掉島，
不可逆的信任破壞，CI `test_thresholds_never_rise` 擋）。每筆授予帶 `RULE_VERSION`，
規則改版**只影響未來，永不回溯重算**。冪等鍵格式一旦上線即凍結。

---

## 8. 需要知道的陷阱

完整清單在 `memory/shared/reference/fleet_gamification_stack.md`，最容易踩的三個：

1. **`fcom_posts.meta` 是 PHP serialize 不是 JSON**——`json_decode` 會靜默回 null。
2. **`wp eval-file` 不允許 `declare(strict_types=1)`**（必須是檔案第一語句，eval 語境做不到）。
3. **LiteSpeed 快取 REST GET**——只送 header 不夠，要 `do_action('litespeed_control_set_nocache')`。
   症狀是 Sanji 吃到殭屍空 events 靜默空轉。

---

## 9. 詞彙

| 詞 | 意思 |
|---|---|
| 海里 | XP 的對外說法，1 XP ＝ 1 海里 |
| 生涯里程 | 永不歸零的 XP，決定等級 |
| 挑戰積分 | 每季歸零的競賽積分，決定挑戰排行榜（尚未實作） |
| 島 | 等級，16 階，風車村 → 拉夫德魯 |
| 位階 | 隨等級取得的江湖稱呼，整條航路只換幾次 |
| 船長／艦長／見習船長 | 完成啟航宣言者互稱船長；艦長專指修修；未發宣言者為見習船長 |
| 航海日誌 | 個人檔案的 gamification 分頁 |
| 分享期 | 2026-08-24 起的階段：只計被讚／被留言／被收藏 |
| 沖正 | 帳務糾錯：以負值紀錄沖銷錯帳，公開可查 |
