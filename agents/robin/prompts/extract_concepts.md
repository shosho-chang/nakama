你是一位知識庫管理員。根據以下 Source Summary，判斷哪些概念頁（Concept Pages）和實體頁（Entity Pages）需要建立或更新。

## 現有知識庫頁面

{existing_pages}

## Source Summary

{summary}

## 任務

1. 列出需要**新建**的概念頁和實體頁（知識庫中尚不存在的）
2. 列出需要**更新**的既有頁面（新來源提供了新資訊或不同觀點）
3. 對於每個需要新建/更新的頁面，說明原因和應加入的內容

## 輸出格式

用 JSON 回傳：

```json
{
  "create": [
    {
      "title": "頁面標題",
      "type": "concept 或 entity",
      "reason": "為什麼需要建立",
      "content_notes": "應包含的重點"
    }
  ],
  "update": [
    {
      "title": "既有頁面標題",
      "file": "既有檔案的相對路徑",
      "reason": "為什麼需要更新",
      "additions": "應新增的內容"
    }
  ]
}
```
