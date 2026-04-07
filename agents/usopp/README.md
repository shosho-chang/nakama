# Usopp — 狙擊手（Community Monitor Agent）

監控 WordPress Fluent Community 的社群狀態，偵測未回覆的貼文並發出提醒。

**排程：** 每小時  
**狀態：** 🚧 待開發

---

## 計畫功能

- 定期呼叫 WordPress REST API 掃描社群貼文
- 偵測超過 24 小時未獲回覆的問題
- 發送 Email 通知 Owner

## 設定

```yaml
# config.yaml
agents:
  usopp:
    wordpress:
      alert_threshold:
        unanswered_hours: 24
```

WordPress base URL 與帳號設定於 `.env`：

```
WP_BASE_URL=https://your-site.com/wp-json
WP_USER=admin
WP_APP_PASSWORD=xxxx-xxxx-xxxx-xxxx
```
