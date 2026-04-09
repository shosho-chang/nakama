# Sanji — 廚師（Community Manager Agent）

負責「自由艦隊」訂閱制社群（Fluent Community）的日常營運、社群監控與成員互動管理。

**排程：** 監控每小時；社群活動策劃手動觸發  
**狀態：** 🚧 待開發

---

## 功能

### 1. 社群監控
- 定期呼叫 WordPress REST API 掃描 Fluent Community 貼文
- 偵測超過 24 小時未獲回覆的問題，發送 Email 提醒 Owner
- 偵測異常互動（爭議貼文、垃圾訊息等）

### 2. 社群管理
- 追蹤成員活躍度（新加入、活躍 / 沉默成員）
- 固定活動提醒（讀書會、直播、習慣挑戰）
- 協助媒合志同道合成員（配對學習/行動夥伴）

### 3. 社群活動策劃（手動觸發）
- 根據社群回饋，建議下一季 90 天習慣挑戰主題
- 直播主題建議（依成員票選與近期趨勢）
- 讀書會選書建議

## 社群資訊

- **平台**：Fluent Community（WordPress plugin）
- **社群名稱**：自由艦隊 Freedom Fleet
- **訂閱方案**：月付 USD39 / 季付 USD99 / 年付 USD365
- **核心服務**：分主題論壇、頂級醫學期刊月報、直播、讀書會、90天習慣挑戰

## 設定

```yaml
# config.yaml
agents:
  sanji:
    wordpress:
      alert_threshold:
        unanswered_hours: 24
```

WordPress 帳號設定於 `.env`：

```
WP_BASE_URL=https://your-site.com/wp-json
WP_USER=admin
WP_APP_PASSWORD=xxxx-xxxx-xxxx-xxxx
```

## 執行

```bash
python -m agents.sanji
```
