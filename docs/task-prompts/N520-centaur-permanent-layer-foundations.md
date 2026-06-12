# N520 — Centaur 永久層地基（Permanent layer foundations）

> **上游文件（先讀）**：`docs/plans/centaur-zettelkasten/Centaur-Zettelkasten-規格-v0.2.md`（§3 §7 為本任務核心）、`SESSION-HANDOFF-Centaur-Zettelkasten.md`（背景）、`docs/decisions/ADR-043`（衝突處以 v0.2 為準）。
> **依賴**：無（N 系列第一棒）。**worktree**：`E:\nakama-N520-permanent-foundations`，branch `feat/n520-permanent-foundations`。

## 1. 目標

建立 `KB/Permanent/` 的寫入紀律、索引與治理，讓後續任何 task 都踩不過紅線。

## 2. 範圍

- `shared/kb_writer.py`（或新 module）：`update_permanent_bookkeeping()` — 白名單 key（`source_refs` / `modified` / `aliases`），其餘 key 與 body 寫入一律 raise
- `shared/kb_indexer.py`：索引 `KB/Permanent/`，抽取 `支持::` / `反駁::` / `延伸::` Dataview inline field；檢索排序 Permanent 置頂
- `shared/promotion_*`：斷言 promotion target resolver 永不解析 `KB/Permanent/`
- `tests/`：tripwire（promotion target 永不回 Permanent；bookkeeping 白名單；Permanent body 寫入只來自 human 路徑）
- `docs/VAULT-LAYOUT.md` + LifeOS `CLAUDE.md` 權限表（同 PR）：新增 `KB/Permanent/`（🔒 body human-only）、`KB/Fleeting/`（人+Nami 寫）、`KB/Literature/`（🤖 render）、`KB/MOCs/`（🟡 marker convention）；寫入 v0.2 §7 五條演算法紅線

## 3. 輸入

v0.2 §3（frontmatter / typed edges / status 兩級）、§7（紅線五條）；`agents/robin/kb_search.py`（FTS5 現況）；ADR-042（無 embedding 前提）。

## 4. 輸出

上述 code + tripwire 測試 + 兩份治理文件更新，單一 PR。

## 5. 驗收

- tripwire 全綠
- 含 typed edge 的樣本卡可被 kb_search 檢索且 Permanent 排序最前
- `update_permanent_bookkeeping()` 對非白名單 key raise
- VAULT-LAYOUT 與 LifeOS CLAUDE.md 權限表一致

## 6. 邊界

不建任何 UI；不動 Reader / annotation store；不寫 vault 實際內容（測試用 fixture）；不實作 Fleeting / Literature writer（N521/N526）。
