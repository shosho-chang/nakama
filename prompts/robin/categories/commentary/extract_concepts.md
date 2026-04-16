根據以下評論/觀點內容的 Source Summary，判斷哪些概念頁和實體頁值得建立或更新。

## 現有知識庫頁面

{existing_pages}

## Source Summary

{summary}

## 使用者引導方向

{user_guidance}

如果使用者有提供引導方向，請優先考慮並強化該方向相關的概念/實體頁面。

---

## 評論觀點專用篩選標準

{vault_conventions}

### Concept Page 標準
評論/觀點中，概念提取應保守：
- **被多位專家討論的概念**：此評論中提到的概念如果是業界熱門議題，值得建立
- **新興辯論的焦點概念**：尚未在 KB 中出現但正在形成共識的概念
- **不要建**：發言者的個人創造詞彙、只在此文/訪談中使用的特殊術語
- **數量上限**：每次 ingest 最多 3 個 Concept

### Entity Page 標準
評論/觀點來源的實體提取偏重人物：
- **人物（KOL）**：受訪者、發言者，若為值得長期追蹤的意見領袖
- **工具/產品**：被推薦或評測的具體產品（需標明為推薦/觀點，非驗證）
- **數量上限**：每次 ingest 最多 2-3 個 Entity

### 重要提醒
所有從評論/觀點提取的內容，在 `content_notes` 中必須標明來源性質（expert opinion / influencer take / journalist summary），不可呈現為已驗證的事實。

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
      "content_notes": "應包含的重點內容（必須標明來源為觀點/評論）"
    }}
  ],
  "update": [
    {{
      "title": "既有頁面標題",
      "file": "既有檔案的相對路徑",
      "reason": "新來源提供了什麼新觀點",
      "additions": "應新增的具體內容（以「某人認為...」格式呈現）"
    }}
  ]
}}
```
