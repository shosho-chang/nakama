# N521 — Centaur Literature Note writer 統一

> **上游文件（先讀）**：`docs/plans/centaur-zettelkasten/Centaur-Zettelkasten-規格書.html`（Literature Note 規格 v0.1 全文）、`Centaur-Zettelkasten-規格-v0.2.md` §9（re-ingest 語意）。
> **依賴**：N520。**worktree**：`E:\nakama-N521-literature-writer`，branch `feat/n521-literature-writer`。

## 1. 目標

三路（書/文章/影片）統一 render 人讀的 `KB/Literature/{slug}.md`；退役 notes.md / digest.md。

## 2. 範圍

- 新 `shared/literature_writer.py`：從 `KB/Annotations/{slug}.md`（V3 schema）render；frontmatter `type: literature`（v0.1 §4）；body 三路版型（書按章+CFI、文章平鋪+`^p-N`、影片時間軸+講者+`t=` 跳轉連結）
- **idempotent re-render**（v0.2 §9）：保留 `mined_concepts` / `status` 記帳欄與「已開卡」標記，只更新劃線內容區
- `agents/robin/annotation_merger.py`：reflection 改流向 literature writer
- consumer repoint：`agents/brook/context_bridge.py:138-145`、`agents/robin/reading_context_package.py:17-19`、`agents/robin/kb_search.py:178`
- 停用 `agents/robin/book_notes_writer.py`、`book_digest_writer.py`
- 文章段落錨 `^p-N`（robin reader save 路徑補位置錨）
- migration script：財富階梯、發現我的多重職涯組合、讓你的思緒平靜下來安然入睡 三本從 annotation store 重 render；舊 notes/digest 送回收桶（PowerShell，禁 `rm`）
- `docs/VAULT-LAYOUT.md` 同 PR 更新（登記 `KB/Literature/`、移除 notes/digest 列）

## 3. 輸入

Literature 規格 v0.1（雙檔制 D1–D7、migration §6、三路對照 §7）；`shared/schemas/annotations.py`；`thousand_sunny/routers/robin.py` `_format_t_locator`。

## 4. 輸出

writer + migration script + consumer repoint + VAULT-LAYOUT 更新，單一 PR。

## 5. 驗收

- 《卡片盒筆記》50 條全量 render 正確：章分組、cite 錨點、note 原文一字不差
- re-render 兩次 diff 為零，記帳欄保留
- consumer 測試綠（Brook context_bridge、RCP、kb_search 讀新路徑）

## 6. 邊界

`KB/Annotations/` 機器檔零改動；`🔗 KB 相關` 先純 FTS5（D-17，不做 LLM-judge）；👍/👎 不實作（D-21）；ingest 編排不動（N524）。
