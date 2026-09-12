# ADR-069: Finished Cut Production 回到 ADR-066 自己說的 inexpensive structural gates

- **Status**: **Accepted** — owner 簽核 2026-09-12（三項裁決：放棄封存 Release、person_inset 整刪、`chapter_transition_projection_mismatch` 升回 blocking）
- **Date**: 2026-09-12（v1）→ 2026-09-12（v2，三方審查後）
- **Owner**: Brook / Podcast Stage 5
- **Stage**: 5 Multi-channel Production
- **Amends**: ADR-066 的實作範圍與三條決策：(1) Candidate → seal → Release → pointer → cutover 的封存鏈**退役**，review_ready 的 plan 就是紀錄；(2) 「constructors 不公開」的 authority sentinel 不變式**取消**；(3) §Open follow-up 的 `request_amendment` 承諾**放棄**。其餘（一個深模組、一條 AcceptedStage 權威鏈、Long/Short 分家）不變
- **Preserves**: ADR-064 Editorial Master 真相根；ADR-051 Director 創意所有權；ADR-067 長短分家
- **Invokes**: `memory/claude/feedback_codebase_minimalism_first_principle`（2026-05-17 owner 裁決：程式庫精簡是最高指導原則，預設動作是砍）

## v2 相對 v1 改了什麼

三個獨立 reviewer（創作者視角／cost×risk×complexity 審計／ADR-066 原作者辯護）一致裁決 v1「改了再簽」。v2 的改動：

- **數字全部重算**（v1 漏算 `amendments/`、漏了動態組字串的 6 種錯誤碼）。
- **v1 標「砍」但砍了會真的壞的，改回保留**：每段頭尾比對（`source_range_drift`）、活字幕軌比對、master content hash 與快取（`_materialization_fusion.py` 的邏輯）、三層 parent 連結（`_current_chain_is_exact`）、retry base、派工簿記三態、canonical 精確匹配、素材 bytes 比對。每一條 v1 的承擔理由都是**事實錯誤**，不是取捨差異——見〈保留清單〉各列的「v1 錯在哪」。
- **v1 標「留」但其實該砍的，加進砍除清單**：`_face_placement.py`（person_inset 495 集 0 次使用）、`ShortPolicy`、legacy 路徑掃描、hyperframes 供應鏈釘死、`_release_payload` 死碼、五個 authority sentinel、退役詞彙相容層。
- **journal 的承擔理由改正**：它從來不管並行（並行由 `_store.py:274-287` 既有的 filelock 管），它管的是重入冪等；冪等改由交易復用＋回歸測試保住，journal 檔才能砍。
- **owner 三項裁決落地**：放棄封存 Release（`_cutover.py` 整刪，plan 成為紀錄，Bridge 與分章改讀 plan）；person_inset 整刪；`chapter_transition_projection_mismatch` 升回 blocking。
- 「不動的東西」改正：公開 API **會**變（`ProductionCutoverConfiguration` 與 `cutover` CLI 移除）；兩支 script 直接 import 私有名要一起改。
- 留下的每一種錯誤碼附「你現在該做什麼」。

## 一句話

ADR-066 的〈Validation profile〉寫著 *"Normal production keeps only **inexpensive structural gates**"*。實作跑出來是 **21,547 行、991 個 `raise`、48 種錯誤碼、77 份白名單**。本 ADR 把實作拉回它自己的授權範圍：**留下防三件事的檢查——你登錄後動了你的東西、機器動到了你的軌、素材不是登記的那一份——其餘砍掉或換成便宜的形式。**

## Context — 證據

### 規模（v2 重算）

| 量測 | 數字 |
|---|---|
| `agents/brook/script_video/finished_cut_production/` | 40 個檔、**21,547** 行（含 `amendments/` 1,846） |
| 對應測試 | 38 個檔、22,117 行 |
| `raise` 語句 | **991** |
| 錯誤碼 | **48** 種：41 個字面 `reason_code` ＋ `_codex_semantic.py:379-394` 動態組字串的 6 種 `semantic_*` ＋ `_materialization.py:206-210` 的 fallback |
| 封閉集合（`frozenset` / `Literal` 白名單） | 77 個，散在 25 個檔（不含 amendments） |
| `hero_title` 這一個詞被獨立宣告的地方 | 11 個檔、25 處字串字面值 |
| 一次性遷移碼仍在產線裡（`_cutover`、`_neutral_asset_import`、`amendments/`） | **2,884** 行；`_neutral_asset_import` docstring 第一行自述 *"Removable one-shot Adapter"* |
| `verify_editorial_master` 的包裝層 | **3 層**（`_approved_cut.py:49`、`_materialization_fusion.py:81`、`_face_placement.py:245`） |
| `person_inset` 在 runtime store 495 個 episode JSON 裡的出現次數 | **0**（帶 694 行實作 ＋ 665 行測試 ＋ OpenCV 5.0.0 硬釘） |
| `"format": "short"` 的 run | **0**（ADR-067 已把短片移到 `shortform-cut`；`ShortPolicy` 整條是死路） |
| 封存過的 Release | **0**；0 個 current pointer；6 筆 transaction 全停在 `preview_ready`；G: 全掃無 `nakama.finished_cut_release.v1` |

### 它抓到過什麼

8/29 cutover 到 9/12，套件 20 個 commit，逐一分類：

- **約 12 個是框架在跟自己打架**：時長守衛混用單位（fcae6f4f）；格數換算錯（ee11f277）；素材目錄凍結導致登錄後買的素材進不來——同一件事修了三次（1a5c5690、41454b88、1c76a1dd）；修正窗口關太緊要補 Revision（0d408aee）；政策降級「剪輯不是寫程式」（5bdc499b）；cutover 之後才發現沒接到 Resolve（4c29fffc、07ba8ddc）；版位版本兩份不同步（6d464d19）；下游硬限制上游看不到（785c3fe1）。
- **v1 寫「0 個守衛抓到真問題」，v2 修正**：854edf9f 修的是本 ADR 要保住的重入冪等（不是誤判）；1a5c5690 的 commit body 記錄 store 的 run 歷史讓一個真實死結被診斷出來。這兩個不算「打架」。
- 其餘是產品工作（創意手冊進 prompt、handoff runner）與外部變化（Envato 改站、主控台視窗）。

錯誤碼在**持久化 store** 裡真的 fired 過的：`semantic_process_timeout` ×7、`semantic_output_invalid` ×1、`semantic_dispatch_error` ×1——**全部是「subagent 沒回來」**，而前兩種在 v1 的 41 種清單之外。run log 文字裡另有 Resolve 綁定設定摩擦（27 次）與一次守衛誤判（fcae6f4f）。**沒有一次是擋下真實的內容或完整性故障。**

### 唯一的真實事故，以及什麼真正接得住它

ADR-066 的起因是 `punch-L04`：worker 把 34 個舊 Director event 加一個常數平移後當新回覆交出來。那次事故是 **owner 在 review 看到「標題密度、語意、風格都是舊的」**才發現的——接住它的是人眼，不是 id 比對；平移複製的 34 個 event 每一個都帶著合法的 id。

便宜且有效的形式：**review 頁顯示這一輪與上一輪的 event diff**（顯示成「第幾秒、哪張卡變了」，不是 JSON；第一輪沒有上一輪時，全部標「新增」）。但 diff 是**加法**：三層 parent 連結（保留清單 G）擋的是「DP 仍掛在被取代的 Director 上」，那一層的特徵是「沒變」，diff 天生看不到。

### 它每一集收的稅

- 任何人碰這條線之前要先重讀契約。2026-09-12 一個三行規則能講清楚的 brand badge，撞四道封閉契約，兩個回合、約 20 次檔案探勘、零行可用程式碼。
- 同一個視覺詞彙 25 份副本，任何一份漏改就是一批測試紅——同一天發生三次同形狀的 bug（版位版本、title_trace 覆寫、候選池覆寫）。
- 供應商換網址（f42064db）→ 整個素材庫讀不了 → 全線停產。
- Bridge 與 YouTube 分章設計上從 Release 讀，而 Release 從未封存過，所以**一直是空的**；owner 沒缺過，因為他自己從 Resolve 匯出、自己上傳。

## Decision

### 全貌：這條線上只有兩種東西

**owner 擁有的**：V1、A1、字幕軌。機器只能讀。
**機器擁有的**：V2–V7。每次物化清空重鋪。
**橋叫「登錄」**：owner 在 timeline 上確定剪輯後，agent 記下 V1 每一段用了 master 的哪一段、字幕 cue 對上哪一句。**之後所有 B-roll 落點都是對著登錄算的，不是對著 Resolve 現在的樣子算的。**

所以留下的檢查在回答三個問題：**你登錄後動了你的東西嗎？機器動到你的軌嗎？素材是登記的那一份嗎？**

### 保留清單 A–L

評分 1–5。Cost＝寫它、維護它、改動時要重讀它；Risk＝防的事的發生機率 × 代價；Complexity＝多難懂、多少地方要同步。

| # | 項目 | 做什麼 | 什麼時候會擋你 | 拿掉會怎樣 | C | R | X | 決定 | v1 錯在哪 |
|---|---|---|---|---|:-:|:-:|:-:|---|---|
| **A** | 先複製 timeline 再動（`_resolve.py` duplicate-work） | 物化前複製一份，機器改副本；原本改名 `__fcp_backup__` | 從不擋你；它是退路 | 機器直接在你的剪輯上動手，出錯沒退路 | 1 | 5 | 1 | **留** | — |
| **B** | 數片段（`protected_track_drift`，`_materialization.py:1106,1139`） | 物化前後數 V1／A1／字幕軌各幾段、都在都啟用 | 登錄後多切一刀、刪一段、關掉字幕軌 | 照舊結構鋪 B-roll 全錯位；機器動到 V1 沒人發現 | 1 | 4 | 1 | **留**；`:1128`（adapter 自洽性）砍 | — |
| **C** | 每段的頭尾（`source_range_drift`，`:1141-1180`） | 逐段比 V1 第 N 段用 master 幾秒到幾秒 | 登錄後修剪 2 秒或滑動——**段數沒變，B 抓不到** | 該段之後全部錯位；1 GiB render 白做；後續修正基準全錯 | 1 | 4 | 1 | **留，併入 B 同一碼** | v1 說「`_context` 資料模型保證」——`_context` 只知道登錄時的樣子，看不到 Resolve 現在的樣子。4 次誤判全是單位換算 bug，已修 |
| **D** | 活字幕軌逐句比對（`:1188-1209`） | 字幕軌每句時間（容 1 格）與文字 vs 登錄 cue | 在 Resolve 直接改錯字、沒走 `--refresh-subtitles` | Hero 引舊文字；名牌錨舊時間 | 1 | 3 | 1 | **留，併入 B** | v1 說「master 已由 sha256 保證」——那是 `master.srt` 檔案，不是活的字幕軌 |
| **E** | Master 沒被換掉（`_materialization_fusion.py:81-183, 108-121`） | 登錄時對的 Master（mp4+srt）還是同一份；**content hash**（含 `master_srt_sha256`）算一次快取 | 重跑字幕 → 新 hash，cut 照舊版登錄 | 所有 cue 指向舊字幕；沒快取則 watcher 每次 advance 重算 10 GB | 2 | 5 | 2 | **留邏輯；8 種錯誤碼 → 1 `editorial_master_mismatch`；檔案併入 `_resolve_davinci`** | v1 標整檔「刪」——驗證邏輯與快取就住在這個檔，`_composition` 也 import 它。v1 說「一個 sha256」——要用 content hash，不是 mp4 sha256，否則 SRT 重出漏掉 |
| **F** | 複製到對的那條（`_materialization_fusion.py:238-250`） | UID＋名稱確認是 canonical 不是 `__fcp_backup__` | 多條相似 timeline、UID 換手（那 27 次 fired） | 複製到備份 → 備份後的剪輯不在成品裡 | 1 | 3 | 1 | **留 1 檢查；8 種綁定碼 → 1；雙讀（`:261-269`）砍** | — |
| **G** | 三層沒錯配（`_engine.py:1458-1487` `_current_chain_is_exact`） | Director→DP→review 各記基於哪一版，鑄 plan 前確認指向最新 | 退了 Director，DP 卻還基於舊版（修正中斷、重啟） | plan 用新事件配舊落點；**diff 看不到**（錯的那層「沒變」） | 1 | 3 | 1 | **留**；`_materialization.py:982-1021` 四個格式驗證搬到 `EditorialCutContext.__post_init__`（與 `_approved_cut.py:436-553` 重複） | v1 說 `authority_chain_mismatch` 在 `_engine.py` 且是「逐物件」——全在 `_materialization.py`，其中 4 個是資料驗證；真正的鏈檢查是 3 個 id 比對，成本近零 |
| **H** | 局部重做基於最新版（`_engine.py:1426-1455`） | event_retry 確認基準是最新 acceptance | 連續修正兩次，第二次指回第一版 | 從舊基準複製「沒動的列」→ 前一輪修正**靜默回退** | 1 | 3 | 1 | **留（與 G 同組）** | v1 沒看到 |
| **I** | 派工簿記三態（`_store.py:140-146`） | 完成／失敗／**不確定**（派出去、process 崩） | subagent 逾時（兩集 7+ 次） | 「不確定」併「失敗」→ 自動重派 → 兩份回覆、多燒 quota | 1 | 3 | 1 | **留三態；錯誤碼 3 → 1，diagnostic 欄位帶原因** | — |
| **J** | 素材是登記的那一份（`_materialization.py:926-942`） | 登記算 sha256 寫收據；物化重算確認沒被換 | 同名重下載、補買覆寫 | 用錯 bytes 上 timeline | bytes 1／**URL 形狀 3** | bytes 4／**URL 1** | 1／3 | **收據留、bytes 比對留（自己的碼 `asset_digest_mismatch`）；URL 形狀砍；授權字串釘在 `_assets.py:22-23` 兩個常數，`_ACQUISITION_SOURCE_CLASSES` 留** | v1 把 bytes 比對藏在「改名成 receipt_missing」裡 |
| **K** | 六個「東西不在」 | 素材不在庫、mp4 不能播、cut 超出 master、run／plan 不存在、Resolve 起不來 | 對應的東西真的不在 | 在更深處用更難懂的錯炸掉 | 1 | 3 | 1 | **留** | — |
| **L** | 交易紀錄與不重做兩次（`_persistence.py:382-410`；`_resolve.py:331-351`） | 記「複製了哪條、備份叫什麼、baseline」＝rollback 憑據；`advance()` 每次重進 prepare（watcher 反覆呼叫）要認得「已物化」 | 拿掉紀錄：rollback 不知回哪；拿掉冪等：每次 advance 多一條備份＋一次 15 分 render | 見左 | 紀錄 1／信封 2／**journal 4** | 紀錄 4／信封 1／journal 1 | 1／2／4 | **紀錄內容留；checksum 信封（`:473-517`）砍；journal 檔砍——冪等靠 plan-keyed 交易復用（854edf9f）＋ 回歸測試「同一 cut advance 兩次、跨重啟 → duplicate／render 各恰好 1 次」** | v1 說 journal 防「兩個 session 同時改」——並行由 `_store.py:274-287` 既有 filelock 管，journal 從來不管並行 |

### 換形式的一件事

**權威鏈（punch-L04 防線）**：保留 G＋H 的機器比對（成本近零），**加上** review 頁的 event diff。diff 規格：每列「第幾秒、哪一種卡、原文→新文」；第一輪全部標「新增」；顯示在 timeline review 頁，不是 JSON。

### owner 三項裁決

1. **放棄封存 Release。** `_cutover.py` 整刪；Candidate／seal／pointer／cutover 四個概念退役。**review_ready 的 plan 就是紀錄**：plan 帶上 Resolve timeline 名、transaction receipt id、preview sha256、events、components。`agents/usopp/publish_timeline.py`（`:125-141` 反查 timeline 名、`:145-175` 分章）與 `thousand_sunny/routers/highlight_review.py:389-411`（讀 events／components）**改從 plan 讀**。`publish_timeline.py` docstring 記的「260 秒舊剪輯冒充 492 秒成品」事故，改由 plan 的 timeline 名＋preview sha256 防。理由：這條路從未在現行資料上跑過，owner 的實際工作方式是 Resolve 匯出、自己上傳。
2. **`_face_placement.py` 整刪**（694 ＋ 測試 665）。person_inset 495 集 0 次使用、OpenCV 5.0.0 硬釘、MediaPipe 死碼、第三層 verify 包裝。`person_inset` 從詞彙移除（不需相容層：run store 裡也是 0 次）。需要時再寫。
3. **`chapter_transition_projection_mismatch` 升回 blocking。** 它是全套件唯一有 docstring 記載真實抓到問題的規則（2026-09-08 Director 改寫章節標題、錯字上片）；5bdc499b 誤把它降成警告。`BLOCKING_DIAGNOSTICS` 其餘四條從未 fired，降為警告。

### 不動的東西

- ADR-064：cut 的每個 cue 必須錨定在 Editorial Master（`_context.py`）。資料模型，不是守衛。
- ADR-067：Long 政策（`_policy.py` 的 Long 部分）。創意規則。
- 渲染（`_derived_assets`、`_visual_assets`、`_long_visual_renderer`、`_hyperframes_renderer` 的渲染部分）。那是產品本身。
- `__init__.py` 的 `__all__` **除了** `ProductionCutoverConfiguration` 之外維持；七個外部呼叫端不改 import 路徑，但 `thousand_sunny/adapters/finished_cut_review.py:9-14` 與 `agents/usopp/publish_timeline.py` 讀的**欄位**會從 Release 改成 plan。
- `scripts/sync_resolve_config.py:37`、`scripts/diagnose_derived_build.py:33-34` 直接 import 私有名（`_resolve_fusion`、`_visual_assets`、`_composition`）——縮檔時一起改。

## 砍除清單

### A. 錯誤碼：48 → 13 ＋ 1 條 blocking 政策規則

| 原錯誤碼 | 處 | 判定 | 去向 |
|---|---|---|---|
| `materialization_journal_conflict` / `_invalid` / `_write_failed` / `_incomplete` | 21 | **砍** | journal 檔砍；冪等由交易復用＋回歸測試保住（保留 L） |
| `editorial_master_cache_invalid` | 7 | 砍 | 壞掉的快取 = miss，`return None` 重驗即可，不是錯誤 |
| `editorial_master_contract_invalid` / `_duration_invalid` | 5 | 砍 | 上游 `editorial_master` 輸出錯是 bug，一個 ValueError |
| `editorial_master_identity_mismatch` / `_media_drift` / `_content_identity_mismatch` / `_verification_failed` | 7 | **併** | → `editorial_master_mismatch`（`_verification_failed` 是存活者：`_materialization_fusion.py:108-121` 就是 content hash 不符的落點）；`:456,464` 兩處是參數驗證，砍 |
| `editorial_master_project_mismatch` | 1 | 併 | → `resolve_project_identity_mismatch`（它是綁定問題） |
| `authority_chain_mismatch` | 7 | **拆** | `:136,161,177` → `authority_mismatch`（run 邊界）；`:982-1021` 四個搬進 `EditorialCutContext.__post_init__`，砍 |
| `subtitle_staging_conflict` / `_staging_failed` | 6 | 砍 | 只護機器 staging 目錄（`_verify_srt_bytes`，`:799-905`） |
| `subtitle_contract_drift` | 5 | **拆** | `:851-871` 渲染輸入驗證砍；**`:1188-1209` 活字幕軌比對留**，併入 `protected_track_drift` |
| `canonical_timeline_unknown` / `_ambiguous` / `_live_drift`、`canonical_identity_mismatch`、`canonical_binding_unknown` / `_ambiguous`、`canonical_authority_failed` | 9 | **併** | → `resolve_project_identity_mismatch`；`_live_drift` 的雙讀砍 |
| `resolve_project_identity_mismatch` | 2 | **留** | 合併後的存活者 |
| `protected_track_drift` | 3 | **留、擴義** | 涵蓋 B＋C＋D；`:1128` adapter 自洽性砍 |
| `timeline_duration_drift` / `frame_rate_drift` / `timeline_frame_rate_unavailable` | 3 | 砍（作為獨立碼） | 時長與 C 的 `:1176-1180` 重疊；fps 仍要讀（格數換算用），拿不到走 `resolve_prepare_failed` |
| `final_asset_unavailable` | 4 | **留** | |
| `final_asset_identity_mismatch` | 2 | **拆** | `:929` → `asset_receipt_missing`；**`:938-942` bytes 比對 → `asset_digest_mismatch`**，留 |
| `candidate_staging_failed` / `preview_transaction_mismatch` | 4 | 砍 | Candidate 概念退役（裁決 1） |
| `preview_probe_failed` | 2 | **留** | |
| `source_range_drift` | 2 | **留** | 併入 `protected_track_drift`（保留 C） |
| `source_range_outside_editorial_master` | 1 | **留** | |
| `semantic_dispatch_indeterminate` / `_incomplete` / `_error` ＋ 動態 `semantic_packet_rejected` / `_process_failed` / `_process_timeout` / `_output_missing` / `_output_invalid` / `_dispatch_error` | 9 | **併** | → `semantic_dispatch_failed`，`diagnostic` 欄位帶原因；**ledger 三態不併**（保留 I） |
| `resolve_prepare_failed` | 1 | **留** | |
| `production_run_not_review_ready` | 1 | 砍 | 是狀態，status view 已有 |
| `production_run_missing` / `materialization_plan_missing` | 2 | **留** | 後者改名 `plan_missing` |

**留下的 13 種與「你現在該做什麼」**：

| 錯誤碼 | 意思 | 你現在該做什麼 |
|---|---|---|
| `protected_track_drift` | 你登錄後改了 V1／音軌／字幕軌（段數、每段頭尾、或字幕文字） | 這是正常的——你剪了。重新登錄這支 cut（`register-approved-cut`），plan 會重算 |
| `editorial_master_mismatch` | 這支 cut 登錄時對的 Master 已經換了（通常是重跑了字幕） | 重新登錄這支 cut |
| `resolve_project_identity_mismatch` | 找不到你的那條 timeline，或找到兩條 | 跑 `sync_resolve_config.py` 重新綁定；如果專案裡有兩條同名 timeline，刪掉舊的 `__fcp_backup__` |
| `resolve_prepare_failed` | Resolve 起不來或沒回應 | 開 Resolve、開對的專案，再跑一次 |
| `source_range_outside_editorial_master` | 這支 cut 用到了 Master 以外的畫面 | 登錄檔的 source_ranges 錯了；檢查選段 |
| `final_asset_unavailable` | 一支 B-roll 素材不在素材庫 | DP 要重新取得那支素材（看錯誤裡的 slug） |
| `asset_receipt_missing` | 素材在，但沒有收據（來源、授權） | DP 補收據；沒來源的素材不上片 |
| `asset_digest_mismatch` | 素材檔被換過（同名不同內容） | 確認哪一份是對的，重新登記 |
| `preview_probe_failed` | render 出來的 mp4 不能播 | 看 ffprobe 訊息；通常是 Resolve render 中斷，再跑一次 |
| `authority_mismatch` | 三層企劃（Director→DP→review）對不上，或局部重做的基準過期 | 從上一個乾淨的 stage 重跑一次 `advance`；不要手改 JSON |
| `semantic_dispatch_failed` | subagent 沒回來（逾時／輸出壞掉），看 `diagnostic` 欄位 | 逾時：`retry_failed_dispatch` 重派一次。輸出壞掉：看 diagnostic 裡的原因再重派 |
| `production_run_missing` | 這個 command id 沒有 run | 先 `register-approved-cut` |
| `plan_missing` | run 還沒走到 review_ready | 繼續 `advance` |

**blocking 政策規則（`_policy.py`）**：只剩 `chapter_transition_projection_mismatch`（裁決 3）。其餘全部是警告，跟 plan 一起進 review。

### B. 檔案：40 → 26

| 檔 | 現在 | 目標 | 處置 |
|---|---|---|---|
| `_cutover.py` | 712 | **0** | 刪（裁決 1）。連帶 `_composition.py:27-33, 140-200, 372-405` 接線、`__init__.py:21,65` export、`scripts/run_finished_cut_production.py` 的 `cutover` 子命令與 `--cutover-config`、`test_finished_cut_cutover.py`（762） |
| `_face_placement.py` | 694 | **0** | 刪（裁決 2）。連帶 `_composition.py:959` 接線、`_visual_assets.py` 的 person_inset 分支、`test_finished_cut_face_placement.py`（665）、`assets/haarcascade_frontalface_default.xml` 與其 receipt |
| `_neutral_asset_import.py` | 326 | **0** | 刪。無任何 production 引用（grep ＋ `git log -S`） |
| `_amendment.py` | 210 | **0** | 刪。ADR-066 §Open follow-up 的 `request_amendment` 正式放棄；`docs/plans/2026-08-29-release-amendment-authority-p9.md` 標 superseded |
| `amendments/`（六檔） | 1,846 | **0** | 刪。20260805 林之晨 L04 的 current Release 從此只在 git history 可推導，runtime 不引用 |
| `_materialization_fusion.py` | 514 | **0**（併入） | verify＋cache（`:81-183`）與 UID＋名稱精確匹配（`:238-250`）搬進 `_resolve_davinci`；雙讀、6 種綁定碼砍 |
| `_materialization.py` | 1,225 | **350** | prepare 主流程 ~80、`_validate_editorial_base`（B＋C＋D ＋ master digest）~100、`_validate_final_assets` ~30、`_render_srt` ~40、preview probe ~30、paths ~20；journal、subtitle staging、Candidate 全砍；`_validate_context_contract` 搬去 `_context` |
| `_store.py` | 1,370 | **400** | 先刪五個 authority sentinel（見 C 節），再用一個 ~40 行 generic `from_dict(cls, mapping)` 取代 ~50 個手寫 `_from_dict`；dispatch ledger（`:69-160`）與 filelock（`:274-287`）留 |
| `_engine.py` | 2,474 | **1,200** | journal 流程砍；correction 簡化；G＋H 留 |
| `_codex_semantic.py` | 1,019 | **850** | 動態錯誤碼 6→1 併入 `semantic_dispatch_failed`；legacy 路徑掃描（`:431-462`）砍；workspace 隔離留 |
| `_composition.py` | 970 | **550** | cutover、face、stock metadata 接線砍；`_ProductionMediaIdentityResolver` 留 |
| `_resolve_fusion.py` | 905 | **650** | 91 個 `_required_*` 是跟 Resolve API 講話的正常方式，留；`_snapshot_item`（`:512-585`，Fusion tool identity、audio mapping、full_fingerprint）與 `_timeline_current` 切換（`:609-618`）砍；`:332` landscape 硬 raise 砍（規則只留 `_policy` 一份，警告） |
| `_release.py` | 743 | **150** | seal／Candidate／`allowed_projection` 副本／形狀重驗全砍。留一個 read model：從 review_ready plan 組出 Bridge 與 publish 要的 view（events、components、timeline 名、receipt id、preview sha256） |
| `_worker_packet.py` | 733 | **500** | legacy 路徑掃描（`:431-510`）砍；packet 逐欄位重驗砍 |
| `_policy.py` | 588 | **320** | `ShortPolicy`＋`SHORT_*`（`:565-588, :101-102`）砍；`StockVideoMetadata`＋`stock_video_*` 兩條（~40 行）與 `_composition.py:897` 接線砍；12 條警告規則改 (predicate, code, message) 表格；`chapter_transition_projection_mismatch` 升 blocking，其餘 blocking 降警告 |
| `_resolve_davinci.py` | 584 | **350** | 吸收 E＋F 的邏輯（~100）；其餘隨 `_resolve_fusion` 縮 |
| `_persistence.py` | 583 | **100** | 只留 atomic write（temp＋rename）與交易紀錄內容；checksum 信封（`:473-517`）、cutover store（`:120-225`）、`_release_payload`（`:227-280`，今天 0 呼叫端）砍 |
| `_visual_assets.py` | 578 | **500** | person_inset 分支砍 |
| `_records.py` | 655 | **450** | 五個 sentinel 與 `_mint_/_rehydrate_/_seal_` 8 個函式砍；view 留 |
| `_approved_cut.py` | 555 | **350** | payload 雜湊當 key 留；欄位驗證交 loader；`FilesystemEditorialMasterVerifier` 與 E 合成一層 |
| `_hyperframes_renderer.py` | 529 | **450** | 供應鏈釘死（`:68-145`：runtime root 不可 symlink、Node hash、manifest hash、receipt schema）砍；渲染留 |
| `_active_store.py` | 513 | **450** | 讀 index 時逐筆重驗 receipt profile（`:307→:482`）砍 |
| `_correction.py` | 250 | **150** | targeted retry 留；多層 id 砍 |
| `_context.py` | 260 | **300** | 吸收 `_materialization.py:982-1021` 四個不變式進 `__post_init__` |
| `_projection.py` | 114 | **160** | **升格為唯一詞彙宣告**（見 C 節） |
| `_assets.py` | 451 | **200** | URL 形狀 profile（`:133-170`）砍；兩個 license 常數與 source class 集合留 |
| `_derived_assets.py` | 263 | 230 | person_inset 從 `_GENERATED_IMPLEMENTATIONS` 移除；其餘改 import `_projection` |
| `_semantic.py` | 215 | 200 | 3 碼→1 |
| `_long_visual_renderer.py` `_resolve.py` `_brand_badge.py` `_agent_handoff.py` `_timeline_apply.py` `_commands.py` `__init__.py` | 1,691 | ~1,650 | 留；`__init__` 拿掉 `ProductionCutoverConfiguration` |

**加總**：21,547 → **約 10,500 行（−51%）**；`raise` 991 → 約 250；錯誤碼 48 → 13；測試 22,117 → 約 11,000（cutover 762、face 665、amendment、neutral import 全刪；store 測試 2,481 隨 loader 縮）。

### C. 白名單副本收斂 ＋ sentinel 取消

同一套視覺詞彙現在各宣告一次的地方：`_projection`（權威）、`_release.allowed_projection`、`_resolve_fusion._LANE_TRACKS`、`_derived_assets._GENERATED_IMPLEMENTATIONS`／`_NEUTRAL_PASSTHROUGH_IMPLEMENTATIONS`、`_visual_assets` 的 `expected_kind`、`_worker_packet`、`_policy`、`_long_visual_renderer._RECIPES` 的 key、`_persistence.py:317-325`。

收斂後 **`_projection.py` 是唯一宣告**：

```
每個 implementation_kind 一筆：{semantic_kind, lane, track_index, asset_kind, layout_version, generated: bool}
```

其餘全部 `from ._projection import VOCABULARY` 再推導。**新增一種視覺元素（例如 brand badge）只改這一個檔。**

退役詞彙（`visual_effect`、`supporting_title`、`person_inset`）：磁碟上沒有 Release；89 個已完結的 run JSON 含 `visual_effect`——**先歸檔那些 run，再把相容層全刪**（`_projection.py:14-19`、`_release.py:640-651`、`_resolve_fusion.py:36`、`_derived_assets.py:27`、`LAYOUT_VERSIONS["visual_effect"]`）。

**五個 authority sentinel**（`_records.py:24-27, 94-95, 243-244, 315, 483-484, 554-555`、`_context.py:11, 74-75` 的 `_authority`）：它們覆寫了 dataclass `__init__`，所以 `asdict`／generic loader 都無法 round-trip——**這是 70 個手寫 `_from_dict` 存在的根因**。本 ADR 取消 ADR-066 §Production run and stage authority「Constructors … are not public」的不變式。權威由 run 邊界的 `authority_mismatch` 與 G＋H 保證，不由建構子保證。

### D. 測試

- 被砍的守衛，對應測試一起走。
- **保留清單 A–L 的測試不可弱化**：`test_finished_cut_resolve*.py` 的 duplicate／rollback／protected-track 案例；`test_editorial_base_drift_fails_before_asset_or_timeline_mutation`（L695-721，涵蓋 C＋D 的全部參數）；`test_tampered_visual_lineage_cannot_mint_materialization_plan`（G）；`test_superseded_same_stage_base_fails_before_worker_dispatch`（H）；`test_claimed_request_without_terminal_outcome_cannot_be_recovered`（I）；`test_assetless_component_and_changed_active_object_fail_closed`（J）；master content hash 案例（E）。
- **新增**：`test_success_stages_one_preview_ready_candidate_and_reopens_idempotently` 的行為（advance 兩次、跨重啟 → duplicate／render 各 1 次）改寫成不依賴 journal 的版本，作為階段 4 的驗收。
- 重寫的模組用**行為測試**證明等價：同一份 approved cut 進去，Resolve timeline item 列表逐項相同。

## 副作用（誠實列）

| 放掉的 | 多承擔的風險 | 為什麼可以承擔 |
|---|---|---|
| 封存 Release、pointer、cutover | 沒有「這一版已定案」的不可變記錄；Bridge 與分章從 plan 讀 | 從未用過；owner 的實際流程是 Resolve 匯出、自己上傳；plan 帶 timeline 名＋preview sha256 已能防錯片 |
| journal 檔 | 重入時若交易復用壞掉，會多一份備份、多一次 render | 交易復用已存在（854edf9f）且有回歸測試鎖住；並行另有 filelock |
| 8 種 master 身分碼 → 1 | 某種快取損壞被當成 miss 重驗一次 | 重驗的代價是幾十秒，不是錯誤 |
| 字幕 staging 逐 byte | staging 目錄的 SRT 在寫入後、送 Resolve 前被改 | 機器目錄；活字幕軌另有 D 比對 |
| 供應商 URL profile | 收據裡的網址是假的 | 收據是 agent 寫的，形狀驗證擋不住造假；擋得住的是「有沒有收據」與 bytes sha256；授權釘常數 |
| person_inset | 未來想要臉部安全落點要重寫 | 495 集 0 次使用；重寫時不會帶 OpenCV 版本釘死 |
| `request_amendment` | 對已交付的成品做機械改動要走整趟 `request_revision` | 從未用過（三支腳本是一次性手術） |
| 25 份詞彙副本 → 1 | 無 | 純減法 |
| 五個 sentinel | 理論上可以在 store 之外建構 record | 權威由 run 邊界檢查與 G＋H 保證；建構子保護從未擋下任何事 |

**不放掉的**：A–L 全部、ADR-064 錨定、Long 政策、渲染。

## Considered options

### 維持現狀，逐個 bug 修
兩週約 12 個框架 bug 的趨勢線不會自己轉彎。否決。

### 只收斂副本，守衛全留
解掉 badge 那類「四道門」，但每一集的固定稅不變，且 36 種從未 fired 的錯誤碼繼續佔測試。否決，但它是本 ADR 的階段 1。

### 補一條單支封存路徑（v1 的隱含選項）
從 `_cutover` 拿掉三集固定順序留 ~150 行，Release 保留。owner 2026-09-12 裁決否決：這條路從未跑過，plan 就是紀錄。

### 回到 ADR-065
ADR-066 的核心決策是對的，是實作超標。否決。

### 本 ADR v2
採用。

## 執行順序（每階段一個 PR，各有測試證明行為不變）

1. **詞彙收斂**：`_projection` 升格為唯一宣告，其餘改 import。純重構，全部既有測試須綠。**做完 badge 就能接。**
2. **純刪除**：`_neutral_asset_import`、`_amendment`＋`amendments/`、`_face_placement`（含 person_inset 詞彙與接線）、`ShortPolicy`、legacy 路徑掃描、hyperframes 供應鏈釘死、`_resolve_fusion.py:328` landscape 硬 raise；歸檔含 `visual_effect` 的 89 個 run 後刪相容層。連帶對應測試。回歸：既有 `review_ready` run 仍讀得回來。（`_cutover` 與 `_release_payload` 移到階段 4——見上方〈兩處順序修正〉。）
3. **取消 sentinel、通用 loader**：`_records`／`_context` 的 `_authority` 刪；`_store` 的 `_from_dict` 群 → generic loader；`_materialization.py:982-1021` 搬 `_context.__post_init__`。回歸：store 讀寫 round-trip。
4. **plan 成為紀錄**：Candidate／Release／pointer 合一為「review_ready plan record」（帶 timeline 名、receipt id、preview sha256、events、components）；`publish_timeline.py` 與 `highlight_review.py` 改讀 plan；`_cutover.py` 與 `_release_payload` 在此刪除（連帶 `cutover` CLI、`ProductionCutoverConfiguration` export）；journal 檔砍，`_persistence` 縮到 atomic write＋交易紀錄。回歸：**同一 approved cut `advance` 兩次、跨重啟 → duplicate／render 各恰好 1 次**；Bridge 頁與分章從 plan 出來的內容與現在一致。
5. **身分碼收斂**：master 8→1（content hash＋cache，邏輯搬 `_resolve_davinci`）、Resolve 9→1（UID＋名稱精確匹配留、雙讀砍）、dispatch 9→1（ledger 三態留）、drift 3→0（併入 `protected_track_drift`）。回歸：故意換 master.srt 一個字 → 擋；故意在 Resolve 修剪 V1 一段 2 秒（段數不變）→ 擋；故意改字幕軌一個字 → 擋；專案裡放一條 `__fcp_backup__` → 不會複製到它。
6. **review diff**：Bridge timeline review 頁加「這一輪 vs 上一輪」的 event diff（第幾秒、哪種卡、原文→新文；第一輪全標新增）。G＋H 不動。回歸：用 punch-L04 那份平移複製的回覆當測試資料，diff 必須顯示 34 個 event 同一常數。
7. **素材收據簡化＋政策分級**：URL profile 砍、license 釘常數、`asset_digest_mismatch` 獨立；`_policy` 表格化、`chapter_transition_projection_mismatch` 升 blocking。回歸：沒收據 → 擋；同名換 bytes → 擋；Envato 新舊兩種網址都過；章節標題被 Director 改寫 → 擋。

**階段 6 之前不動 G＋H**——diff 是加法，不是替代；本 ADR 本來就不砍它們。

## 實作中發現的兩處順序修正（2026-09-12，階段 1 實作時）

1. **`_cutover.py` 從階段 2 移到階段 4。** 辯護人的警告成立：它是唯一的
   commit → seal → pointer 路徑，在 plan 成為紀錄之前刪掉它，中間那一個 commit
   會讓 `inspect_current` 永遠空。刪除與替代必須同一個 PR。
2. **`_release_payload` / `_release_from_payload` 不是今天就死的死碼。** v2 引用
   reviewer 的「0 個呼叫端」判斷，實測錯了——它們在 `_persistence.py:163` 與
   `:186` 被 cutover journal 呼叫。所以它們隨 `_cutover` 一起走，也在階段 4。

## 實作中發現的第三處修正（2026-09-12，階段 3 實作時）

3. **`_validate_context_contract` 只有一半搬得進 `__post_init__`。** 它有四條規則，
   其中兩條已經有別的主人，搬過去會變成「第三份實作」而且會弄壞既有行為：

   | 規則 | 處置 | 為什麼 |
   |---|---|---|
   | 每段來源範圍自身有效、不重疊 | **搬進 `__post_init__`** | 只有 context 自己知道；以前只有物化那條路驗得到，store 讀回與 worker packet 都繞過去 |
   | 每句 cue 的 id 唯一、文字非空、時序不倒退 | **搬進 `__post_init__`** | 同上。順帶把 `_approved_cut._validate_cues` 的重複實作刪掉 |
   | 來源範圍總和 == `duration_sec` | **留在 `_policy`** | 它是 `source_range_sum_mismatch` 診斷，要報給修修看。放進建構子等於讓那條診斷永遠發不出來——帶著它的 context 根本造不出來 |
   | `cues` 不可為空、cue 不可越過片尾 | **留在 `_approved_cut`** | 引擎的 in-memory 假 authority 本來就沒有 cue；而「不越過片尾」只有在登錄那一刻才保證 `duration_sec` 就是來源範圍總和（見上一列）。`CUE_END_EPSILON_SEC` 因此只剩這一份實作 |

   淨結果仍然是「一條規則一個地方」，只是那個地方不是每一條都在建構子。

4. **`ProjectedComponent` 的退役投影檢查不能進 `__post_init__`。** `_release` 的
   receipt reader 刻意比 writer 寬鬆——既有 receipt 裡的 `supporting_title` 要讀得
   回來（`test_historical_supporting_title_receipt_remains_read_only_compatible`）。
   規則因此留在 writer 側的 `_mint_projected_component`，store 讀回走同一支。

## 階段 4 實作時量到的事（2026-09-12）

5. **封存鏈從來沒有跑過一次。** 動手前先量了整台機器，不是推論：

   | 量到什麼 | 數字 |
   |---|---|
   | 已 `review_ready` 並有 `materialization.json` 的 cut | 6（20260721 ×3、20260901 ×3）|
   | 其中 `transaction_receipt_id` 不是 `null` 的 | **0** |
   | `G:\Footages` 與 `E:\nakama\data` 裡的 release receipt | **0** |
   | `current.v1.json`（pointer） | **0** |
   | `highlights/publish-timelines.v1.json`（人維護的對應表） | **0** |
   | `authority.json` 裡的 `targeted_revisions` | **0**（兩集都是 `{}`）|

   所以 `StagedReleaseCandidate` → `FinishedCutRelease` → 不可變版本 →
   pointer → `GlobalCutoverJournal` 這五層，加上 `GlobalCutover` 的原子切換與
   回滾，全部只在測試裡執行過。而 `materialization.json` 已經是事實上的紀錄。

6. **這條鏈不只是沒用，它還讓發布線查不到 timeline 名。**
   `publish_timeline.canonical_timeline_from_transactions` 要求交易
   `status == "committed"` 才回名字——而沒有任何路徑會 commit，所以它對每一支
   cut 都回 `None`。`release_chapters` 與 `release_subtitle` 也都以
   `target.release_id is not None` 為前提，於是長片的分章與字幕來源**一直**
   在回退。plan record 直接記下 `timeline`、`preview.duration_sec`、`events`、
   `components`，這三個缺口同時補上——階段 4 因此不是純刪除，是修掉一個
   安靜錯了很久的東西。

7. **`_resolve.commit` / `compensating_rollback` 成為不可達碼。** `_cutover` 是
   它們唯一的呼叫端（grep 實測）。本階段沒有刪它們：交易狀態機與
   `__fcp_backup__` 的保留策略是階段 5「身分碼收斂」要一起看的東西，拆開改
   會讓兩邊都半途。留著的代價是 `ResolveTransactionStatus` 有四個今天到不了的
   狀態——記在這裡，不要當成還有人在用。

8. **`ActiveAssetStore.bind_release` / `resolve_for_release` 沒有生產呼叫端。**
   它們回答的是「哪個已封存的 Release 用了這份素材」。同樣不在本階段動手，
   理由同上：那是素材存活期的問題，跟紀錄層分開處理才看得清楚。

## 階段 5 實作時的一處修正（2026-09-12）

9. **「邏輯搬 `_resolve_davinci`」不做。** v2 寫這一句的時候把兩個 Resolve 模組
   看成一個。實際上 `_materialization_fusion` 的模組定位就是「Resolve-backed,
   read-only authority」——Editorial Master 的收據驗證與 cache 正是那個東西；
   `_resolve_davinci` 是會**動** timeline 的交易 adapter。把一個唯讀的 cache 搬
   進會動手的那一支，是讓兩邊都變模糊，不是收斂。code 的收斂照做，檔案不搬。

   實際收斂結果（用 AST 掃 `reason_code=` 的字面值量的，不是估的）：

   | 家族 | 之前 | 之後 |
   |---|---|---|
   | Editorial Master 身分 | 8 | `editorial_master_mismatch` |
   | Resolve 綁定／canonical timeline | 9 | `resolve_binding_mismatch` |
   | 語意派工（含 `f"semantic_{code}"` 組出來的 6 個） | 9 | `semantic_dispatch_failed` |
   | drift | 3 | 併入 `protected_track_drift` |
   | **模組總計** | **48（盤點日）** | **21** |

   `f"semantic_{code}"` 那一行是這個毛病的原型：把「到底哪裡不對」編進 code，
   一個家族就自動長出九個字串，而且沒有任何一處按它們分岔。細節移到
   `diagnostic` 與 `CodexDispatchDiagnostic.code`——那兩處才是追問題會看的地方。

10. **雙讀砍掉的理由要寫下來。** `ResolveCanonicalTimelineAuthority.inspect` 以前
    把 state 與 baseline 各讀兩次再比對，用來抓「讀的當下有人在動 timeline」。
    那個 race 沒有人遇過，而且抓不抓到都不改變結果：baseline 會原封不動帶到
    `_resolve.prepare`，在**動手之前**再比一次指紋（`protected_track_drift`），
    中間漂掉照樣擋得住。多讀的那一次是每支 cut 都要付的 Resolve 往返。

## 階段 7 實作時量到的事（2026-09-12）

11. **`BLOCKING_DIAGNOSTICS` 當時是一張空名單。** v2 寫「其餘四條從未 fired，
    降為警告」的時候，以為那四條是硬擋。實測：它們四個在 `validate` 裡都是
    **提前 `return PolicyDecision("needs_review", …)`**，走不到 `decide()`；而
    `decide()` 只看得到走完全程收進 `notices` 的診斷。所以：

    * 名單裡那四筆對 `decide()` 是死的——看得到，永遠讀不到。
    * 唯一真的會經過 `decide()` 的硬擋候選就是章節卡投影，而 5bdc499b 把它從
      名單裡移掉了。

    也就是說那張名單看起來有四道防線，實際上一道都不在，而唯一該擋的那一條
    反而在放行。修修 2026-09-12 的裁決（升回 blocking）修的就是這一個。

12. **那四條不能照 v2 寫的「降為警告」處理，其中三條是前置條件。**
    `canonical_sections_missing` 的下一行就 `sections[0]`；`first_section_not_zero`
    餵章節卡的配對；`source_range_sum_mismatch` 的 `duration_sec` 是後面覆蓋率與
    節奏規則的分母。把它們降成警告不會讓系統更寬容，只會把 IndexError 推到更
    下游、訊息更難懂。

    所以分級是**三級**，寫在 `_policy.DIAGNOSTIC_GRADES` 一張表裡：

    | 級別 | 誰 | 為什麼 |
    |---|---|---|
    | `precondition` | `source_range_sum_mismatch`、`canonical_sections_missing`、`first_section_not_zero` | 後面的規則靠它才成立。不是政策，降不了級 |
    | `blocking` | `chapter_transition_projection_mismatch` | 會做出壞成品，而且人眼在 timeline 上看不出來（卡片對、字錯了） |
    | `warning` | 其餘 10 條（含 `title_placement_overlap`，它從硬擋降下來） | 修修逐支看 timeline 時看得見 |

    `BLOCKING_DIAGNOSTICS` 改成從表推導，不再是另抄一份名單——抄了就會像
    5bdc499b 那樣，兩邊各自漂走而沒有人發現。

13. **URL profile 砍掉之後，剩下的鎖要講清楚是哪三道。** 以前每個 provider 有一條
    「網址必須由 `provider_item_id` 組得出來」的 profile；Envato 併站那次就得再
    加一條，於是同一個判斷養著四條正則與兩套 item id 格式。而網址的**形狀**擋不住
    一個格式正確但指錯素材的網址，也擋不住平台下次改版。

    留下的三道：收據必須存在（sanitized source facts 那一圈）、授權字串必須是
    `_LICENSES_BY_PROVIDER` 兩個常數之一且逐字相符、檔案 bytes 的 sha256 必須對
    得上。第三道因此拿到自己的 code `asset_digest_mismatch`——它跟「reference
    綁錯」（`final_asset_identity_mismatch`）出事時的處置完全不同，不該共用一個名字。

## 階段 6 的範圍拆分（2026-09-12）

14. **diff 的資料源只有一個：run 的驗收歷史。** 一開始想從 plan record 的前後兩份
    去比，但 plan record 之間沒有先後——staging 目錄是 content-addressed，不帶時
    間，也不記前一份是誰。唯一有順序的是 `_ProductionRun.accepted_stage_history`
    （append 上去的），所以 diff 算在 `_correction._project_run_inspection` 裡，
    跟著 `RunInspection` 一起出來。

    「這一輪」＝走得最遠那一關（director → dp → visual_review）的最後一次驗收；
    「上一輪」＝同一關裡最後一次**已被取代**的驗收。第一輪沒有上一輪，全部標
    `added`。

15. **多加一格 `uniform_shift_sec`。** ADR 原本只要求列出「第幾秒、哪種卡、原文→
    新文」。實作時回去看 2026-09-09 punch-L04 那次才發現：逐條列出來救不了那個
    案子——34 個 event 每一條都是「移動 4.25 秒」，而 3 秒的位移在剪輯上完全正常，
    人不會逐條去比對它們是否恰好相等。所以由機器講出結論：**每一個移動過的 event
    位移都相同**時，把那個常數報出來。一個 event 被搬是判斷，34 個被搬同樣的距離
    不是。

    它刻意對「只有一個 event 移動」與「移動之外還有改寫」都不亮——亮了就沒有人
    會再相信這一格。

16. **Bridge 那一頁的接線與這一段分開。** 成品審核頁（`finished_review.html`）的
    event 不是直接讀 plan record，而是走
    `highlights/review/<cut>/events.json` → `build_finished_review_manifest.py` →
    manifest → 模板。要把 diff 帶上那一頁，得動 manifest builder 與模板，而依
    repo 的 UI 紀律，那一步要在跑起來的 Bridge 上實際走一次 golden path 才算完成
    （CI green 與 pytest 不算）。

    所以這個 commit 只到「diff 這個事實被算出來、被測住、而且讀得到」——
    `run_finished_cut_production.py inspect-run <command_id>` 現在就會印出
    `event_diff` 與 `uniform_shift_sec`。頁面呈現另開一次，連同瀏覽器實走。

## Review record

- **v1 三方審查（2026-09-12）**：創作者視角、cost×risk×complexity 審計、ADR-066 原作者辯護——三方一致「改了再簽」。審計重算數字並指出 v1 標「留」但該砍的七項；辯護人對 v1 標「砍」的八項各給出今天就會發生的失敗情境（`source_range_drift`、活字幕軌、master content hash、`_current_chain_is_exact`、retry base、ledger 三態、canonical 精確匹配、素材 bytes），並指出 journal 的承擔理由對錯對象。整合報告在該 session 的 `ADR-069-panel-report.md`。
- **owner 裁決（2026-09-12）**：放棄封存 Release；`_face_placement.py` 整刪；`chapter_transition_projection_mismatch` 升回 blocking。
- **owner 簽核 v2：2026-09-12「簽，直接做到完」。** 七個階段依序實作，每階段一個 commit。
