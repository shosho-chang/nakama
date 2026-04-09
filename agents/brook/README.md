# Brook — 音樂家（Composer Agent）

將現有素材（文章草稿、影片腳本、逐字稿）Compose 成不同平台所需的格式，再交由 Usopp 發布。

**排程：** 手動觸發  
**狀態：** 🚧 待開發

---

## 功能

輸入原始素材，依平台需求輸出格式化內容：

| 輸入 | 輸出格式 |
|------|---------|
| 文章草稿 | WordPress 部落格文章（SEO 優化） |
| 影片腳本 | YouTube 描述、章節標記、標籤 |
| 長文內容 | Facebook / LinkedIn 貼文 |
| 長文內容 | Instagram Carousel 腳本（逐張文字） |
| 任何內容 | 電子報摘要（交給 Usopp 發送） |

套用 `config/style-profile.json` 的 Owner 寫作風格。

## 工作流程

```
Owner 提供素材
    ↓
Brook 選擇對應格式 Compose
    ↓
輸出各平台版本（存入指定路徑）
    ↓
通知 Usopp 待發布，等待 Owner 核准
```

## 設定

```json
// config/style-profile.json（待建立）
{
  "tone": "...",
  "vocabulary": "...",
  "cta_style": "..."
}
```

## 執行

```bash
python -m agents.brook
```
