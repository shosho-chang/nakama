# N524 — Centaur route C 文章端到端接線

> **上游文件（先讀）**：`docs/plans/centaur-zettelkasten/Centaur-Zettelkasten-規格書.html`（Ingest 流程 v0.1 Phase 1–2）、`Centaur-Zettelkasten-規格-v0.2.md` §2 §7 §8、`Centaur-Zettelkasten-Prompt規格-v0.1.md`（P-3、P-4、P-5）。
> **依賴**：N521、N522。**worktree**：`E:\nakama-N524-route-c-ingest`，branch `feat/n524-route-c-ingest`。

## 1. 目標

文章（route C）ingest 全流程接上新件：凍結 → Literature render → wiki 編譯 → 隔日每日回顧。

## 2. 範圍

- `agents/robin/ingest.py`：`IngestPipeline` 接 `literature_writer`（Phase 1 凍結 + render）
- Phase 2 chain（順序鎖 Sources → Entities → Concepts → Index/Log）：
  - **P-3** Source digest（`promotion_renderer` / `kb_writer.write_source_page` 路徑）
  - **P-4** Entities upsert
  - **P-5** Concept 編譯 — 改造既有 `upsert_concept_page` 的 Opus diff-merge prompt（整合非新增、矛盾標記、Permanent defer 標記）
  - 全部掛 Prompt 規格 §1 共同 system 前置（防注入、紅線、語言）
- **紅線⑤ enforcement**：Concept/Output 寫入時 citation lint — terminal cite 指向 Concept/Output 即 reject
- `KB/Wiki/Outputs/` 復活（D-19）：建目錄 + VAULT-LAYOUT 登記；write-back 確認式（D-18）的寫入函式（query workflow 本身另開 task，這裡只鋪儲存層）
- index / log 寫入鏈

## 3. 輸入

Ingest v0.1 Phase 1–2 + 真實 code 接點（§13）；v0.2 §2 §7 §8；Prompt 規格 P-3/P-4/P-5。

## 4. 輸出

route C 端到端 + 一篇真實文章的 pilot run 紀錄（進 `docs/plans/centaur-zettelkasten/pilot-run-001.md`）。

## 5. 驗收

- 一篇文章：Reader 劃線 → ingest → `KB/Literature/` + `Wiki/Sources/` + `Wiki/Concepts/`（或 merge 既有）+ index/log 全產出
- 每個事實宣稱有錨點；citation lint 對違規 fixture 正確 reject
- 隔日 N522 job 把該文章的候選帶進每日回顧

## 6. 邊界

書路 Concept 抽取不動（卡 N519，等它 merge）；影片路不動；MOC 不動（紅線④：ingest 不建 MOC）；P-6/P-7 lint 排程另開 task。
