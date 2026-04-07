# Brook — 音樂家（Publish Agent）

在 Owner 核准後，自動將內容發布至 WordPress 並上傳至 YouTube。

**排程：** Owner 核准後觸發  
**狀態：** 🚧 待開發

---

## 計畫功能

- 將 Franky 產出的文章發布至 WordPress（含圖片、標籤、分類）
- 將影片上傳至 YouTube（含縮圖、描述、標籤）
- 發布前等待 Owner 明確核准，不自動發布

## 設定

WordPress 帳號設定於 `.env`：

```
WP_BASE_URL=https://your-site.com/wp-json
WP_USER=admin
WP_APP_PASSWORD=xxxx-xxxx-xxxx-xxxx
```

YouTube OAuth 設定於 `.env`：

```
YOUTUBE_CLIENT_ID=
YOUTUBE_CLIENT_SECRET=
YOUTUBE_REFRESH_TOKEN=
```

## 執行

```bash
python -m agents.brook
```
