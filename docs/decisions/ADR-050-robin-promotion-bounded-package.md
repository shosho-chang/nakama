# ADR-050：Robin Source Promotion bounded package + composition root

- **狀態**：Accepted（2026-07-03）
- **相關**：ADR-024（Source Promotion）、ADR-034（promotion polymorphism / `promotion_targets.py`）、ADR-043（Centaur permanent-layer tripwire）、ADR-045（Robin KB role）、`agents/robin/CONTEXT.md`（ownership boundary）
- **範圍**：module 佈局 + 組裝責任歸屬；**不**改變任何 promotion 行為 / schema / HTTP 介面
- **PR**：本 PR（單刀，`git mv` 保留歷史）

## Context

2026-07-03 架構審計（improve-codebase-architecture skill，5 個 Explore agent 交叉驗證）發現兩個互相加乘的摩擦：

1. **`shared/` 平面命名空間被單一 agent 的 domain logic 佔據。** CONTEXT-MAP 定義 shared kernel 為「任何 agent 必經介面」，但 ~148 個頂層模組中有 19 個是 Robin Source Promotion 專屬（`promotion_*` ×7、concept/entity engines、dry-run fixtures、KB indexes、source map builder、resolver、video 兩支），非測試 caller 只有 `thousand_sunny/promotion_wiring.py` 一處。**Deletion test**：把它們從 `shared/` 拿掉，複雜度不會散落 N 個 caller，只會整批集中到一個 Robin package — 證明它們沒在 `shared/` 賺到位置。平面 148 模組也直接傷 AI-navigability。

2. **Wiring ceiling：組裝知識漏進 presentation layer。** `PromotionReviewService` 建構需要 11–17 個 collaborator，~120 行 env → adapter → service 的組裝邏輯住在 `thousand_sunny/promotion_wiring.py`。service 的有效 interface（呼叫者必須知道的一切）因此比 implementation 還寬。Robin CONTEXT.md 明文要求「CLI and future agents must be able to reuse the same Robin/shared service」，但重用 = 複製整段 wiring 或 import web 層。

前置事實：`thousand_sunny` 已有 13 處 import `agents.*`（robin/zoro/brook/foundry），`promotion_wiring` 自己就 import `agents.robin.source_map_extractor` — presentation → agents 的依賴方向早已存在，本 ADR 不引入新方向。

## Decision

- **D1 — Bounded package**：19 個 promotion 模組 `git mv` 至 `agents/robin/promotion/`（模組名不變）。對應測試移至 `tests/agents/robin/promotion/`。
- **D2 — Composition root**：新增 `agents/robin/promotion/factory.py` — `load_promotion_config()`（唯一 env 讀取點，承襲 W6 boundary）＋ `build_promotion_review_service(config)`（mode 分支 + collaborator graph）。`thousand_sunny/promotion_wiring.py` 縮成 thin shim（載 config → factory → `set_service`），公開名 `PromotionWiringConfig` / `load_promotion_wiring_config` 保留為別名，`app.py` lifespan 一行不改。
- **D3 — `shared/` 邊界規則**：`shared/` 只收 **2+ agent 共用**的基礎設施。單一 agent 的 domain logic 住該 agent 的 package。跨層 **contract types**（`shared/schemas/`）是 interface 層，留 kernel — presentation 渲染要用、domain 實作要用，types 即 seam。
- **D4 — EPUB helper 歸家**：`source_map_builder` 內被 `shared/literature_writer` 借用的兩個私有 EPUB helper（`_build_toc_title_map` / `_extract_epub_spine_items`）升級為 `shared/epub_metadata.py` 的公開函式（`build_toc_title_map` / `extract_spine_items`），消除搬移後會出現的 shared→agents 反向依賴，順帶收斂一份 OPF 解析重複。
- **D5 — Subprocess gate 語意更新**：T12/WT9 類 import-隔離 gate 原本禁 `agents.*`（守「shared 不得 import agents」）。搬家後模組本身就在 `agents.*`，gate 改為：禁 fastapi / thousand_sunny / LLM clients / **自身 package 鏈以外的任何 agents 模組**（`agents.robin.agent`、`agents.robin.ingest` 等重量級機器仍被擋）。`shared/` 模組（如 `blob_loader`）的 gate 維持全面禁 agents。

### 刻意不搬的（本刀 scope 之外）

- `reading_source_registry` / `reading_source_lister`：Reader domain（robin reader router 也用），不是 promotion 專屬 — 未來若立 `agents/robin/reading/` 再議。
- `concept_canonicalize`：Robin-wide（ingest + kb_writer 用），非 promotion 專屬。
- `writing_assist_surface`：Stage 4 boundary object，自成一格。
- `permanent_layer`：跨領域 tripwire（`output_writer`、kb_review router 都用），是 kernel。
- `shared/schemas/`：見 D3。

## Consequences

**正面**
- **Leverage**：任何 caller 一行拿到組裝好的 service（`build_promotion_review_service(load_promotion_config())`），CLI / 未來 agent 不再複製 wiring。
- **Locality**：Robin promotion 的變更、bug、知識集中在一個目錄；`shared/` 往「~50 個真基礎設施模組」收斂的第一步。
- **Test surface 對齊 interface**：integration test 與 production 走同一個 factory；wt6/wt6b 的 patch 目標跟著 seam 移到 factory。

**負面 / 代價**
- 一次性 import churn：54 個檔案改寫（多為機械替換）；`git mv` 保留 blame 歷史。
- 歷史 ADR / research docs 內的舊路徑**不改寫**（它們是紀錄不是活文件）；活文件（VAULT-LAYOUT、CONTEXT-MAP、robin CONTEXT.md、centaur 靜態頁）已同步。

## Follow-ups（審計遺留，另開任務）

- `concept_dispatch.py` / `concept_schema.py`：零非測試 caller 的孤兒模組 — 刪除候選（deletion test：複雜度直接消失）。
- 後續搬移循 D3 規則逐案評估（`kb_writer` 等 Robin-wide 模組的家在 `agents/robin/`，但不在 `promotion/`）。
