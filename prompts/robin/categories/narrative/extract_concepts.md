根據以下敘事/經驗內容的 Source Summary，判斷哪些概念頁和實體頁值得建立或更新。

## 現有知識庫頁面

{existing_pages}

## Source Summary

{summary}

## 使用者引導方向

{user_guidance}

如果使用者有提供引導方向，請優先考慮並強化該方向相關的概念/實體頁面。

---

## 敘事經驗專用篩選標準

{vault_conventions}

### Concept Page 標準
敘事經驗中，概念提取應非常保守：
- **已在其他來源出現的概念**：此敘事提供了實踐案例，可豐富既有概念頁的「實際經驗」區塊
- **跨來源可重複的介入方式**：作者嘗試的方法如果是知名做法（如 Cold Exposure、Intermittent Fasting），值得建立概念頁
- **不要建**：此人獨有的生活習慣、過於個人化的經驗
- **數量上限**：每次 ingest 最多 3 個 Concept（敘事來源概念提取應最保守）

### Entity Page 標準
敘事來源的實體提取相對較多：
- **人物**：故事的主角本身（若為知名 KOL 或值得長期追蹤的人物）
- **工具/產品**：作者使用且具體描述效果的工具、補充劑、器材
- **數量上限**：每次 ingest 最多 2-3 個 Entity

### 重要提醒
所有從敘事經驗提取的內容，在 `content_notes` 中必須標明來源為「個人經驗報告」，不可呈現為已驗證的事實。

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
      "content_notes": "應包含的重點內容（必須標明來源為個人經驗）"
    }}
  ],
  "update": [
    {{
      "title": "既有頁面標題",
      "file": "既有檔案的相對路徑",
      "reason": "新來源提供了什麼新經驗",
      "additions": "應新增的具體內容（以「某人報告...」格式呈現）"
    }}
  ]
}}
```
