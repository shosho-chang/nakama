根據以下研究文獻的 Source Summary，判斷哪些概念頁和實體頁值得建立或更新。

## 現有知識庫頁面

{existing_pages}

## Source Summary

{summary}

## 使用者引導方向

{user_guidance}

如果使用者有提供引導方向，請優先考慮並強化該方向相關的概念/實體頁面。

---

## 研究文獻專用篩選標準

{vault_conventions}

### Concept Page 標準
研究文獻中，優先提取以下類型的概念：
- **生物機制**：研究揭示或驗證的作用機制（如 autophagy、mTOR pathway）
- **生物標記**：被測量的指標（如 HbA1c、VO2max、telomere length）
- **方法論概念**：重要的研究方法（如 randomized controlled trial、dose-response relationship）
- **可跨來源重複出現**的抽象概念，不是此研究特有的
- **數量上限**：每次 ingest 最多 3-5 個 Concept

不要建：此研究的特定實驗條件名稱、某研究團隊自創的縮寫

### Entity Page 標準
- **人物**：只建立該領域的核心研究者（通訊作者且有大量相關研究），不建配角
- **工具/測量儀器**：研究中使用的重要測量工具或介入工具（如特定的 wearable device）
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
      "content_notes": "應包含的重點內容"
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
