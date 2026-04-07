你是一位知識庫管理員。根據以下 Source Summary，判斷哪些概念頁（Concept Pages）和實體頁（Entity Pages）值得建立或更新。

## 現有知識庫頁面

{existing_pages}

## Source Summary

{summary}

## 使用者引導方向

{user_guidance}

如果使用者有提供引導方向，請優先考慮並強化該方向相關的概念/實體頁面。

注意：來源文件中可能包含使用者的標記（`==highlight==`）和註解（`> [!annotation]`）。
如果存在這些標記，使用者標記的概念和實體應獲得較高的建立優先級。

---

## 篩選標準（重要）

### Concept Page 標準
只建立符合以下條件的概念頁：
- **可跨來源重複出現**：不是這本書特有的細節，而是在 Health & Wellness / Longevity / Productivity 領域會反覆討論的抽象概念
- **有獨立解釋價值**：值得用一整頁來說明定義、機制、應用
- **不要建**：過於細節的章節主題、單一來源的特殊術語、某書特有的框架名稱（除非已被廣泛引用）
- **數量上限**：每次 ingest 最多建議 3-5 個 Concept

### Entity Page 標準
**人物（person）**：只建立在你的領域有長期參考價值的核心研究者或 KOL，例如：
- 某領域的奠基人或代表性學者
- 你會持續追蹤其研究的人
- **不要建**：書中被引用但與你的長期研究無關的配角研究者

**工具/產品（tool）**：只建實際會用到或值得深入了解的工具

**書籍（book）**：書本身已是 Source，通常不需要另建 Entity

**機構/組織（organization）**：幾乎不建，除非是對你的領域有決定性影響的機構（例如 WHO、NIH 的特定計畫）

**數量上限**：每次 ingest 最多建議 1-3 個 Entity

---

## 輸出格式

用 JSON 回傳，每個條目都要包含清楚的 `reason`（為什麼符合篩選標準）：

```json
{{
  "create": [
    {{
      "title": "頁面標題",
      "type": "concept 或 entity",
      "entity_type": "person / tool / book / company / other（只有 entity 需要填）",
      "reason": "為什麼符合篩選標準、為什麼值得建立",
      "content_notes": "應包含的重點內容"
    }}
  ],
  "update": [
    {{
      "title": "既有頁面標題",
      "file": "既有檔案的相對路徑",
      "reason": "新來源提供了什麼新資訊或不同觀點",
      "additions": "應新增的具體內容"
    }}
  ]
}}
```
