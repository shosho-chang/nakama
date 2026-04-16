根據以下臨床指引/治療方案的 Source Summary，判斷哪些概念頁和實體頁值得建立或更新。

## 現有知識庫頁面

{existing_pages}

## Source Summary

{summary}

## 使用者引導方向

{user_guidance}

如果使用者有提供引導方向，請優先考慮並強化該方向相關的概念/實體頁面。

---

## 臨床指引專用篩選標準

{vault_conventions}

### Concept Page 標準
臨床指引中，優先提取以下類型的概念：
- **Protocol 類型概念**：具體的治療/介入方案（如 CBT-I Protocol、Time-Restricted Eating）
- **診斷標準概念**：用於判定適用條件的標準（如 Pittsburgh Sleep Quality Index）
- **機制概念**：方案背後的生理機制，幫助理解「為什麼有效」
- **可跨來源重複出現**：不是此方案獨有的術語
- **數量上限**：每次 ingest 最多 3-5 個 Concept

不要建：此方案的特定步驟編號名稱、過於通用的醫學術語

### Entity Page 標準
- **工具/產品**：方案中使用的特定補充劑、藥物、器材（附帶建議劑量）
- **人物**：方案的原始提出者（若為知名研究者）
- **機構**：發布此指引的機構（若為 WHO/NIH 等級）
- **數量上限**：每次 ingest 最多 1-3 個 Entity

---

## 輸出格式

回傳純 JSON，每個條目都要包含清楚的 `reason`：

```json
{{
  "create": [
    {{
      "title": "頁面標題",
      "type": "concept 或 entity",
      "entity_type": "person / tool / book / company / other（只有 entity 需要填）",
      "reason": "為什麼符合篩選標準",
      "content_notes": "應包含的重點內容（Protocol 概念請包含 step-by-step 摘要）"
    }}
  ],
  "update": [
    {{
      "title": "既有頁面標題",
      "file": "既有檔案的相對路徑",
      "reason": "新來源提供了什麼新資訊",
      "additions": "應新增的具體內容"
    }}
  ]
}}
```
