# P9 Task Prompt — Release Amendment authority（ADR-066 follow-up）

- **日期**：2026-08-29
- **模式**：P9（多模組，8+ 檔案）
- **前置**：`3ab01f75` amendment journal 已落地（B）；本文件是 A
- **ADR**：ADR-066 §Open follow-up — Release Amendment authority

---

## 1. 目標

讓「已封存 Release 的機械式、非語意修訂」成為 `FinishedCutProduction` 的一等公民命令，
使 current Release 能**從 tracked 狀態經由 module 重新推導**，而不再依賴 `amendments/operations/`
底下寫死 episode 常數、且伸手進 package 私有層的一次性腳本。

---

## 2. 範圍

**必改**（皆位於 `agents/brook/script_video/finished_cut_production/`）：

| 檔案 | 改什麼 |
|---|---|
| `_commands.py` | 新增 `AmendmentCommand` dataclass（比照 `TargetedRevisionCommand:31`）；`_COMMAND_ID_RE:10` 目前只收 `approved-cut\|targeted-revision`，要加 `amendment` |
| `_records.py` | 新增機械變換的型別化詞彙與 `_mint_amendment_plan`（包住既有 `_mint_materialization_plan:320`，強制沿用 base 的三個 acceptance id） |
| `_store.py` | `payload` 新增 `amendments` 區塊；`save_amendment` / `load_amendment` 比照 `save_targeted_revision:349`。**注意 `_read_payload:378` 是 `set(payload) != {"schema","runs","targeted_revisions"}` 的嚴格全等檢查**——見 §5 遷移 |
| `_engine.py` | 新增 public `request_amendment`（比照 `request_revision:359`）；`_advance_locked:201` 加入 amendment 分支（**不派工 semantic worker**） |
| `_composition.py` | composition root 接上新路徑 |
| `amendments/_journal.py` | 改為 aggregate 可寫；schema 維持 v1 |
| `__init__.py` | 匯出新增的 public 型別 |
| `scripts/run_finished_cut_production.py` | 新增 `request-amendment` 子命令（維持只做反序列化＋委派） |

**必改（package 外）**：
- `docs/decisions/ADR-066-…md` — 把 §Open follow-up 從「Still owed」改為已實作
- `agents/brook/script_video/CONTEXT.md` — 新增 **Release Amendment** 詞彙條目
- `agents/brook/script_video/finished_cut_production/amendments/README.md` — 改寫為 journal 說明
- 測試：新增 `tests/brook/script_video/test_finished_cut_amendment.py`；擴充
  `test_finished_cut_store.py`、`test_finished_cut_cli.py`

**明確不在範圍**：Bridge router / watcher / UI；`_projection.py` 詞彙；legacy DAG 刪除。

---

## 3. 輸入

- **ADR-066** §Public module responsibilities、§Staged candidate and Finished Cut Release
- **既有 journal**：`amendments/journal/20260805-lin-zhi-chen.json`——它就是本命令必須接受的
  資料形狀，也是驗收基準
- **參考實作**：`amendments/operations/*.py`。**只讀不抄**：它們的驗證順序（exact current 三重驗證 →
  確定性 plan_id → duplicate-swap → work timeline 逐 track/frame/digest 比對 → pointer 前後各驗一次 →
  compensating rollback）是正確的，要保留；它們伸手進私有層與寫死常數的部分要丟掉
- **live authority store**：`G:\Footages\20260805 林之晨\highlights\finished-cut-production-v1\runtime\episodes\<ep>\runs\authority.json`（schema `nakama.finished-cut-production-store.v1`，鍵為 `runs` / `targeted_revisions`）
- **既有不變式**：`_projection.py` 的 `_ACTIVE_PROJECTION_COMBINATIONS`；`intentional_aroll` 已是
  active projection（`_event_has_active_projection:57`），因此 L04 current Release 可被命令定址

---

## 4. 輸出

1. **`AmendmentCommand`** — 只帶 `command_id` / `current_release_id` / `episode_id` / `cut_id` /
   `format` / 型別化 `operation`。不得帶 stage rows、recipe、檔案路徑、acceptance id。
2. **機械變換詞彙**（封閉集合，兩種）：
   - `suppress_components(target_event_ids)` → 目標事件轉 `intentional_aroll`，其 component 移除
   - `replace_component_assets(component_id → asset_ref)` → 僅換 Active Asset Store 參照
3. **`request_amendment(current_release_ref, operation) -> str`** — 鑄造 `amendment:<uuid4 hex>`，
   驗證 exact current、事件存在、operation 合法後寫入 store。
4. **`advance(amendment_id)`** — 從 base Release 推導 plan（沿用其三個 acceptance id）→
   Resolve duplicate-swap → Candidate → commit → seal → pointer-last。全程零 semantic dispatch。
5. **store schema v2** + 既有 v1 authority.json 的前向遷移。
6. **journal 由 aggregate 寫出**，取代人工產生。
7. **CLI** `request-amendment` 子命令。

---

## 5. 驗收

**主驗收——plan 相等，不是 preview 位元相等**：

以 journal 記錄的 L04 兩次修訂為 fixture，透過新命令重跑，產出的 `MaterializationPlan` 必須與
sealed Release 記錄的 `plan-suppression-e8080c9bbb1bcbbd4624792e`、
`plan-transition-v4-833e4ac1dd54c65c9ffa8165` **完全一致**（含 plan_id）。

> **不要**把 preview 位元相等當驗收標準。1 GiB 影片經 HyperFrames + ffmpeg 渲染，字型、
> codec、驅動任一飄移就對不上，那不是可重現性契約。

**其餘驗收**：

- `request_amendment` 對以下一律 fail closed：非 exact current 的 release ref、不存在的 event、
  未知 operation kind、會改變 event 數量的 operation、會動到 acceptance chain 的 operation、
  結果違反 `_ACTIVE_PROJECTION_COMBINATIONS` 的 operation
- `advance(amendment_id)` 全程零 `semantic_adapter.dispatch` 呼叫（用 spy 斷言）
- 產出的 Release 三個 acceptance id 與 base 逐字相同
- commit 後可 compensating rollback；pointer 在 commit 前後各驗一次
- **store 遷移**：既有 v1 authority.json（3 個鍵）能被 v2 讀取並升級；升級為原子寫入且可重入
- 既有 `test_finished_cut_*` 全數維持現況（**目前基線：518 passed / 23 failed，23 個全部是
  `supporting_title` stale fixture**，不得新增失敗）
- `amendments/operations/` 於本工作完成時刪除，journal 測試改為驗 aggregate 產出

---

## 6. 邊界

**絕對不可**：

- 在 amendment 路徑上鑄造任何新的 `AcceptedStage` 或 acceptance id
- 在 amendment 路徑上呼叫 semantic worker / Director / DP
- 擴充 `_projection.py` 的 active 詞彙（尤其**不得**復活 `supporting_title`）
- 在 canonical Resolve timeline 原地編輯——一律 duplicate/apply/preview/commit/pointer-last
- 讓 `operation` 攜帶檔案路徑、recipe、絕對路徑或 stage payload
- **在未取得明確授權下對 live L04 current Release 執行 `publish`**。驗證一律在還原出來的
  staging 副本上跑；current pointer 的任何變更是獨立、需授權的動作
- 動 Bridge router / watcher / UI（另案）
- 刪 G: 上任何歷史資料

**互動邊界**：實作過程若發現 journal 記錄的 plan 與新命令推導結果不一致，**停下來回報**，
不要調整 journal 去迎合程式——journal 描述的是已封存、不可變的 Release。

---

## 已知會咬人的地方

1. **`_store.py:378` 的嚴格全等鍵檢查**是最容易被低估的一項。加 `amendments` 鍵不是加一行，
   要連帶處理 schema 版本、live 檔案遷移、以及舊版程式讀到新檔會硬失敗的問題。
2. **`_advance_locked:201` 開頭就 `load_run(command_id)`**，amendment 沒有 run 概念，分支要
   在那之前，否則會走進 targeted-revision 的 base 解析邏輯。
3. `amendments/operations/*.py` 的 `_verify_work_timeline` 用了
   `transactions._adapter` / `adapter._facade`。新實作需要 module 內合法的等價驗證路徑，
   這可能需要在 `_resolve*.py` 補一個公開的 work-timeline 檢查點。
