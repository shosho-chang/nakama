# Zoro — 劍士（Scout Agent）

追蹤 KOL 動態、PubMed 最新研究、Google Trends 關鍵字，每日產出情報報告。

**排程：** 每天 06:00  
**狀態：** 🚧 待開發

---

## 計畫功能

- 追蹤 Twitter/X 上的健康與長壽 KOL（設定於 `config/kol-twitter.yaml`）
- 訂閱 PubMed RSS，過濾與 longevity / healthspan 相關的新論文
- 監控 Google Trends 關鍵字（longevity、biohacking、身心健康、healthspan）
- 將新情報寫入 `KB/Wiki/` 並標記為待 Robin 深度處理

## 設定

```yaml
# config.yaml
agents:
  zoro:
    sources:
      twitter:
        kol_list: config/kol-twitter.yaml
      pubmed:
        rss_feeds: config/pubmed-feeds.yaml
      google_trends:
        keywords: [longevity, biohacking, 身心健康, healthspan]
```
