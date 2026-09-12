# ADR-069: Finished Cut Production 回到 ADR-066 自己說的 inexpensive structural gates

- **Status**: Proposed — 等 owner 簽核；簽核後才動 code
- **Date**: 2026-09-12
- **Owner**: Brook / Podcast Stage 5
- **Stage**: 5 Multi-channel Production
- **Amends**: ADR-066 的**實作範圍**（決策本身不變：一個深模組、一條 AcceptedStage 權威鏈、Long/Short 分家）
- **Preserves**: ADR-064 Editorial Master 真相根；ADR-051 Director 創意所有權；ADR-067 長短分家
- **Invokes**: `memory/claude/feedback_codebase_minimalism_first_principle`（2026-05-17 owner 裁決：程式庫精簡是最高指導原則，預設動作是砍）

## 一句話

ADR-066 的〈Validation profile〉寫著 *"Normal production keeps only **inexpensive structural gates**"*。實作跑出來是 **42,000 行、896 個 `raise`、41 種錯誤碼、77 份白名單**。本 ADR 把實作拉回它自己的授權範圍：**留三件真的救不回來的事，其餘砍掉或換成便宜的形式。**

## Context — 證據，不是感覺

### 規模

| 量測 | 數字 |
|---|---|
| `agents/brook/script_video/finished_cut_production/` | 40 個檔、19,701 行 |
| 對應測試 | 38 個檔、22,117 行 |
| `raise` 語句 | 896 |
| 錯誤碼（`reason_code`） | 41 種 |
| 封閉集合（`frozenset` / `Literal` 白名單） | 77 個，散在 25 個檔 |
| `hero_title` 這一個詞被獨立宣告的地方 | **12 個檔、27 處** |
| 一次性遷移碼仍在產線裡（`_cutover`、`_neutral_asset_import`、`amendments/`） | 2,797 行；`_neutral_asset_import` docstring 第一行自述 *"Removable one-shot Adapter"* |

### 它抓到過什麼

8/29 cutover 到 9/12，套件共 20 個 commit，逐一分類：

- **14 個是框架在跟自己打架**：時長守衛混用單位把表示誤差當漂移擋下（fcae6f4f）；來源端格數換算錯（ee11f277）；交易做完才失敗卻結不了帳（854edf9f）；素材目錄凍結導致登錄後買的素材進不來——**同一件事修了三次**（1a5c5690、41454b88、1c76a1dd）；修正窗口關太緊，要補 Revision 才能快速改（0d408aee、cce8d93b）；政策要降級成「剪輯不是寫程式」（5bdc499b）；cutover 之後才發現沒接到 Resolve（4c29fffc、07ba8ddc）；語意 worker 寫死 Codex（5203e32d）；版位版本兩份不同步、27 個測試一起紅（6d464d19）；packet 沒帶預覽畫格（8cdd2bce）。
- **0 個是「守衛抓到真問題，然後修了那個真問題」。**
- 其餘是真的產品工作（創意手冊進 prompt）與外部變化（Envato 改站、主控台視窗）。

41 種錯誤碼，掃兩集 run log 與 runtime store，**曾出現過 5 種**：

| 錯誤碼 | 次數 | 實際上是什麼 |
|---|---|---|
| `resolve_project_identity_mismatch` | 14 | 第一次綁 Resolve 專案的設定摩擦 |
| `canonical_binding_unknown` | 13 | 同上，同一件事的另一個碼 |
| `semantic_dispatch_error` | 3 | subagent 逾時——真的，但一行就能報 |
| `timeline_duration_drift` | 1 | **守衛自己的誤判**（fcae6f4f） |
| `resolve_prepare_failed` | 1 | Resolve 準備階段失敗 |

**36 種從未出現。出現過的 5 種裡沒有一次是擋下真實的內容或完整性故障。**

### 唯一的真實事故，以及什麼真正接得住它

ADR-066 的起因是 `punch-L04`：worker 把 34 個舊 Director event 加一個常數平移後當新回覆交出來。整套 authority-chain（每個物件帶 acceptance id、逐層機器比對）是為這件事設計的。

但那次事故最後是**怎麼被發現的**：owner 在 review 看到「標題密度、語意、風格都是舊的」。也就是說，**接住它的是人眼看 diff，不是 id 比對**。id 比對能證明「這份回覆屬於這一輪」，證明不了「這份回覆是好的」——平移複製的 34 個 event 每一個都帶著合法的 id。

便宜且有效的形式：**review 頁顯示這一輪與上一輪的 event diff**。34 個 event 全部只差同一個常數，一眼就看得出來。

### 它每一集收的稅

- 任何人（agent 或人）碰這條線之前要先重讀契約。2026-09-12 一個三行規則能講清楚的 brand badge，撞四道封閉契約，兩個回合、約 20 次檔案探勘、零行可用程式碼。
- 同一個視覺詞彙 27 份副本，任何一份漏改就是一批測試紅——今天發生了三次同形狀的 bug（版位版本、title_trace 覆寫、候選池覆寫）。
- ADR-066 自己的 Consequences 說 *"Targeted retries remain cheap"*；實際上 owner 9/8 的回饋是「每次做一些改動就會把之前的弄壞。我到底要改到什麼時候？」

## Decision

### 留下來的三件事（真的救不回來的）

1. **動 Resolve 之前先複製 timeline；動完數 V1／音軌／字幕軌的 item 數沒變。** V1 是 owner 的剪輯，不可再生。這件事現在已經在做（`_resolve.py` duplicate-work transaction ＋ `protected_track_drift`），保留原樣。
2. **`master.mp4` 一個 sha256。** 剪錯版本的代價是中等，一個雜湊就擋得住。
3. **每支素材旁一張收據**（來源網址、授權字串、sha256）。**不驗網址形狀。** 授權風險是真的；供應商換網址格式不是。

### 換形式的一件事

4. **權威鏈（punch-L04 防線）從「機器逐物件比對 id」改成「review 頁顯示與上一輪的 diff」。** 保留 run 邊界的單一檢查（這份回覆屬於這個 run），砍掉逐物件、逐層的比對。

### 其餘：砍

同一件事驗三遍的、防從未發生過的事的、一次性遷移已經做完的、副本。逐項見下方清單。

### 不動的東西

- 公開 API（`__init__.py` 的 `__all__`）維持穩定；七個外部呼叫端不改 import：`agents/usopp/publish_timeline.py`、`scripts/diagnose_derived_build.py`、`scripts/finished_review_watcher.py`、`scripts/run_finished_cut_production.py`、`scripts/sync_resolve_config.py`、`thousand_sunny/adapters/finished_cut_review.py`、`thousand_sunny/routers/highlight_review.py`。
- ADR-064：cut 的每個 cue 必須錨定在 Editorial Master 上（`_context.py`）。這不是守衛，是資料模型。
- ADR-067：Long/Short 政策分家（`_policy.py`）。這是創意規則，不是防禦。
- 既有已封存的 Release 與 receipt 仍讀得回來（reader 保留寬鬆；writer 只出新形狀）。

## 砍除清單

### A. 錯誤碼：41 → 12

| 原錯誤碼 | 出現處 | 判定 | 去向 |
|---|---|---|---|
| `materialization_journal_conflict` | 14 | **砍** | 「先複製 timeline」就是 rollback；日誌是背帶加吊帶 |
| `materialization_journal_invalid` | 5 | 砍 | 同上 |
| `materialization_journal_write_failed` | 1 | 砍 | 同上 |
| `materialization_journal_incomplete` | 1 | 砍 | 同上 |
| `editorial_master_cache_invalid` | 7 | 砍 | 8 種 master 身分碼 → 1 種 `editorial_master_mismatch`（一個 sha256） |
| `editorial_master_contract_invalid` | 4 | 砍 | 同上 |
| `editorial_master_identity_mismatch` | 3 | **併** | → `editorial_master_mismatch` |
| `editorial_master_media_drift` | 2 | 併 | 同上 |
| `editorial_master_verification_failed` | 1 | 砍 | 同上 |
| `editorial_master_project_mismatch` | 1 | 砍 | 同上 |
| `editorial_master_duration_invalid` | 1 | 砍 | 同上 |
| `editorial_master_content_identity_mismatch` | 1 | 砍 | 同上 |
| `authority_chain_mismatch` | 7 | **換形式** | → 1 處 run 邊界檢查 `authority_mismatch`；逐物件比對改成 review diff |
| `subtitle_staging_conflict` | 5 | 砍 | 字幕從 master cue 推導；master 已由 sha256 保證 |
| `subtitle_contract_drift` | 5 | 砍 | 同上 |
| `subtitle_staging_failed` | 1 | 砍 | 同上 |
| `canonical_timeline_unknown` | 2 | 併 | 8 種 Resolve 綁定碼 → 1 種 `resolve_project_identity_mismatch`（uid 對不上） |
| `canonical_timeline_ambiguous` | 2 | 併 | 同上 |
| `canonical_timeline_live_drift` | 1 | 併 | 同上 |
| `canonical_identity_mismatch` | 1 | 併 | 同上 |
| `canonical_binding_unknown` | 1 | 併 | 同上 |
| `canonical_binding_ambiguous` | 1 | 併 | 同上 |
| `resolve_project_identity_mismatch` | 2 | **留** | 保留為合併後的那一個 |
| `protected_track_drift` | 3 | **留** | 這是 V1 的保命符，原樣保留 |
| `timeline_duration_drift` | 1 | 砍 | 唯一 fired 過的一次是誤判（fcae6f4f）；Resolve 回報的時長就是時長 |
| `frame_rate_drift` | 1 | 砍 | 同上 |
| `timeline_frame_rate_unavailable` | 1 | 砍 | Resolve 回報 fps；拿不到是 Resolve 壞了，走 `resolve_prepare_failed` |
| `final_asset_unavailable` | 4 | **留** | 素材不在 store 裡，真的要擋 |
| `final_asset_identity_mismatch` | 2 | 換形式 | → `asset_receipt_missing`；擋「沒收據」，不擋「收據網址形狀不對」 |
| `candidate_staging_failed` | 2 | 砍 | Candidate／Release 兩階段合一，見檔案清單 |
| `preview_transaction_mismatch` | 2 | 砍 | 同上 |
| `preview_probe_failed` | 2 | **留** | 出來的 mp4 要能播 |
| `source_range_drift` | 2 | 砍 | ADR-064 錨定由 `_context` 資料模型保證，不需要第二道 |
| `source_range_outside_editorial_master` | 1 | **留** | ADR-064：cut 必須落在 master 裡面 |
| `semantic_dispatch_indeterminate` | 1 | 併 | 3 → 1 `semantic_dispatch_failed` |
| `semantic_dispatch_incomplete` | 1 | 併 | 同上 |
| `semantic_dispatch_error` | 1 | **留** | 保留為合併後的那一個 |
| `resolve_prepare_failed` | 1 | **留** | Resolve 起不來要說 |
| `production_run_not_review_ready` | 1 | 砍 | 那是狀態，不是錯誤；status view 已經有 |
| `production_run_missing` | 1 | **留** | |
| `materialization_plan_missing` | 1 | **留** | 改名 `plan_missing` |

**留下的 12 種**：`resolve_project_identity_mismatch`、`protected_track_drift`、`resolve_prepare_failed`、`editorial_master_mismatch`、`source_range_outside_editorial_master`、`final_asset_unavailable`、`asset_receipt_missing`、`preview_probe_failed`、`authority_mismatch`、`semantic_dispatch_failed`、`production_run_missing`、`plan_missing`。

### B. 檔案：40 → 約 25

| 檔 | 行 | 判定 | 理由／目標 |
|---|---|---|---|
| `_cutover.py` | 712 | **刪** | 三集一次性遷移，2026-08-30 已完成。git history 是 audit trail |
| `_neutral_asset_import.py` | 326 | **刪** | 自述 *removable one-shot*，遷移已完成 |
| `_amendment.py` | 210 | **刪** | 套在已封存 Release 上的變換層；三支釘死的歷史腳本用完即棄 |
| `amendments/` 全部（`_journal`、三支 `operations/*_l04_*`、`align_long3_*`、`__init__`） | 1,846 | **刪** | 20260901 蘇予昕 一次性的修補腳本。要重做就走正常 correction，不是留一套平行的變換引擎 |
| `_materialization_fusion.py` | 514 | **刪** | 「Resolve-backed read-only authority」——同一件事的第二個權威來源，與 `_resolve_davinci` 重疊 |
| `_materialization.py` | 1,225 | **重寫 → 約 200** | 現在是 plan → journal → staged Candidate → 逐 byte 驗字幕 → Release。改成：plan → Resolve adapter → probe preview → Release。journal、subtitle 重推導、Candidate 中間態全砍 |
| `_store.py` | 1,370 | **重寫 → 約 400** | 70 個 `raise` 幾乎全是「持久化的 X 欄位不合法」手寫檢查；改用 pydantic model 讀回，一行 |
| `_persistence.py` | 583 | **縮 → 約 100** | 只留 atomic write（temp + rename）；transaction／cutover 兩套持久化砍 |
| `_release.py` | 743 | **縮 → 約 250** | 砍第三份視覺詞彙副本（`allowed_projection`）改 import `_projection`；Release = plan id + preview sha256 + 時間，不再重驗每個 component |
| `_resolve_fusion.py` | 905 | **縮 → 約 450** | 留 duplicate／rename／delete／append／protected-track count／render；砍 8 種綁定交叉驗證、時長與幀率漂移 |
| `_resolve_davinci.py` | 584 | **縮 → 約 300** | 隨 `_resolve_fusion` 縮 |
| `_engine.py` | 2,474 | **縮 → 約 1,200** | 權威鏈逐物件比對改成 run 邊界一次；journal 相關流程砍；correction 流程簡化 |
| `_assets.py` | 451 | **縮 → 約 200** | 砍 Pexels／Envato URL 形狀 profile（今天 f42064db 就是這個 profile 拒收合法素材）；收據 = 4 個欄位 |
| `_composition.py` | 970 | **縮 → 約 600** | 隨接線減少而縮；`_ProductionMediaIdentityResolver` 留（內容定址是留下來的第三件事） |
| `_worker_packet.py` | 733 | **縮 → 約 500** | 砍 packet 內容的逐欄位重驗；packet 由 pydantic 定型 |
| `_correction.py` | 250 | 縮 | targeted retry 概念留；「exact-event authority transition」的多層 id 砍 |
| `_approved_cut.py` | 555 | 縮 | 登錄的 payload 雜湊當 key 留；其餘欄位驗證交 pydantic |
| `_codex_semantic.py` | 1,019 | 縮 | 3 種 dispatch 錯誤碼 → 1；workspace 隔離留 |
| `_projection.py` | 114 | **留，升格** | 成為**唯一**的視覺詞彙宣告；其餘 26 處全部改 import 這裡（`LAYOUT_VERSIONS` 已於 6d464d19 收進來，是這條路的第一步） |
| `_resolve.py` | 466 | **留** | duplicate-work transaction 就是留下來的第一件事 |
| `_context.py` | 260 | **留** | ADR-064 錨定，資料模型不是守衛 |
| `_policy.py` | 588 | **留** | 創意規則（8 分鐘下限、密度、橫式 stock）；5bdc499b 已分級 |
| `_records.py` | 655 | 留，微縮 | public view |
| `_timeline_apply.py` | 123 | 留 | |
| `_active_store.py` | 513 | 留，微縮 | 內容定址 store |
| `_derived_assets.py` `_visual_assets.py` `_long_visual_renderer.py` `_hyperframes_renderer.py` | 1,931 | **留** | 渲染就是產品本身 |
| `_face_placement.py` | 694 | 留 | 產品功能（臉部安全落點） |
| `_semantic.py` `_agent_handoff.py` `_commands.py` `_brand_badge.py` `__init__.py` | 733 | 留 | |

**估計**：19,701 → **約 9,000 行**；`raise` 896 → 約 200；封閉集合 77 → 約 15（每個詞彙一份）。

### C. 白名單副本收斂

同一套視覺詞彙現在在這些地方各宣告一次：`_projection`（權威）、`_release.allowed_projection`、`_resolve_fusion._LANE_TRACKS`、`_derived_assets._GENERATED_IMPLEMENTATIONS`／`_NEUTRAL_PASSTHROUGH_IMPLEMENTATIONS`、`_visual_assets` 的 `expected_kind` 對照、`_worker_packet`、`_policy`、`_long_visual_renderer._RECIPES` 的 key、`_persistence`、三支 amendment 腳本。

收斂後：**`_projection.py` 是唯一宣告**，內容為

```
每個 implementation_kind 一筆：{semantic_kind, lane, track_index, asset_kind, layout_version, generated: bool}
```

其餘全部 `from ._projection import VOCABULARY` 再推導自己要的形狀。**新增一種視覺元素（例如 brand badge）只改這一個檔。** 這正是 2026-09-12 badge 撞四道門的解法：不是開四個縫，是讓四道門讀同一張表。

### D. 測試：22,117 行跟著砍

- 被砍的守衛，對應測試一起走（journal、subtitle drift、canonical 交叉驗證、URL profile、cutover、amendment、neutral import）。
- **留下來的三件事各自的測試必須保留並且不可弱化**：`test_finished_cut_resolve*.py` 裡 duplicate／rollback／protected-track 的案例、master sha256 案例、素材收據案例。
- 重寫的模組用**行為測試**證明等價：同一份 approved cut 進去，出來的 Resolve timeline item 列表逐項相同。這是每一個階段的驗收條件。

## 副作用（誠實列）

| 放掉的防線 | 多承擔的風險 | 為什麼可以承擔 |
|---|---|---|
| 逐物件 authority id | agent 交出一份「合法但錯」的回覆，gate 不先擋 | owner 本來就在 timeline review 看每一支；review 頁加 diff 之後比 id 比對更容易看出平移複製 |
| materialization journal | 兩個 session 同時改同一集會互相蓋 | 一集一個 session，從未同時發生；真要防，一個 lock 檔 20 行 |
| 字幕逐 byte 重推導 | master 沒變但字幕匯出壞掉 | 從沒發生；preview probe 會看到字幕軌 item 數 |
| 8 種 master 身分碼 | 某種罕見的部分損壞 sha256 沒抓到 | sha256 抓得到任何 bit 變化 |
| forensic 鏈（journal + Candidate + 逐層 receipt） | 三週後想回溯「那一幀為什麼長那樣」會變薄 | 從沒查過；git history ＋ Release 的 plan id 仍可回溯到哪一版 plan |
| 供應商 URL profile | 收據裡的網址是假的 | 收據是 agent 自己寫的，形狀驗證擋不住造假；擋得住的是「有沒有收據」與 sha256 |

**不放掉的**：V1 保護、master sha256、素材收據、ADR-064 錨定、Long/Short 政策分家。

## Considered options

### 維持現狀，逐個 bug 修
兩週 14 個框架 bug 的趨勢線不會自己轉彎。每修一個守衛的誤判，就是再讀一次 42k 行。否決。

### 只收斂副本（C 節），守衛全留
解掉 badge 那類「四道門」的問題，但每一集的固定稅（journal、8 種身分碼、逐 byte 字幕）不變，且 36 種從未 fired 的錯誤碼繼續佔測試。做一半。否決，但 C 節是本 ADR 的**第一階段**。

### 回到 ADR-065
ADR-066 的決策（單一深模組、單一權威鏈、Long/Short 分家）是對的，是實作超標。回頭等於把對的決策一起丟掉。否決。

### 本 ADR：留三件事，其餘砍或換形式
採用。

## 執行順序（每一階段獨立可合併，各有測試證明行為不變）

1. **收斂詞彙**（C 節）：`_projection` 升格為唯一宣告，26 處改 import。純重構，全部既有測試須綠。**做完 badge 就能接**。
2. **刪一次性碼**：`_cutover`、`_neutral_asset_import`、`_amendment` + `amendments/`。刪對應測試。回歸：既有 Release 仍讀得回來。
3. **砍 journal 與 Candidate 中間態**：重寫 `_materialization`、縮 `_persistence`。回歸：同一 approved cut → 相同 timeline item 列表。
4. **身分碼收斂**：master 8 → 1、Resolve 8 → 1、dispatch 3 → 1、字幕 3 → 0、drift 3 → 0。回歸：故意換掉 master.mp4 一個 byte 必須被擋；故意改 V1 一個 item 必須被擋。
5. **權威鏈換形式**：`_engine` 逐物件比對改 run 邊界；Bridge review 頁加 event diff。回歸：用 punch-L04 那份平移複製的回覆當測試資料，diff 必須顯示 34 個 event 同一常數。
6. **素材收據簡化**：砍 URL profile。回歸：沒收據的素材必須被擋；Envato 新舊兩種網址都必須過。

每一階段一個 PR。**不做階段 5 之前不砍 authority-chain**——diff 要先能看到，才拿掉機器比對。

## Review record

- 待 owner 簽核。本 ADR 的證據（規模量測、20 個 commit 分類、5/41 fired 統計）由 2026-09-12 session 實測，指令與輸出留在該 session transcript。
