# Centaur Zettelkasten — N 系列 Task Prompts（N520–N526）

> P9 六要素格式。每份丟 Claude Code 前置於獨立 sibling worktree（`E:\nakama-N5xx-…`），branch `feat/n5xx-…`。
> 共同上游：`Centaur-Zettelkasten-規格-v0.2.md`（定案）、Literature Note 規格 v0.1、Ingest 流程規格 v0.1、`centaur-kb-prototype-v2.html`（UI 規格附件）、`docs/decisions/ADR-043`（衝突處以 v0.2 為準）。
> 依賴序：**N520 → N521 → N522 → N523 → N524**；N525 / N526 可平行後置。

---

## N520 — 永久層地基（Permanent layer foundations）

1. **目標**：建立 `KB/Permanent/` 的寫入紀律、索引與治理，讓後續任何 task 都踩不過紅線。
2. **範圍**：
   - `shared/kb_writer.py` 或新 module：`update_permanent_bookkeeping()`（白名單 key：`source_refs` / `modified` / `aliases`；拒絕其他 key 與 body 寫入）
   - `shared/kb_indexer.py`：索引 `KB/Permanent/`（含 `支持::/反駁::/延伸::` inline field 抽取）、檢索排序 Permanent 置頂
   - `shared/promotion_*`：斷言 promotion target resolver 不解析 `KB/Permanent/`
   - `tests/`：tripwire 測試（promotion target 永不回 Permanent；bookkeeping 白名單；body 寫入只來自 human 路徑）
   - `docs/VAULT-LAYOUT.md` + LifeOS `CLAUDE.md` 權限表：新增 `KB/Permanent/`（🔒 body human-only）、`KB/Fleeting/`、`KB/Literature/`、`KB/MOCs/`、移除 home.md 計畫；紅線五條（v0.2 §7）寫入
3. **輸入**：v0.2 §3 §7；`agents/robin/kb_search.py`（FTS5 現況）；ADR-042（無 embedding）
4. **輸出**：上述 code + 測試 + 兩份治理文件更新（同 PR）
5. **驗收**：tripwire 全綠；kb_search 對含 typed edge 的樣本卡可檢索且 Permanent 排序最前；`update_permanent_bookkeeping()` 對非白名單 key raise
6. **邊界**：不建任何 UI；不動 Reader / annotation store；不寫 vault 實際內容（測試用 fixture）

## N521 — Literature Note writer 統一

1. **目標**：三路（書/文章/影片）統一 render `KB/Literature/{slug}.md`，退役 notes/digest。
2. **範圍**：新 `shared/literature_writer.py`；`agents/robin/annotation_merger.py`（reflection 改流向）;
   consumer repoint：`agents/brook/context_bridge.py:138-145`、`agents/robin/reading_context_package.py:17-19`、`agents/robin/kb_search.py:178`；停用 `book_notes_writer` / `book_digest_writer`；文章段落錨 `^p-N`（robin reader save 路徑）；影片 `t=` 錨 render（`_format_t_locator` 已存在）
3. **輸入**：Literature 規格 v0.1（frontmatter、三路 body 版型、migration §6）；v0.2 §9（idempotent re-render：保留 `mined_concepts`/`status`/已開卡標記）；`shared/schemas/annotations.py`（V3）
4. **輸出**：writer + migration script（三本既有書從 annotation store 重 render；舊 notes/digest 送回收桶）+ VAULT-LAYOUT 同 PR 更新
5. **驗收**：《卡片盒筆記》50 條全量 render 正確（章分組、cite 錨點、note 原文一字不差）；re-render 兩次 diff 為零且記帳欄保留；consumer 測試綠
6. **邊界**：不動 `KB/Annotations/`（機器檔零改動）；不做 `🔗 KB 相關` 的 LLM-judge（純 FTS5，D-17）；👍/👎 不實作（D-21）

## N522 — 每日回顧 daily job

1. **目標**：scheduled job 產出每日回顧資料（候選卡 + fleeting + 清掃），與 UI 解耦。
2. **範圍**：新 `agents/robin/daily_review.py`（或 gateway cron）：annotation delta 掃描（`created_at` 昨日）、fleeting `status:open` 掃描、候選篩選與排序（有 note 優先、強評價訊號置頂、純 highlight 排除）、AI 建議卡名、Robin judged 相關卡（kb_search + 方向判斷）、「之後再說」14 天過期歸檔、每週清掃模式（stale seedling >30 天、孤兒）、`KB/log.md` append、Nami Slack 通知
3. **輸入**：v0.2 §2 §5；N520 的 indexer；N521 的 Literature/annotation 讀取
4. **輸出**：job + 結構化輸出（JSON 或 md，供 N523 UI 消費）+ schedule 設定
5. **驗收**：以《卡片盒筆記》06-10 真實資料跑出候選清單，置頂含「必須重複三次」「應該要記起來」兩條；過期歸檔邏輯有測試
6. **邊界**：不寫 Permanent；不動 UI；LLM-judge 過濾不做（fast-follow）

## N523 — 每日回顧 Web UI + 開卡 endpoint

1. **目標**：Thousand Sunny 上的每日回顧頁與人工寫卡入口（pilot 的核心人機介面）。
2. **範圍**：`thousand_sunny/routers/kb_review.py`（`GET /kb/review`、`POST /kb/api/permanent` human-authoring endpoint、動作回寫 skip/later）；templates + `static`（照 `centaur-kb-prototype-v2.html` 的每日回顧 view + 開卡 drawer：Robin 建議 chips 兩層選擇器、檔案預覽、空正文阻擋）；存檔後 Phase 5 hook（mined_concepts 回填、fleeting status、index、log）
3. **輸入**：prototype v2（視覺/流程規格）；`docs/design-system.md`（`--sho-*`、LINE Seed TW、asset versioning pattern）；N522 輸出格式；N520 bookkeeping API
4. **輸出**：可用的 `/kb/review` 頁 + endpoint + 測試（含「endpoint 寫入帶 `author: human`」斷言）
5. **驗收**：端到端手測：早上開頁 → 開卡 → 存檔 → vault 出現正確永久卡（frontmatter + typed edges）→ Literature `mined_concepts` 回填 → log 有紀錄；aesthetics 過 design-system 檢查（無 AI slop 清單項）
6. **邊界**：只做每日回顧 view（其餘 view 是 N525）；不做 Obsidian plugin；`POST /kb/api/permanent` 是全系統唯一 Permanent body 寫入口

## N524 — Route C 文章端到端接線

1. **目標**：文章 ingest 全流程接上新件：凍結 → Literature render → wiki 編譯 → 每日回顧。
2. **範圍**：`agents/robin/ingest.py`（IngestPipeline 接 literature_writer）；`shared/promotion_renderer.py` / `kb_writer.py`（紅線⑤：Concept/Output 終端證據 lint）；`KB/Wiki/Outputs/` 復活（write-back 確認式，D-18/D-19）；index/log 寫入鏈
3. **輸入**：Ingest 規格 v0.1 Phase 1–2；v0.2 §2 §7 §8
4. **輸出**：route C 端到端 + 一篇真實文章的 pilot run 紀錄
5. **驗收**：一篇文章從 Reader 劃線 → ingest → Literature + Source + Concept + index/log 全產出且 citation 合規；隔天每日回顧出現其候選
6. **邊界**：書路 Concept（N519）與影片路不動；MOC 不動

## N525 —（後置）Web UI 其餘 view

Permanent browser + 卡片詳情（typed edges / backlinks / provenance / 升級 evergreen）、Literature view、MOC（索引 + 展開 + marker section render + AI 建議新 MOC）、Wiki 唯讀、系統頁。規格 = prototype v2 對應 view。依賴 N523 的 chassis。

## N526 —（後置）Nami fleeting capture

Slack bot 指令/DM → `KB/Fleeting/{timestamp}-{slug}.md`（v0.2 §4 格式）。依賴 gateway 現有 Nami handler。N522 在此之前可先用手建 fleeting 檔測試。
