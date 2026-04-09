# Usopp — 狙擊手（Publisher Agent）

精準將內容發布到最對的地方，同時負責電子報發送管理（Fluent CRM）。

**排程：** 手動觸發（發布）；電子報依排程  
**狀態：** 🚧 待開發

---

## 功能

### 1. 內容精準發布
接收 Brook（Composer）產出的格式化內容，發布至對應平台：
- **WordPress 部落格**：文章發布（含圖片、標籤、分類、SEO meta）
- **YouTube**：影片上傳（含縮圖、描述、標籤、章節）
- **社群媒體**：Facebook / Instagram / LinkedIn 貼文排程
- 發布前需 Owner 明確核准，不自動發布

### 2. 電子報管理（Fluent CRM）
- 電子報名稱：**張秀秀的自由之路**
- 依排程發送電子報至訂閱名單
- 追蹤發送結果（open rate、click rate）並回報給 Nami

## 設定

WordPress / YouTube 帳號設定於 `.env`：

```
WP_BASE_URL=https://your-site.com/wp-json
WP_USER=admin
WP_APP_PASSWORD=xxxx-xxxx-xxxx-xxxx
YOUTUBE_CLIENT_ID=
YOUTUBE_CLIENT_SECRET=
YOUTUBE_REFRESH_TOKEN=
FLUENT_CRM_API_KEY=
```

## 執行

```bash
python -m agents.usopp
```
