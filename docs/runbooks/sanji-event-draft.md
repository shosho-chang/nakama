# Sanji 建社群活動／開內部會議（Slack → freedom-fleet 執行）

> 2026-11-16 改寫：FCA 外掛退役，改用 FluentCommunity 2.14 內建的 FluentEvent。
> 分工依 ADR-071 修訂（分工 B）：nakama 只管 Slack 對話；開 Zoom、做封面、發活動都在
> freedom-fleet 執行（`python -m fleet.events`，經 `shared/fleet_events.py` 呼叫）。

## 兩個功能

| 功能 | 觸發詞 | 做什麼 | 程式 |
| --- | --- | --- | --- |
| 建社群活動 | 建活動／建立活動／新增活動／開活動／活動草稿／幫我開一場 | 確認卡（含封面預覽）→ 確認後開 Zoom（線上）→ 在社群發**待審核**活動 | `gateway/handlers/sanji_event.py` |
| 開內部會議 | 內部會議／開會議／約會議／開個會／排個會／開一場會 | 確認卡 → 確認後只開 Zoom，開會前 15 分鐘在 #freedom-fleet 排提醒 | `gateway/handlers/sanji_meeting.py` |

兩個都有「建活動」字樣時走建活動。

## 建社群活動：誰在哪一關審

1. **內容審核在 Slack 確認卡**：名稱、時間、地點／Zoom、發在哪個 space、公告信、封面預覽連結、完整文案。
   社群上看不到待審核活動的內頁（FluentEvent `CommunityEvents::canViewFeed` 只放行
   published／unlisted），所以內容一定要在這裡看清楚。
2. 回「確認」後，活動以「待審核」發到 space：一般會員看不到。
3. **上線開關在社群**：打開那篇貼文 → Review → Approve Post（FluentCommunity Pro 內建審核）。
   核准那一刻才觸發「新貼文」通知；勾了公告信的話，核准後約 5 分鐘寄給 space 全部成員。

## 設定

| env（nakama `.env`） | 說明 |
| --- | --- |
| `SANJI_EVENTS_SPACE` | 活動發到哪個 space 的 slug。**沒設 Sanji 會拒絕建**。UAT 期間 = `test-dock` |
| `SANJI_EVENTS_SPACE_LABEL` | 確認卡顯示的 space 名稱（選填） |
| `SANJI_MEETING_CHANNEL` | 內部會議提醒頻道（選填，預設 #freedom-fleet `C0CG3UDSTGU`） |
| `FREEDOM_FLEET_DIR` / `FREEDOM_FLEET_PYTHON` | freedom-fleet 位置（預設 `/home/freedom-fleet`、`<dir>/.venv/bin/python`）；本機開發要自己設 |
| `SANJI_SLACK_BOT_TOKEN` | 下載附圖、排提醒 |

Zoom／OpenAI／WP（sanji 帳號）金鑰都在 **freedom-fleet 的 `.env`**，nakama 不再需要；子行程只拿最小環境變數，
nakama 的金鑰不會流過去。

Slack 前提：Sanji app 要有 `files:read`（下載附圖）與 `chat:write`（排提醒），而且要在 #freedom-fleet 頻道裡。

## 安全設計

- 文案逐字照抄：LLM 解析出的文案若不是使用者原文的片段，退回用原文。
- 沒設 `SANJI_EVENTS_SPACE` 一律不建，避免發錯 space。
- 中途失敗保留進度：封面做過不重做、Zoom 開過不重開。發活動那步若是逾時／5xx／站上已建但缺審核紀錄
  （freedom-fleet 回 `maybe_created`），回覆會提醒先到該 space 看有沒有那篇待審核貼文，避免重複。
- 封面預覽在確認前就上傳到網站媒體庫；取消不會自動刪。

## 排錯

| 症狀 | 原因 |
| --- | --- |
| 「這則活動資訊我沒整理成功」 | LLM 呼叫失敗或沒回 JSON；看 gateway log `event_parse ask failed`。 |
| 確認卡「預覽：這次沒產生成功」 | freedom-fleet `cover` 失敗；看訊息，常見是字型（VPS 要有 fonts-noto-cjk-extra）。 |
| 封面沒有 AI 插畫 | freedom-fleet `.env` 沒有 `OPENAI_API_KEY`（2026-11-16 時 VPS 兩邊都沒設）。 |
| 「找不到 freedom-fleet 目錄」「無法啟動 freedom-fleet」 | `FREEDOM_FLEET_DIR` / `FREEDOM_FLEET_PYTHON` 錯。 |
| 卡在「發待審核活動」，HTTP 403 | sanji 帳號缺 `fleet_community_events` 權限，或不是該 space 成員。 |
| 內部會議卡在「排 Slack 提醒」，`not_in_channel` | Sanji bot 沒被加進提醒頻道。 |
| 卡在「開 Zoom 會議」 | Zoom S2S 憑證錯誤或過期（freedom-fleet `.env`）。 |
