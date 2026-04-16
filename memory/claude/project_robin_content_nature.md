---
name: Robin 內容性質分類系統
description: PR #12 已 merge — 兩層分類架構、6 類別專屬 prompt、領域四大面向、取消按鈕
type: project
originSessionId: 8bece3a7-26ae-4215-bade-04d2bca1809b
---
**PR #12 已 merge（2026-04-16）**

兩層分類架構：
- Layer 1: `source_type`（媒體格式）→ 決定存放位置（不改）
- Layer 2: `content_nature`（內容性質）→ 決定 ingest prompt

6 個類別：
- `research`（研究文獻）— IMRAD 結構、信心基於研究設計
- `popular_science`（科普讀物）— default，沿用原 prompt
- `textbook`（教科書）— 概念層次、定義、參考值
- `clinical_protocol`（臨床指引）— step-by-step、劑量、禁忌
- `narrative`（敘事經驗）— 時間線、N=1 注意事項
- `commentary`（評論觀點）— 發言者立場、信號追蹤

**Why:** 修修的 KB 服務不同類型文件（論文 vs 教科書 vs 回憶錄），同一套 prompt 無法適配。
**How to apply:** 使用者在 Web UI 選 content_nature dropdown，prompt_loader 自動路由到 `prompts/robin/categories/{nature}/`。

領域四大面向：睡眠、飲食、運動、情緒。
領域範圍：身心健康 + 運動科學 + 營養科學 + 腦神經科學 + 長壽科學。

額外功能：Web UI processing 頁有取消按鈕（cancel endpoint + 回收桶刪除）。

待做 E2E 測試：Gemma 4 已裝好，下次測大文件 ingest + content_nature 驗證。
