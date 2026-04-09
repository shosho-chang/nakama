# Nami — 航海士（Secretary Agent）

整合各 agent 當日產出，每天早上產出 Morning Brief；負責頻道數據追蹤與邀約報價回覆。

**排程：** 每天 07:00（Morning Brief）；數據追蹤每週一次  
**狀態：** 🚧 待開發

---

## 功能

### 1. Morning Brief
- 讀取 Robin、Zoro、Usopp、Sanji 的當日執行結果
- 彙整重點資訊，產出 Morning Brief（Markdown 格式）
- 寫入 `AgentBriefs/YYYY-MM-DD.md`

### 2. 數據追蹤（每週）
追蹤並彙整各平台關鍵指標：
- **電子報**（Fluent CRM）：訂閱人數、open rate、click rate、取消訂閱率
- **YouTube**：訂閱人數、近期影片觀看數、平均觀看時長
- **Podcast**：下載量、訂閱數
- **自由艦隊**（Fluent Community）：付費會員數、月付/季付/年付分布、流失率

每週一彙整成數據摘要，附入 Morning Brief。

### 3. 邀約報價
- 使用者提供邀約訊息 → Nami 根據當前頻道數據與定價表，產出報價回覆草稿
- 定價表由使用者維護於 `config/nami-pricing.yaml`

## 設定

```yaml
# config/nami-pricing.yaml（待建立）
sponsored_video:
  base_price: ...
  per_1k_subs: ...
podcast_mention:
  ...
```

## 輸出

- `AgentBriefs/YYYY-MM-DD.md` — 每日 Morning Brief
