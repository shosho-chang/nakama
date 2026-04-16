根據以下教科書/參考書的 Source Summary，判斷哪些概念頁和實體頁值得建立或更新。

## 現有知識庫頁面

{existing_pages}

## Source Summary

{summary}

## 使用者引導方向

{user_guidance}

如果使用者有提供引導方向，請優先考慮並強化該方向相關的概念/實體頁面。

---

## 教科書專用篩選標準

{vault_conventions}

### Concept Page 標準
教科書是概念密度最高的來源類型，但要精選：
- **帶 canonical definition 的核心概念**：教科書中有明確定義的重要術語，概念頁應包含「教科書定義」區塊
- **概念層次的關鍵節點**：在 parent-child 結構中屬於重要分支點的概念
- **有參考值的概念**：附帶正常範圍、閾值等具體數值的概念（如 VO2max、BMI、GFR）
- **可跨來源重複出現**：不是此教科書特有的章節編排
- **數量上限**：每次 ingest 最多 3-5 個 Concept

不要建：教科書的章節名稱本身、過於基礎的定義（如「蛋白質」）、過於細節的子分類

### Entity Page 標準
教科書中的實體通常較少需要獨立頁面：
- **工具/測量儀器**：教科書推薦的標準化測量工具或量表
- **人物**：幾乎不建 — 教科書引用大量研究者，只建該領域的奠基人
- **數量上限**：每次 ingest 最多 1-2 個 Entity

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
      "content_notes": "應包含的重點內容（教科書概念請包含 canonical definition）"
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
