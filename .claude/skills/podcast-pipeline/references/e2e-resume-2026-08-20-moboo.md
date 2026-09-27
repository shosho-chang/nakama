# 抹布 E2E 重開機續接點 — 2026-08-20

這是 Codex 與 Claude Code 共用的 durable handoff。續接本集前必須先讀本文件，不能只憑對話摘要重跑。

**Operator rule：只讀 `.cache/simple-step7/AUTHORITATIVE.json` 內的 current paths；任何其他檔名
都不可手選，即使內容或 SHA 看似相同。**

## 2026-08-20 12:20 更新（取代下方歷史停機狀態）

- 正式 episode runtime 固定使用 `E:\nakama\.venv-v2`（Python 3.12）；
  `E:\nakama\.cache\moboo-test-deps-py314` 只可跑 isolated tests，不能跑 production episode。
- 本集 exact vocabulary 固定為：`抹布`、`Moboo`、`陳暐軒`、`高薪賽道`。任何
  `run`／migration command 都必須帶齊四個 `--vocabulary`；漏帶會在 sealed
  Reference retrieval policy 驗證時 fail closed。
- 重開機前未持久化的 audio audit 設定已從 Codex session command log 找回：
  `PODCAST_SUBTITLE_V2_AUDIO_AUDIT_MODEL=gpt-5.6-sol`、
  `PODCAST_SUBTITLE_V2_AUDIO_AUDIT_MODEL_VERSION=gpt-5.6-sol`。
- P0-A 已完成：新增 batch text source materialization；episode-shape benchmark 為
  2,630 spans / 36,825 cells / 7,889 signals / 526 packets，plan 27.701 秒、batch
  materialization 20.272 秒，四個全域 operation 各只執行一次；byte-identical regression
  已通過。
- P0-B 已完成：normalized audio 在 source validation 後立即進 CAS；recognition、speaker、
  coverage failure injection 都證明 staging=0。
- P1 已完成：production code identity 改為 package-wide Python source inventory digest；
  code-only drift 在 provider call 前 fail closed。
- 相關驗證：correction/text/selective/evidence-prefix 90 passed；production/code-drift
  11 passed；failure paths 7 passed；Ruff 與 `git diff --check` 通過。另一次完整整合檔在
  180 秒後 timeout，不能稱全檔通過；兩個直接相關 integration cases 個別通過。

### 現在的 active checkpoint

- Pointer：`G:\Footages\20260814 抹布\.subtitle-v2\transactions\active-create-checkpoint.json`
- `checkpoint_id`：`create-checkpoint-8aff555ee36c7849a86181b3689afede8c46dcd44982bbdee56d58b7dc0af376`
- `checkpoint_hash`：`f8ba472842a9853c7ed811e02422f70de1f01ee3cbdb22b99b1b52b49fe74dc7`
- `operation_key`：`fdc07a2efd0e032bbc2057f08792575bc33c5f32b88b723b10072e28e7a3a21e`
- Stage：`native_audit_basis_ready`
- Previous：`create-checkpoint-34dbbcf038477b14891e83d8fd7fb341e8b63bf72961104c48d2e7303e2a8d2d`
- Previous 是合法 sequence 4 evidence-prefix refresh；receipt ID
  `f2568080d53f000b15f0147047e26a1522fd6d83362fc9444eb2232a4decaec3`，舊 evidence bytes 未改。
- Basis：16,233 artifacts，其中 16,224 text sources，總計 307,232,594 bytes；staging=0，
  尚未建立 subscription work packets，尚未呼叫 text/audio subscription worker。

### 最新 profiler finding 與唯一新 blocker

真實 resume 的 60 秒 `cProfile`：82,000,255 calls；`hash_file()` 66,549 次／33.302 秒，
`GenerationStore._load_create_checkpoint_directory()` 69 次／32.290 秒，
`_load_native_model()` 兩次／23.942 秒。根因是同 process 每讀一個 model artifact 都重新
hash 整個 16k-artifact checkpoint；不是字幕演算法或 subscription worker 慢。

曾實作一個 `GenerationStore.read_create_checkpoint_artifact()` process-local cache prototype：
每個 store instance 第一次 open 仍完整驗證 checkpoint，後續只重 hash requested artifact；
requested-artifact tamper、fresh store/process 都保持 fail closed。Checkpoint + evidence migration tests
24 passed，Ruff 通過。但該 prototype 會讓 active `native_audit_basis_ready` 發生 code-only drift，
而 formal basis migration 的成本高於本集可接受範圍，因此已完整撤回，未進 production episode。

撤回後 exact production module code hash 已重新驗證為
`ee03c1e5d3aaaba2b1ca750df1e0500a5ca47cddf4e40c585f86d9ab780fd4d7`，與 active basis 一致。
可以續跑同一 `run`；預期每個 fresh-process resume 因 16k 小檔完整驗證需約 5–6 分鐘，期間
CLI 可能沒有輸出。至少每 60 秒讀 process CPU/RSS 與 active pointer；不要在第五分鐘剛到時中止，
本集 basis 就是在約五分鐘邊界完成 commit。若 CPU/RSS 不再增加、process 消失、或超過 8 分鐘仍
沒有 `Interrupted`／新 checkpoint，才停止並回報。

12:24–12:32 的最後一次真實 resume 已用 exact matching code identity 重跑。8 分 18 秒時
worker CPU 486.703 秒、RSS 1,443,438,592 bytes，active pointer 仍完全沒有前進，因此已中止；
中止後 process=0、staging=0、packets=0。這推翻「接受 5–6 分鐘即可續跑」的暫定判斷。

store verified-session cache 與 append-only `native_audit_basis_ready` code-only migration 現在是
本集的 P0 blocker：沒有 cache，每次 resume 超過 8 分鐘；直接套 cache 又會造成 code drift。
在兩者一起完成並通過 tamper／fresh-process／basis-artifact-preservation tests 前，不得再跑真實
`run`。禁止改 pointer、偽造 code hash、重跑 Memo，或把 cache prototype 偷塞進 runtime。

P2（migration ledger 尚未進最終 Generation provenance）仍是下一 release gate，未修。

## 停機結論（歷史：已被 12:20 更新取代）

- 可以安全重開機。
- 沒有 Podcast Subtitle V2 背景程序在執行；最後一次真實 Stage 7 run 已在五分鐘 hard timeout 後停止。
- `G:\Footages\20260814 抹布\.subtitle-v2\audio\staging` 目前是空的（0 entries）。
- `G:\Footages\20260814 抹布\.subtitle-v2\subscription-work` 目前沒有 packet files（0 files）。
- 不要自動重啟 Stage 7，不要進 Stage 8，不要上傳新影片。
- 目前修改尚未提交，因此不得稱為 production-ready。

## 唯一工作位置

- Worktree：`E:\nakama\worktrees\moboo-e2e`
- Branch：`codex/moboo-e2e`
- Episode：`G:\Footages\20260814 抹布`
- Canonical mix：`G:\Footages\20260814 抹布\Audio\Live-Mix.wav`
- Host mic：`Audio\1_COMBO-1.wav`（修修）
- Guest mic：`Audio\2_COMBO-2.wav`

工作樹是 dirty worktree。不得 reset、checkout 或清掉 untracked files；所有既有修改都要保留。

## Active checkpoint（歷史：已被 12:20 更新取代）

- Pointer：`G:\Footages\20260814 抹布\.subtitle-v2\transactions\active-create-checkpoint.json`
- `checkpoint_id`：`create-checkpoint-4a3ee27871bb874eb4262e9e528ba93eaa41c61f47ff5d85dda1390151854c6d`
- `checkpoint_hash`：`22e240f1d6108fbafc0236079bc46a2b46fc5df54531e40b6bf7a757c5b28abd`
- `operation_key`：`116b32ef7a53e76a9ca7363c0114c146198c4be98b74938311800be17a8535df`
- Stage：`evidence_ready`
- Checkpoint directory：`G:\Footages\20260814 抹布\.subtitle-v2\create-checkpoints\create-checkpoint-4a3ee27871bb874eb4262e9e528ba93eaa41c61f47ff5d85dda1390151854c6d`

若 pointer、hash、operation key 或 stage 任一不符，立即停止；不可自動 rollback 或另開新 run。

## 已驗證的 immutable upstream evidence

下列檔案在停機前均存在：

- `G:\Footages\20260814 抹布\normalized-handoff.v1.json`
- `G:\Footages\20260814 抹布\subtitle-v2\memo-recognition.v1.json`
- `G:\Footages\20260814 抹布\subtitle-v2\memo-recognition.composite.execution.srt`
- `G:\Footages\20260814 抹布\subtitle-v2\memo-recognition-acceptance.v1.json`
- Cue source 是同一份 `memo-recognition.composite.execution.srt`
- `G:\Footages\20260814 抹布\subtitle-v2\memo-cue-acceptance.v1.json`

Recognition/cue quorum 已通過：2,630 cues，非正 duration 0，overlap 0。已修補的約 7.264 秒缺口沒有 catastrophic blocker；可疑同音字與語助詞屬 S5 text audit，不得回頭重跑 Memo。

## 已完成並有測試證據的工作

1. Memo bundled runner、SRT deterministic repair、VAD gap repair 與 recognition/cue receipt 已接入。
2. Selective Audio V3 規格已改為：全集 deterministic/text/reference audit；音訊只跑 risk-targeted + 10% sample，異常才擴至 30%，再達門檻才 full audit。
3. Evidence identity migration 改成 append-only V2 ledger，已支援多次 refresh；migration 測試 10 passed，migration/CLI/checkpoint/production 57 passed。
4. Reference retrieval、audit plan、candidate generation 與 correction execution 的多個全量重算熱點已改為共享索引／快取。
5. Episode-shape benchmark（只到 execution-plan build）結果：36,825 cells、5,391 signals、526 packets；plan 15.88 秒，audit-to-plan 22.20 秒。
6. Speaker sweep 與 Recognition interval index 各 2,000 組 randomized differential cases 均與 brute-force 語意一致。
7. Identity mismatch 路徑的 audio snapshot leak 已修；相關 lifecycle/migration/audio tests 16 passed。
8. 過去遺留的五份完整 CAS-identical staging snapshot 已移到可復原 quarantine：`E:\nakama\.quarantine\moboo-audio-staging-20260820`。不要在續接時刪除。

## 尚未修完：禁止直接續跑的 P0（歷史：P0-A/P0-B/P1 已修）

### P0-A — 526 packets 逐包重播完整父資料

真實 Stage 7 在 `agents/brook/podcast_subtitles/module.py` 約 4969–4985 對 526 個 text packets 逐一呼叫 `materialize_text_correction_packet_sources`。

每包都在 `correction_execution.py` 約 2172 起重新：

- serialize + Pydantic parse 整份 526-packet execution plan；
- hash Canonical Transcript、36,825-cell AuditPlan、Recognition Evidence、2,630 retrieval receipts；
- 再做一次完整 artifact identity hashing；
- 重建全域 token/cell/span/binding indexes。

複雜度目前是 `O(P × (A + S + R + P))`。至少會做 `526 × 36,825 = 19,369,950` 次 cell traversal，AuditPlan 約完整序列化 1,052 次。這是最後一次 run 超過五分鐘、單核滿載與約 1 GB working set 的最高可信主因。

既有 22.20 秒 benchmark 只量到 plan builder，沒有量 526 packet full materialization，因此不能拿來宣稱 Stage 7 已修好。

修法：建立 batch materialization context；全量 plan validation、exact-input hashes、artifact identities、binding indexes、packet map 各只算一次。現有單包 API 保留為 wrapper。新增 episode-scale full-materialization benchmark，以及 counters 證明全量運算每批只執行一次；小 fixture 新舊輸出必須 byte-identical。

### P0-B — fresh create 的 normalized snapshot 仍可能洩漏 1.2 GB

`module.py` 約 7354 建立 `normalized_snapshot`，現有 cleanup scope 約在 7364 就結束；Recognition、speaker attribution、projection、coverage 到約 7559 才完成，snapshot 約 7560 才 commit。中間任何 exception 都可能留下完整 normalized WAV。

修法：從 snapshot 建立到 commit 使用單一 ownership scope；只有成功 commit 才解除 cleanup。新增 recognizer、speaker、coverage 各自拋錯時 staging 必須為空的測試。

## Independent review 的其餘 findings

### P1 — production `code_hash` 是固定字串，不是真實 source/release identity

`module.py` 約 2785/2887 的 `code_version` 預設為 `podcast-subtitle-v2`；`production.py` 約 316 沒有注入 source-tree/release digest。只改 orchestration code 時，舊 checkpoint 仍可能被誤判為同一 code identity。

在下一次真實 run 前，production factory 必須注入可重現、涵蓋 module/store/checkpoint/selective orchestration 的明確 release/source digest，並測試「只改 Module identity」會在 provider 前 fail closed。若 identity 改變，依法 append 一次 evidence-only identity refresh；不得覆寫舊 evidence。

### P2 — migration ledger 未進最終 Generation provenance

Terminal checkpoint 會驗證完整 migration ledger，但 Generation artifact set 沒保存 ledger root/receipts。至少把 content-addressed migration-ledger root hash 納入 Generation；較佳做法是一併保存 receipt bytes 並納入 manifest stage hashes。

### 已量化但不是第一主因的成本

- Resume 前 WAV hashing 約 4.57 GiB，若含 CAS verify 約 5.71 GiB。
- `_bind_speaker_tracks` 對每支約 613 MB mic 重複 hash。
- Checkpoint artifacts 也有重複 rehash。
- Production commit/materialization 的大量 bounded JSON + fsync 沒包含在 22 秒 benchmark。

只有 P0-A/P0-B 修完並通過 full-materialization benchmark 後，才評估上述次要成本；不得再盲跑真實 pipeline 找熱點。

## 重開機後的固定執行順序

1. 只讀核對 worktree branch/status、active pointer、checkpoint stage、staging=0、packet files=0、沒有 pipeline process。
2. 先修 P0-A batch materialization，建立包含全部 526 packets 的 episode-scale benchmark；不得碰 provider 或 G 槽 production state。
3. 修 P0-B snapshot ownership，補三個 failure-injection cleanup tests。
4. 修 P1 production code identity，補 provider-before-drift rejection test。
5. 補 P2 Generation migration provenance，或在文件中明確判定為下一個 release gate；不可靜默略過。
6. 跑 targeted tests、episode-scale benchmark、完整相關 regression、ruff、`git diff --check`。
7. 只有所有 P0/P1 gate 綠燈後，依新 code identity append evidence refresh；不得重跑 Memo。
8. 再做一次最多五分鐘、stage-level instrumented 的真實 Stage 7 resume。若失敗或 timeout，停止 process、確認 staging=0、回報各 stage wall time/RSS；不要連續重試。
9. Stage 7 產生且驗證 work packets 後才進 Stage 8 subscription workers。不得自動上傳 YouTube。

## 重開機後第一個安全步驟

先開終端並執行：

```powershell
Set-Location 'E:\nakama\worktrees\moboo-e2e'
git branch --show-current
git status --short
Get-Content -Raw -Encoding utf8 'G:\Footages\20260814 抹布\.subtitle-v2\transactions\active-create-checkpoint.json'
```

預期 branch 是 `codex/moboo-e2e`，pointer 必須仍指向本文件記錄的 checkpoint。接著重新讀本文件與 `SKILL.md`，從 P0-A 開始，不是從 Auphonic 或 Memo 開始。

## Definition of Done（下次可以真的續跑前）

- Full 526-packet materialization 不再逐包 hash/parse/index 全量父資料，且有 counters + benchmark 證據。
- Fresh-create 任一 pre-commit exception 後 staging 都是 0。
- Production code identity 真實、可重現、會對 code-only drift fail closed。
- Targeted + regression tests、ruff、diff check 全綠。
- Active checkpoint identity 已用 append-only receipt 合法刷新。
- 重啟真實 Stage 7 前先向使用者回報預期成本與 hard timeout；遇到異常立即停止並回報。

## 2026-08-20 historical strict-v3 base — SUPERSEDED

Formal V2 的 store verified-session cache 與 append-only basis code migration 已完成實作與測試，
但對真實 16,233-artifact basis 的 migration 在五分鐘 bounded run 仍未完成，已依單集時限中止。
中止後 active pointer 仍是
`create-checkpoint-8aff555ee36c7849a86181b3689afede8c46dcd44982bbdee56d58b7dc0af376`，
checkpoint hash 仍是 `f8ba472842a9853c7ed811e02422f70de1f01ee3cbdb22b99b1b52b49fe74dc7`；
process、staging 與 subscription packet 均為 0。不得再重試 formal migration，也不得改寫 pointer。

使用者明確要求改採較簡單做法後，兩個獨立 text audit 經 strict cue/original/timestamp validation 與
fail-closed safe-category allowlist、generic actual cue-count validation 與三輸出 preflight 合併。
任何 destination 衝突都會在建立其他輸出前停止，ledger 最後寫入作 commit marker。以下是
strict-v3 作為 Arbitration import base 時的歷史 pointer snapshot，已被 final-v2 supersede：

歷史 `AUTHORITATIVE.json` SHA-256 是
`c2aed2baf8171b8123d109f848d483fa36f83215e07f00d7030e350fd30a7e5d`；它不是可操作的 pointer。
Audit original 與
timestamps 必須 raw exact match source；A/B proposal 的一致性定義是 NFKC + whitespace
normalization 後 exact match，不是 raw string exact。

- `strict-v3-corrected.srt`：SHA-256
  `37b9bfb93c7515ac54e5e4c80d619fa573b59f090e38c1c45ae3ffe2163b6b6f`；
- `strict-v3-consensus-ledger.json`：SHA-256
  `15d7c11d5ef9b714688d490954e8098a4b16b235389b03743d1db3fe21dc613d`；
- `strict-v3-needs-audio.json`：SHA-256
  `b6770dc97696a6b793971d05d80c3da568f6ac15e6fa5d7869cb726258bee415`。

輸入 Audit A SHA-256 是
`0ef6dd4ff63711cdabf93357cf16db0d8a56b6ce819404271c843a302a8bf523`；Audit B 是
`23809a1ed09ed7f9e2095234e4379979ce07cdb61926a6d22e74e5c73876be01`。合併結果為
34 個 auto-accepted cues、74 個 needs-audio components／82 個 cue IDs；corrected SRT 仍是
2,630 cues、non-positive duration 0、overlap 0，fresh rerun byte-identical。舊 unprefixed 與
`strict-*` 與 `strict-v2-*` 三檔全部 superseded，不得再作 current input。

Targeted Memo 對四組重大／衝突風險的 adjudication 在
`.cache/simple-step7/memo-targeted-adjudication.json`，SHA-256
`af31a6dd74734aef4579ba292aa4bdfeddb182091a63a53d5d6f98ce026f7a9e`。四組全部維持
`keep_original_unresolved`；這只是同一 Memo ASR 的 targeted rerun，**不是 independent audio
quorum**，不得拿它接受重大數字、否定或跨 cue 修正。該歷史 artifact 內綁的 Audit A hash 是
timestamp exact-sync 前的 `0a9b6eb7cfb22058f997c3fa21f48b74f9b387d862cea19fe0821322e55ec6d7`，
不是 timestamp-exact Audit A；因此只能保留作歷史 corroboration，不能宣稱已 adjudicate final-v2 queue。

下一步是由獨立 Arbitration C 處理 82 個 queued cue IDs，優先四組未解重大風險；若沒有真正
audio-capable worker，必須保留 unresolved，不能由文字或同一 Memo rerun 偽裝聽音通過。以上全部是
`degraded-simple-step7`，不是 full V2 completed、不是 Verified Projection、不能寫入正式
checkpoint／Generation，也不能據此進 YouTube upload。

## 2026-08-20 strict Arbitration C import（SUPERSEDED historical snapshot — DO NOT USE）

已用 explicit `apply-arbitration` mode 將 `arbitration-c-v3.json`（SHA-256
`04f6b835b4f9c4501efd74ff9fc67bd85575f64e3935d8b6a01567f3f4d4c11b`）匯入 strict-v3
base。Importer 會重新驗證 source SRT、Audit A/B、base corrected／ledger／needs 的 exact hashes，
要求 74 個 arbitration items 一對一覆蓋 base needs components，並 fail closed 阻擋重大風險、
低信心、空 replacement、數字／否定／漏增字／damaged／proposed-null，以及 cue／original／hash
漂移。多 cue replacement 必須逐 cue 一行且行數完全一致；三份輸出先完整 preflight，ledger 最後
寫入作 commit marker。

Arbitration C 接受 15 個 components、保留 59 個 unresolved components。加上 strict-v3 原有 34 個
base accepted components，final 共 49 個 corrected components；其中 cues 2492–2494 是一個
3-cue component，因此相對 Memo source 實際變更 51 個 cue IDs。Final SRT 仍為 2,630 cues、
non-positive duration 0、overlap 0，fresh rerun byte-identical。以下三檔是已淘汰的歷史輸出：

- `.cache/simple-step7/final-v1-corrected.srt`：SHA-256
  `2d972f40988c16f28f3b0ac3b6da72247bb32699afe75b1f8610ebdc769ea114`；
- `.cache/simple-step7/final-v1-ledger.json`：SHA-256
  `35aaf800f1471fd773f555065b89121f2e81be7522ec9eeb4ee03119508c2f2f`；
- `.cache/simple-step7/final-v1-unresolved.json`：SHA-256
  `947524a96d135ae0570ac96c3c958f3d6614ab63f01f921257a3e47a4406acd3`。

歷史 `AUTHORITATIVE.json` snapshot（SHA-256
`82fb6ccc800dbff8456d4f3fb86d8346acdc16f50bf047c26668edccd5e26a43`）曾指向上述 final-v1 三檔；
它已被標為 `superseded_due_to_P0_untrusted_arbitration_proposal_authority`，不可操作。剩餘 59 個
unresolved 尚未有 independent audio quorum；因此 final-v1 也是
`degraded-simple-step7`，不是 full V2 completed、不是 Verified Projection、不是 upload-ready，
不得寫正式 checkpoint／Generation，也不得上傳 YouTube。下一步只能做這 59 組的獨立音訊仲裁，
或回到修復後的 formal V2；不能把未解項目視為已通過。

## CURRENT AUTHORITATIVE degraded artifact — final-v2

Independent review 證實 final-v1 importer 曾信任 Arbitration JSON 自宣告的 `a_proposals`／
`b_proposals`，且 base 只驗 ledger/hash 自洽；因此 final-v1 三檔已立即標成
`superseded_due_to_P0_untrusted_arbitration_proposal_authority`，不得再使用。

Final-v2 importer 會先由 exact source SRT + raw Audit A/B fresh 重跑 `merge`，要求 supplied strict-v3
corrected／ledger／needs 三份 bytes 完全一致。再以 base needs 每筆 lineage 的 agent、collection、index、
audit hash、canonical finding hash 與 finding payload 回查 raw audit record，derive ordered A/B proposals 與
B risk metadata；Arbitration fields 必須 raw exact 相同。Accepted replacement 只能 raw exact 等於 derived
single proposal、A multi-cue 合併 proposal，或 B 逐 cue proposals 的 newline 合併；Arbitration JSON 不能
自行新增候選。A、B、單邊與 multi-cue proposal injection，以及同步偽造 corrected＋ledger hashes 的
regression 均會 fail closed 且不建立任何 output。

`AUTHORITATIVE.json` 唯一可操作的 paths：

- `.cache/simple-step7/final-v2-corrected.srt`：SHA-256
  `2d972f40988c16f28f3b0ac3b6da72247bb32699afe75b1f8610ebdc769ea114`；
- `.cache/simple-step7/final-v2-ledger.json`：SHA-256
  `762451e2458f98273ef2011dc7f14efde0253c2e48f2843ac1ca7822957d7a30`；
- `.cache/simple-step7/final-v2-unresolved.json`：SHA-256
  `1ee169611f1ee8d84d681fc68d4fc81a20b340f0fcb66980e70df77e9ff8d96b`；
- canonical `.cache/simple-step7/AUTHORITATIVE.json`：SHA-256
  `15f3ee20ed7876b3fd3497cd8487cb50d5077362cd35b7918a0f46e4a7240eb1`。

真實 import 結果仍為 Arbitration 15 accepted／59 unresolved、final 49 corrected components／51 changed
cue IDs、2,630 cues／non-positive 0／overlap 0，fresh rerun byte-identical。Final-v2 仍是 degraded、
不是 full V2 completed、不是 Verified Projection、不是 upload-ready；59 unresolved 的 gate 不變。

## 2026-08-20 CURRENT supervised degraded audio release candidate — release-v1

正式 Faster full-episode bounded run把 25/25 raw chunks封存後，揭露兩個 production adapter gap：
provider segment.start 不一定包住首字（真實 31 筆全部為首字跨入 segment start），以及相鄰 bounded
chunks 的 midpoint ownership 仍可能產生 12 個 word overlaps；24/24 shared-context seam hypotheses 也非
raw-exact 相同。因此 full run保持 fail-closed，沒有產生或冒充 full-episode Recognition Evidence；
formal G checkpoint／pointer仍完全未碰。首字 topology validator 已以 regression tests修成只接受跨入
segment start 的首字，非首字、完全落在 segment 前、超過 end 與全域 overlap仍拒絕。

依使用者要求的簡化規格，使用本機 Faster-Whisper large-v3 與 Qwen3-ASR 1.7B 兩個不同模型家族，
對 32/32 `major_risk=true` components 完成 bounded PCM clip recognition；每段都有 exact clip hash、
raw output、Recognition Evidence 與 replay verification。第一輪 13 components 接受 6 組，第二輪剩餘
23 major components 接受 5 組；其餘 major 衝突保留 source text。另 23 個未做 audio 的 non-major
components依明示 policy保留 source text，不作語意猜改。

Current release artifacts：

- `.cache/simple-step7/release-v1-corrected.srt`：SHA-256
  `8cf28558050e9c5d7cf4fbbcfa430fda9ba534acf20297ac7f4a0b49a674681c`；
- `.cache/simple-step7/release-v1-ledger.json`：SHA-256
  `c83ce46391192b1f6766e6fa9e38ef0c554a23f97c6454214a411e3e6dee19d2`；
- 第一輪 `.cache/simple-step7/audio-final-v1-ledger.json`：SHA-256
  `fa123fb3a7552cf9ad06cefb98dbfc13d2187a3e36bb2da661c53af906f17f15`。

相對 final-v2，第一輪改 cues 444、599、859、1390、1529、2092；第二輪改 cues 8、13、27、28、
362、2131。Release SRT仍為 2,630 cues、non-positive duration 0、overlap 0；兩個 finalize commands
fresh rerun byte-identical。Focused verification為 52 tests passed、Ruff passed、diff-check passed。

這是 `degraded_dual_asr_major_complete_not_full_v2_checkpoint` supervised release candidate，不是 full V2、
不是 Verified Projection，不得寫回正式 checkpoint／Generation。下游只能讀 export manifest 指定的
release-v1 paths，禁止手選 final-v1、final-v2、strict 或其他 superseded檔；進 Resolve 需要明示 degraded
handoff，不得宣稱 canonical V2 production success。YouTube仍未授權自動上傳。

Portable export 已 byte-verified 到
`G:\Footages\20260814 抹布\subtitle-v2\degraded-audio-release-v1`，共 181 files；
`EXPORT-MANIFEST.json` SHA-256 是
`e8a4cabddbcd9f73c3c360724557847a4a97569d7d285bab933eb907d8ef52fa`。Canonical bundle path 是
`release/release-v1-corrected.srt`；所有 34 個 bounded clips、Faster/Qwen raw outputs、Recognition
Evidence、兩輪 ledgers 與 deterministic operator scripts 均已隨 bundle 保存。相同 export command
idempotent rerun通過。

Episode-local supervised Stage 5 request 已放在
`G:\Footages\20260814 抹布\subtitle-v2\degraded-audio-release-v1\STAGE5-HANDOFF.json`，
SHA-256 `3424aac9286d477d79005dbdd7ed15dc146d5ccb036b98b2272ff369500a734e`。它明示
`degraded_dual_asr_major_complete_not_full_v2_checkpoint`，綁定 release SRT、release ledger、export
manifest 的 relative paths／sizes／hashes與 32/32 major audio review gates；不得把它改名或轉成
Verified Projection receipt。

2026-08-20 22:03 +08 已用新 `degraded-dual-asr-v1` Stage 5 selector 跑真實 episode dry-run，exit 0，
未啟動 Resolve、未寫 G 槽。計畫確定選到 `Default_2026-08-14_1.mp4`（1920x1080／30fps）、
`normalized.wav`、三個 camera、Combo 1／Combo 2／Live-Mix，以及 canonical release SRT SHA-256
`8cf28558050e9c5d7cf4fbbcfa430fda9ba534acf20297ac7f4a0b49a674681c`；handoff／ledger／manifest
SHA-256 也與上方 release bundle 完全一致。Stage 5 selector 已移除 32／23／2630 的單集硬編碼：
未來 episode 以 handoff gates 為數值真相，但仍強制 major reviewed == major total、timing 0／0、
rerun true、ledger/gates/actual SRT exact match。下一個人類 gate 只是在真正 build 前開啟 Resolve Studio；
highlight mining 可以先使用同一 `--degraded-release-handoff` 在 GUI 外進行。

2026-08-20 晚間 Stage 5／highlight gate 已推進完成：

- `build_resolve_project --dry-run` 已 exit 0；完整 Stage 5 focused regression為 32 passed。
- 三位獨立 miner 讀完整 release SRT、訪綱與 report，合併後為 16 long／23 short；
  `run_highlight_cut --validate --degraded-release-handoff ...` 保留全部候選、22 variant groups、
  band issues 0。Current `candidates.json` SHA-256 是
  `d286e4132ab1706f8562dd030613e9060a9047875f2783fceece89e85fe1f9cd`。
- 三位 longform persona、brand lens、Renee lens 已完成。舊 Kevin raw 因 18 個錯誤 citation 整份
  作廢；fresh blind rerun 的 112/112 structured quotes 與 16/16 scores 通過第二次 QA。
- `G:\Footages\20260814 抹布\highlights\選段候選表.md` SHA-256
  `05f228e15153976e5072f55de39e2878ebf8ceb22197eb4c6140f7e7fedf0c19`；完整報告
  `TA審稿回饋-抹布長highlight-2026-08-20.md` SHA-256
  `951403defa160eb33c0129e2e930e437bfb2d8e2612f33ec9d0191de37631c05`。
- Ranked long groups目前為 L09 LinkedIn（91）、L12單位時間（89）、L15 AI判斷（85）、
  L02第一桶金（84）、L16留學求職（84）、L01內容創作（61）。L01有 brand veto：正式開錄前
  cue 82–151不得使用，除非另有來賓明確授權。
- `run_cut_shortlist.py` 曾依文件 direct-run 時觸發 `ModuleNotFoundError: shared`；已補 repo-root
  bootstrap並新增 direct `--help` regression，`tests/test_cut_shortlist.py` 10 passed、Ruff通過。

2026-08-21 07:32 +08 已主動啟動 Resolve Studio，並用 `py -3.10` 真正執行
`build_resolve_project.py --degraded-release-handoff ...`。第一次誤用 `.venv-v2` Python 3.12，在印完
plan、載入 Fusion API 時 exit 1；最小 probe 證明 Python 3.10 可連到 DaVinci Resolve Studio
21.0.3.7，之後 build exit 0。Project 與 timeline 均為 `20260814 抹布`；實際 API fresh check 為
1 video track／1 audio track／1 subtitle track，V1 是 `Default_2026-08-14_1.mp4`、A1 是
`normalized.wav`、字幕 2630 items。Timeline 由 `E:\nakama\data\resolve\subtitle-template.drt`
建立，字幕已自動上軌；episode-local exact-copy 顯示檔是
`G:\Footages\20260814 抹布\subs\resolve_subs\transcript_r001.srt`。

Normalized audio 的 Jingle gate 也已 fresh 核對：production receipt 是 `trim_jingle=true`、
`jingle_seconds=6.0`；source 與 final normalized duration 都是 4257.238 秒，final 頭 20 秒對 source
在 0.000 秒對齊（normalized cross-correlation peak 0.86），所以 final 沒有額外頭部 Jingle，尾端也
沒有超出 source clock。現有 receipt 沒保存原始 Auphonic download hash／實際 trim offset，因此只能
證明 final clock 已正確對齊，不能追溯這次是 Auphonic 未加 Jingle 或下載後 `_align_trim` 已移除。

目前安全停在選段 gate，尚未寫 `winners.json`、未替使用者選 top、未 materialize。現在唯一需要
使用者做的選擇是 long candidate ID（預設可挑 1–3 支）；Resolve Studio 與主 episode timeline
已就緒。收到 IDs 後先跑 `run_cut_shortlist.py --pick ...`，其後所有 Resolve／highlight CLI持續帶
同一個 degraded handoff。

## 2026-08-21 抹布 episode 特殊處理與新 production 決策

本集不得作為下一版正式字幕流程的乾淨 E2E acceptance fixture。字幕 release 本身可用，但影片
Program Feed `G:\Footages\20260814 抹布\Default_2026-08-14_1.mp4` 有來源 bitstream 異常：

- Resolve timeline clip 沒有被截斷：0–127,732 frames，30 fps，完整延伸到約 70:57；
- ffmpeg 可重現 H.264 `Invalid NAL unit size`／`Error splitting the input into NAL units`；
- 一秒 seek probe 的壞點集中在約 01:00:13–01:00:52，使用者最先看到 Media Offline 的
  01:00:42 正落在這個區間；
- Program Feed 內嵌 AAC stream 只到 3660.401 秒，與 video 4257.733 秒不一致；
- `Video\1_CAMERA 1.mp4`、`2_CAMERA 2.mp4`、`3_CAMERA 3.mp4` 在 3600／3630／3640／3650／
  4000／4240 秒的 `ffmpeg -xerror` probes 全部 exit 0，均可作修復來源；
- Timeline A1 是獨立 `normalized.wav`，因此 Program Feed 內嵌音訊異常不影響目前正式聲音。

使用者先自行檢查原檔／備份；在來源結論前不要覆寫 Program Feed、不要轉碼冒充修復，也不要重建
字幕。若確定原檔同樣損壞，安全剪輯策略是用健康 Camera 檔覆蓋壞 GOP 區間或重建該段 program cut。

同日使用者裁決：不再把 Formal Subtitle V2 Stage 7（checkpoint basis migration、526 correction
packets、Canonical Generation、Semantic Units、Verified Projection、ordinary 10%／30% sampling）
作為 production default。新的正式字幕路徑要升格目前的 Memo dual-audit simple release：Memo cue
authority → deterministic QC → 兩個獨立 text audits → strict consensus／Arbitration → 所有 major-risk
bounded clips 做 Faster-Whisper＋Qwen3-ASR 雙模型稽核 → 衝突與 non-major unresolved 保留 Memo
原文 → hash-bound release SRT／ledger／manifest／Stage 5 handoff。舊 Full V2 code 與 checkpoints
保留為 explicit legacy forensic，只能讀取，不得由 production default 呼叫。

抹布目前 `degraded_dual_asr_major_complete_not_full_v2_checkpoint` artifacts 不原地改名、不改 bytes，
只作 backwards-compatibility fixture。新正式流程必須用新的 production contract／handoff mode，在下一集
做乾淨 E2E；從可識別 episode 啟動命令一路自動跑到第一個人工 gate：Highlight shortlist review。
Only wrong episode/audio、hash／coverage／timebase catastrophic failure 可以提前停止。本段只記錄
cutover 前的決策時點；當前 production authority 以下方 2026-08-21 activation record 為準。

## 2026-08-21 ADR-063 implementation handoff and activation record

新的正式 contract 名稱已凍結：

- request：`podcast-subtitle-memo-dual-audit-release-request-v1`；
- release：`podcast-subtitle-memo-dual-audit-release-v1`；
- export：`podcast-subtitle-memo-dual-audit-release-export-v1`；
- audio decisions：`podcast-subtitle-memo-dual-audit-audio-decisions-v1`；
- status：`podcast-subtitle-memo-dual-audit-release-status-v1`；
- Stage 5 handoff：`podcast-subtitle-stage5-memo-dual-audit-handoff-v1`；
- Stage 5 mode：`memo-dual-audit-v1`；
- default root：`<episode>/subtitle-release/memo-dual-audit-v1/`；
- default handoff：`<episode>/subtitle-release/memo-dual-audit-v1/STAGE5-HANDOFF.json`。

字幕 runner 是 `scripts/podcast_subtitle_release.py`，提供 `init`、`status`、`seal`、`finalize`、
`verify-legacy`。`init` 是唯一可建立 typed request 的入口；`seal` 綁定已出現的 actual input bytes。
`status` 的 pending exit code 是 3，ready／complete 是 0，fatal contract／drift 是 2。
Stage 5 consumers 預設發現上方 handoff，也接受明示 `--subtitle-release-handoff`；formal、legacy／
degraded 與新正式路徑互斥，舊路只能 explicit 使用。

2026-08-21 獨立 QA 已對 code／schema／consumer、skill routing 與 relevant regression 發出
**CODE CUTOVER GO**：targeted 38 passed、focused 249 passed、Ruff PASS、CLI help PASS、
`git diff --check` exit 0，且 P0 = 0、P1 = 0。ADR-063 現為 **Accepted / Active**。這不表示
新集 clean operational E2E 已跑；下一集從 Auphonic 到 Highlight shortlist review 的 smoke 仍待執行。

抹布仍維持以下特殊規則：

- 不改名、不改 bytes、不重建既有 `degraded-audio-release-v1` bundle；
- 既有 Resolve project／timeline 繼續綁原 handoff；
- source Program Feed bitstream fault 另案處理，不能算新字幕 contract 的失敗；
- `verify-legacy` 只能證明舊 bundle 的 exact integrity，不能把它升格成乾淨 acceptance fixture；
- 下一集才是新正式 runner從 Auphonic、Memo 一路自動走到 Highlight shortlist review 的 clean E2E。
