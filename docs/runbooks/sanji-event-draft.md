# Sanji 建活動草稿（Slack → 自由艦隊活動草稿）

社群經理在 Slack 叫 Sanji 建活動：Sanji 解析訊息 → 回確認卡 → 回「確認」後依序
開 Zoom（線上活動）→ 做封面 → 上傳封面 → 在 fleet.shosho.tw 建**草稿**。
發布與寄會員信由社群經理自己在站上做，Sanji 不碰。

程式：`gateway/handlers/sanji_event.py`（流程）、`shared/fleet_cover/`（封面）、
`shared/zoom_client.py`、`shared/fca_events_client.py`。

## 用法（給社群經理）

@sanji 或 DM，一則訊息寫清楚：

```
幫我建活動
活動名稱：【六月選書】財富階梯
時間：6/28 晚上八點，大概一個半小時
文案：
（照貼要放在活動頁的文案，Sanji 會一字不改照抄）
```

- 觸發詞：建活動／建立活動／新增活動／開活動／活動草稿／幫我開一場。
- 讀書會可以在同一串附上書封圖（png/jpg/webp），會放在封面右側；其他活動沒附圖時用 AI 插畫。
- 確認卡出來後：回「確認」建立；直接說要改什麼（「時間改成 8 點半」）；回「取消」結束。
- 只有開串的人能在同一串接著回。

## 部署前一次性設定

| 項目 | 怎麼確認 |
| --- | --- |
| VPS 繁中字型 | `sudo apt-get install -y fonts-noto-cjk fonts-noto-cjk-extra`，再 `ls /usr/share/fonts/opentype/noto/NotoSansCJK-{Black,Bold}.ttc`。沒裝的話封面步驟會回報 `CoverFontError`。也可以用 env `FLEET_COVER_CJK_BLACK_FONT` / `FLEET_COVER_CJK_BOLD_FONT` 指定字型檔。 |
| WP 帳號角色 | `WP_FLEET_USERNAME` 那個帳號（目前 WP user 125）在社群後台「管理員」要是 **Event Manager**（2026-11-13 已設）。沒有的話建草稿和上傳封面都會 403。 |
| Slack scope | Sanji app 的 Bot Token Scopes 要有 `files:read`，加完要 Reinstall to Workspace。缺的話書封圖下載不到，草稿照建但封面不放圖。 |
| env | `ZOOM_ACCOUNT_ID` / `ZOOM_CLIENT_ID` / `ZOOM_CLIENT_SECRET`、`WP_FLEET_BASE_URL` / `WP_FLEET_USERNAME` / `WP_FLEET_APP_PASSWORD`、`OPENAI_API_KEY`（AI 插畫）、`SANJI_SLACK_BOT_TOKEN`。 |
| 白名單 | 社群經理要在 `config.yaml` `gateway.slack.extra_allowed_users.sanji`（PR #1382）。 |

## 安全設計

- 建活動一律帶 `status=draft`；外掛沒帶 status 會直接發布，所以 client 收到非 draft 的回應會當錯誤處理。
- `hide_from_global=no`：發布後會出現在全站活動列表（避免 2026-11 那次「只限空間、前台看不到」）。
- 文案逐字照抄：LLM 解析出的文案若不是使用者原文的片段，退回用原文。
- 中途失敗會保留進度：Zoom 開過不重開、封面傳過不重傳。建草稿那步若是逾時／5xx，回覆會提醒先到
  `https://fleet.shosho.tw/deck/events/manage` 確認有沒有建出來，避免重複。

## 排錯

| 症狀 | 原因 |
| --- | --- |
| 「這則活動資訊我沒整理成功」 | LLM 呼叫失敗或沒回 JSON；看 gateway log `event_parse ask failed`。 |
| 卡在「上傳封面」或「建立活動草稿」，訊息有 403 | WP 帳號沒有 Event Manager 角色。 |
| 卡在「合成封面」，`CoverFontError` | VPS 沒裝 fonts-noto-cjk-extra。 |
| 附圖下載不到 | Sanji app 缺 `files:read` 或沒 Reinstall。 |
| 卡在「開 Zoom 會議」 | Zoom S2S 憑證錯誤或過期；token 需要 `meeting:write:meeting:admin`。 |
