# Robin — 考古學家（Knowledge Base Agent）

自動將放入 Inbox 的文件攝入知識庫，產出結構化的 Wiki 頁面並同步至 Obsidian vault。

**排程：** 每天 02:00  
**狀態：** ✅ 完成

---

## 功能

1. 掃描 `Inbox/kb/` 中的新檔案
2. 依副檔名分類並搬移至 `KB/Raw/`
3. 呼叫 Claude API 產出 Source Summary
4. 識別文件中的概念（Concept）與實體（Entity），建立或更新對應 Wiki 頁面
5. 更新 `KB/index.md` 與 `KB/log.md`
6. 在 SQLite 標記已處理，移除 Inbox 原檔

## 支援的檔案格式

| 副檔名 | 類型 | 存放位置 |
|--------|------|---------|
| `.md` | article | `KB/Raw/Articles/` |
| `.txt` | article | `KB/Raw/Articles/` |
| `.html` | article | `KB/Raw/Articles/` |
| `.pdf` | paper | `KB/Raw/Papers/` |
| `.epub` | book | `KB/Raw/Books/` |

## Vault 輸出

```
KB/
  Raw/
    Articles/   ← 原始文章檔案
    Papers/     ← 原始論文 PDF
    Books/      ← 原始書籍
  Wiki/
    Sources/    ← 每份來源的摘要頁
    Concepts/   ← 概念頁（如：間歇性斷食、端粒）
    Entities/   ← 實體頁（人物、工具、書籍、機構）
  index.md      ← 知識庫索引
  log.md        ← Append-only 操作紀錄
```

## 使用方式

把檔案放入 Obsidian vault 的 `Inbox/kb/`，Robin 會在排程時間自動處理。

手動執行：

```bash
python -m agents.robin
```

## Prompts

| 檔案 | 用途 |
|------|------|
| `prompts/summarize.md` | 產出 Source Summary |
| `prompts/extract_concepts.md` | 識別需建立/更新的 Concept & Entity |
| `prompts/write_concept.md` | 撰寫 Concept 頁內容 |
| `prompts/write_entity.md` | 撰寫 Entity 頁內容 |
