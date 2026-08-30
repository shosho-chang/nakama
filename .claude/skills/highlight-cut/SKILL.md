---
name: highlight-cut
description: >
  訪談集精華選段：整集 transcript（說話者已切）開採長片（8–12min 橫式 YT）與
  短片（60–120s 直式 Shorts）候選段落，persona 盲審評分排名 → **停下來給修修挑**
  （Step 2.4 gate）→ 物化成 Resolve timeline + marker + 選段企劃報告。
  Use when the user says 「選段」「切精華」「highlight」「剪短影片段落」,
  or after resolve-project completes in the podcast pipeline.
  Step 6–11 製作線只涵蓋短片；長片製作線在 longform-cut skill。
---

# highlight-cut — 訪談集精華選段

設計凍結：`docs/plans/2026-07-25-highlight-cut-plan.md`（grill Q1–Q7）。
**不走 paid API**：miner 與 persona 使用已設定的 Codex／Claude Code subscription workers。

### Semantic model routing

完整 SRT 的 miner／long structure pass 是本 skill 最需要語意理解的工作，模型由 host runtime 選擇：

- Codex：`gpt-5.6-sol`，reasoning `high`。
- Claude Code：優先 `claude-fable-5`；訂閱或當次 runtime 不提供時用最新 Opus。
- 其他平台不得猜 model；沿用 host 的 frontier model，並在交付時明示實際 model。

skill 不呼叫 repo 的 API `llm_router`，也不把單一供應商 model 寫進 Python。這讓同一份 skill 在不同
platform 依 host 自動選擇，而 schema 驗證、merge、Resolve 等 deterministic 工作不浪費 frontier model。

## ⛔ 已停用：Stage 5 Long Highlight orchestrator（ADR-065）

> **`run_long_highlight_orchestrator.py` 已停用，不要照著跑。**
> long 的生產唯一路線 = **ADR-066 Finished Cut Production**：
> `scripts/run_finished_cut_production.py`（`register-approved-cut` / `advance` /
> `request-revision` / `cutover`）。`AcceptedStage` 是唯一語意權威，
> `state.json` 已降級為可重建的 view；細節見 `longform-cut` skill。
>
> 以下保留為歷史說明，供讀舊 receipt／舊 state 時對照，**不是操作指示**。
> 後面的 Step 1–5 strict commands 同樣只供 forensic／migration。

`DirectoryStageRunner` 是 host exchange adapter：orchestrator 將每個 stage/event request 寫到
`<exchange-dir>/requests/`，host workers 將 JSON 回覆放進 `responses/`，再 `resume`。它本身不啟動
LLM process 或 network call。
新版 payload 以 `long_highlight_contract.route=long_highlight_orchestrator_v2`、
`long_highlight_contract.validation_profile=semantic_visual_minimal` 標示 mutable route；Director／DP 優先依此契約工作，不得
因看見舊欄位而落回 immutable receipt/hash legacy route。

orchestrator 只在入口確認來源存在、可讀與 SRT 時間範圍。三個 miner（story／punch／value）平行讀完整
逐字稿；tolerant merge 接受額外欄位、補齊缺少的 optional 欄位，單一 malformed candidate 只 quarantine
並警告，不丟棄其他語意成果。之後平行派阿哲、凱文、淑芬與 Renee；reviewer 漏評是 warning，不阻塞
其他 review。**沒有額外品牌 lens。**

每支 long candidate 的**實際保留內容不得短於 8:00**。若單一連續段落不足 8 分鐘，miner 要像
`value-L01` 一樣合併兩段以上能形成同一論述弧的片段，而不是把一個 4–7 分鐘候選送進製作。
用 `source_ranges` 列出保留的原片區間；長度計算是各 range 的總和，不是第一段起點到最後一段終點的
bounding span。human approve、tighten 完成與 preview 回報三處都重新套同一個 480 秒下限；任一縮短到
8 分鐘以下就停止在該層，不進下一個創意／製作 stage。

每支 long candidate 同時必帶 non-empty `sections` 論述地圖；理想輸出依 `source_range_index` 排序並覆蓋
保留片段。`transition_before=true` 只代表**新 YouTube chapter 的起點**；同一完整論述內的「方向一／
方向二」、列舉、例子或證據不是 chapter，交給 Director 用 Hero／supporting title。下游 payload 直接帶
同一份 `sections` 與衍生的 `chapter_map`，Fullscreen Transition 與 YouTube description timestamp 必須
共用這些 chapter boundaries，不能由 Director 或 UI 另算一套。
code 把第一段投影成 `timestamp_sec=0` 的 YouTube chapter、但標示不產 Fullscreen Transition；後續只把
explicit `transition_before=true` 投影進 `chapter_map`。每列只有一個 canonical cut-local
`timestamp_sec`，同時供 Fullscreen Transition 與 YouTube description 使用；來源可以是既有 cut-local
start，或由 section source time／cue start 經 `source_ranges` 累積換算。後續 chapter 若三者都沒有可靠
時間，terminal 停在 `chapter_timestamp_unknown`；不得猜成 0:00，也不得送進 Director。

LLM schema 的可修正漂移不打回整個 candidate：缺 `section_id`、單一 source range 缺 index、缺 explicit
transition bool、duplicate ID 等由 orchestrator 正規化並記 warning。三個 miner 都沒有可用候選時進
terminal human-attention，不在 `resume` 自動重派。chapter 語意好壞留給既有 LLM review、Director 與
human winner gate，不用 Python 猜。

候選完成後 state 進入 `needs_review`，必須由修修選 winner；選擇前可直接修 candidate。批准後同一個
orchestrator 續跑 tighten → Director → DP → targeted visual review/fix → Resolve/Preview + Packaging readiness。
單一 visual event 失敗只用 `retry-event --stage visual_fix --event-id <id>` 重跑該 event，不整輪重跑。
外層 state 是可修正 draft，不要求 worker 回傳內容／媒體指紋或完整 provenance chain。唯一會停止自動
下游的條件是：來源不可讀；winner／tighten 實際保留長度 <480 秒；winner `sections` 真正為空；
`source_ranges` 非法、越界，或 multi-range tighten 改邊界卻沒回新 ranges；asset 不可播放；Resolve
operation 標成 destructive；preview 缺失；以及 preview 長度既沒有 adapter 回報、也無法由本地檔
ffprobe 得知；以及後續 chapter 沒有可靠的 cut-local/source/cue 時間。後兩種分別標成 terminal
`preview_duration_unknown`／`chapter_timestamp_unknown`，不得拿 winner／tighten 長度猜成片長或拿 0:00
猜章節位置。
human winner gate 與 all-candidates-quarantined human-attention 是有意停點，不是重新跑 LLM 的理由。

當 Director 已固定 stock authority 時，DP 可只保留該一個可信候選，不必虛構 A/B/C。

> ⛔ **已停用（ADR-066）**：`adopt-existing` / `adopt-winner` 兩個匯入入口隨 ADR-065
> orchestrator 一起停用。ADR-066 沒有「匯入既有 JSON 續跑」這條路——既有成品進 ADR-066
> 只能走 migration，新製作一律從 `run_finished_cut_production.py register-approved-cut` 開始。

## 執行環境（v1 收斂裁決，修修 2026-07-27）

**剪輯與 Resolve 寫入只能走本機 API；素材採購可用登入中的 Browser Computer Use。**

理由是機制差異，不是偏好：
- 本 skill 的 Resolve 操作全走**官方 Python Scripting API**
  （`DaVinciResolveScript`，見 `scripts/build_resolve_project.py`）——
  直接呼叫 timeline/item/Fusion 物件，逐幀關鍵影格、0.05s 精度的音效落點
  都靠它
- Resolve 剪輯不走 **Computer Use**（截圖→認畫面→點按鈕），因為它做不到
  逐幀精度；但 Envato 搜尋、授權與下載依 brook-director 的 Computer Use
  狀態機執行，素材落地後再回到 deterministic script
- 前提：**Resolve Studio 執行中** + Preferences → System → General →
  External scripting using = **Local**；跑 Resolve／Fusion 的 script 用
  `C:\Users\Shosho\AppData\Local\Programs\Python\Python310\python.exe`；repo non-Resolve commands 用
  `E:\nakama\.venv-v2\Scripts\python.exe`
- Sandcastle / 雲端 runner 同樣不適用（沒有 Resolve、沒有素材碟）；
  唯一可外包的是純 render 類工作（hyperframes 卡片），疊軌仍要本機

## 前提與唯一正式 media/timebase

Episode 已完成 Podcast Pipeline 的 `memo-dual-audit-v1` release、Resolve 全節目人工剪輯與
`podcast-editorial-master-v1` approval。正式預設由 `run_highlight_cut.py` 自動發現並驗證：

```text
<episode>/editorial-master/v1/EDITORIAL-MASTER.json
<episode>/editorial-master/v1/master.srt
<episode>/editorial-master/v1/master.mp4
```

不傳任何字幕／media flag；不得讀 episode root `transcript.srt`、Stage 5 release SRT、
`Default_*.mp4`、camera files 或 `normalized.wav`。Stage 5 handoff 只保留在 Editorial Master provenance，
不是 Highlight timebase。若 receipt 缺少、stale、cross-episode 或 tampered，回到 `podcast-pipeline`
的 Editorial Master `inspect/status/seal/verify` route；不得 fallback。
其中會連 Resolve 的 `inspect`、`seal`、`verify --live` 必須使用
`C:\Users\Shosho\AppData\Local\Programs\Python\Python310\python.exe`；只有 offline `status` 與不帶
`--live` 的 `verify` 可使用 `E:\nakama\.venv-v2\Scripts\python.exe`。

## Legacy Step 1 — 取得 strict mining input（只供舊 run）

```powershell
E:\nakama\.venv-v2\Scripts\python.exe scripts\run_highlight_cut.py "<episode>" --mining-input
```

把 stdout JSON 原樣保存為 `highlights/mining-input.json`。它至少包含 `status=mining-input`、唯一
`srt_path` 與完整 `editorial_master_lineage`。三個 miners 只讀該 `srt_path`、訪綱、前期報告與 episode
references；不得自行尋找別的 SRT。

## Step 1.1 — agent-owned 3-miner dispatch

Orchestrator 必須平行 dispatch 三個互相隔離、不能讀彼此輸出的 subagents。這一步是 agent-owned，
不可停下詢問使用者，也不可因 repo 沒有 LLM runner 就跳到 `--validate`：

| Miner | 視角 | 唯一輸出 |
|---|---|---|
| `story` | 起承轉合完整、能獨立成篇的故事／論述弧 | `highlights/miner-story.json` |
| `punch` | 反直覺、情緒強、可當 hook 的金句爆點 | `highlights/miner-punch.json` |
| `value` | 觀眾能帶走方法、清單或 protocol 的實用價值 | `highlights/miner-value.json` |

每個 worker 都必須讀完整 SRT，而不是摘要；每個至少提出 3 個 long、3 個 short。每份 JSON exact schema：

```json
{
  "schema_version": 2,
  "contract": "podcast-highlight-miner-output-v2",
  "miner_role": "story|punch|value",
  "source_srt_sha256": "copy mining-input subtitle_srt_sha256",
  "editorial_master_lineage": {},
  "candidates": [
    {
      "id": "story-L01|story-S01",
      "format": "long|short",
      "t_start": 0.0,
      "t_end": 0.0,
      "source_ranges": [
        {"t_start": 0.0, "t_end": 0.0}
      ],
      "title": "工作代號，不是發布標題",
      "hook": "段內逐字原句",
      "rationale": "為何值得剪",
      "miner": "story|punch|value",
      "head_trim": null,
      "cue_start": 1,
      "cue_end": 2,
      "sections": [
        {
          "section_id": "section-01",
          "source_range_index": 0,
          "cue_start": 1,
          "cue_end": 1,
          "start_quote": "第一個 cue 的完整原句",
          "end_quote": "第一個 cue 的完整原句",
          "summary": "這一段完成的論點",
          "transition_before": false,
          "transition_title": null
        }
      ]
    }
  ]
}
```

`editorial_master_lineage` 是 mining-input 排除診斷欄位 `status`／`srt_path`／`elapsed_sec` 後的完整
identity object；不得刪欄、自行重建，也不得把 `elapsed_sec` 這類執行耗時混入 identity。
`source_srt_sha256` raw exact copy 其中的 `master_srt_sha256`。ID 固定以 miner role 開頭，避免
跨 worker 撞名。`head_trim` 是 cue 內要去除的秒數或 `null`，不是文字。

long candidate 的 `source_ranges` 依原片時間排序、不可重疊；`t_start/t_end` 是第一段起點與最後一段終點，
只供快速定位，實際片長永遠是 ranges 長度總和。單段足以完成 8–12 分鐘論述時可只有一個 range；不足時
必須組合 2+ 個語意連貫片段，不能用中間被刪掉的空白時間灌長度。

long candidate 的 `sections` 要完整覆蓋論述結構，通常 4–6 段，最多 8 段；worker 應填唯一
`section_id`、所屬 `source_range_index`、summary 與 explicit `transition_before`，並以首尾 cue 的完整 raw
text 作錨點。short 固定為 `[]`。`transition_before=true` 僅用於一個觀眾可獨立命名、會寫進 YouTube
description 的新 chapter；同章內的列舉、方向一／二、例子、證據、方法步驟保持 false。title 是 6–14
個中文字的 YouTube chapter／全螢幕 TR 候選文案；第一段不得有 transition。這份 section map 是
editorial 建議，Director 可因 tight cut 微調精確時間與否決不必要的 TR，但不得新增另一套章節結構。

每個 candidate 必須滿足：`t_start < t_end`；long 目標 8–12 分鐘、**硬下限 8 分鐘**、上限 18 分鐘；short 目標
60–120 秒、容忍 40–180 且硬上限 180；hook 必須是時間範圍內 raw transcript substring。內容邊界
優先，不在論述中間切；開頭從提問／轉場／完整論點開始，若同 cue 含上一題殘尾就填 `head_trim`；
結尾必須觀點落地。Worker 不得另尋或添加 `editorial_master_lineage` 以外的 source，也不得讀 root
transcript。

## Step 1.2 — deterministic merge to candidates.json

三份檔案都存在後，orchestrator 必須先做 schema、完整 SRT path、exact `editorial_master_lineage`、candidate
count、finite timing、format、hook substring 與 duplicate local ID 驗證；任一失敗只重跑該 miner。
不可在這裡問使用者。

候選檔真的存在後才執行 official strict merge：

```powershell
E:\nakama\.venv-v2\Scripts\python.exe scripts\run_highlight_cut.py "<episode>" --merge-miners
```

`--merge-miners` 固定讀上方三個 default paths，嚴格驗 contract、role、Master SRT hash、完整 lineage、exact
candidate keys、cue range、finite timing、每家至少一個 long、跨 worker ID 唯一；以
`(format,t_start,t_end,id)` 排序，原子寫 `podcast-highlight-candidates-v2`
`highlights/candidates.json`，接著在同一次 command 執行 validate。Validator 吸附 cue 邊界、計算
duration、將同格式重疊 >50% 標為 variant group（不淘汰）。任何 Master receipt／media／SRT drift
都 fail closed。

## Legacy Step 2 — agent-owned blind persona review

Validate 成功後才 dispatch；每個 reviewer 必須 blind，不能讀其他 reviewer output。三位 scoring
persona 全部覆蓋每個 candidate；Renee 只覆蓋 long：

| Reviewer | Output | Required shape |
|---|---|---|
| 阿哲 | `highlights/review_azhe.json` | `{"persona":"azhe","source_sha256":"<candidates sha>","scores":[{"id":"story-L01","total":0,"rationale":"..."}]}` |
| 凱文 | `highlights/review_kevin.json` | `{"persona":"kevin","source_sha256":"<candidates sha>","scores":[...]}` |
| 淑芬 | `highlights/review_shufen.json` | `{"persona":"shufen","source_sha256":"<candidates sha>","scores":[...]}` |
| Renee lens | `highlights/lens_renee.json` | `{"lens":"renee","source_sha256":"<candidates sha>","findings":[{"id":"story-L01","hook_risk":"...","retention_risk":"...","boundary_action":"..."}]}` |

Persona `total` 必須 finite 0–100；舊 gate 的三份 scoring file IDs 必須 exact
等於 long candidate IDs、無重複、無遺漏；每份 `source_sha256` 必須 raw exact 等於 finalized
`candidates.json` SHA-256，並使用 `hashlib.sha256(...).hexdigest()` 的小寫 hex，不得改成
PowerShell `Get-FileHash` 的大寫顯示。所有引用原句須為 candidate time range 內 transcript raw substring。另派一個 QA pass 驗證 schema、coverage 與 quote citations；任何整份 review citation 錯誤就
作廢並 blind rerun 該 reviewer，不能局部補分。

Shortlist ranking 由既有 code 計算三人中位數；同 variant group 只有最高分佔 rank，其他仍列出；
Renee 不計分。只有四份 review outputs 都驗證通過才可進下一步。

Persona dispatch 仍是 orchestrator-owned subscription subagent stage；`run_cut_shortlist.py` 是 strict
review gate，會拒絕缺檔、stale `source_sha256`、partial/extra/duplicate candidate coverage 與 non-finite
scores。無法產生 exact review outputs 時回報 `HIGHLIGHT_PERSONA_REVIEW_NOT_IMPLEMENTED`，不能假稱已到
人工 gate。

## Step 2.4 — long Highlight shortlist gate（唯一正常停點）

```powershell
E:\nakama\.venv-v2\Scripts\python.exe scripts\run_cut_shortlist.py "<episode>" --format long
```

命令成功產出候選表後才停下，把完整表交給修修選 IDs；此時 `winners.json` 必須仍不存在或維持前一個
已知選擇，不得自動 top 3。收到明確 IDs 後才執行：

```powershell
E:\nakama\.venv-v2\Scripts\python.exe scripts\run_cut_shortlist.py "<episode>" --pick L009,L012,L015
```

**為什麼停在這裡**：panel 是**讀逐字稿**評分的，評的是素材強度，不是成片吸引力、
更不是修修的品味。安吉集自動取 top 3 做完三支之後他才說「其中一兩個主題好像不是
特別吸引人」——那時候製作與 packaging 的成本已經付掉了。他自己算過另一條路
（做 5 支挑 3 支）：多付兩支的製作＋packaging，而 packaging 是 100% 線性、
LLM 用量最大的一塊；把 HITL 移到**排完之後、製作之前**幾乎零成本，因為那張表的
料在 panel 跑完時就已經齊了。

- 表上有：排名 / id / variant 群組 / 中位數 / 三位分數 / 長度 / 主題，
  外加每支的 hook 與 lens 細節。同群組落選的 variant **照常列出**（標「同群組落選」），
  修修可以指名要那個切法
- 幾支都可以（預設 3 支）。修修欽點超過預設數量是原始需求，`--pick` 給幾個就寫幾個，
  順序＝rank
- `winners.json` 只由本 script 寫（schema 由它保證）；既有的 `excluded_group` 保留

## Explicit legacy forensic inputs（不是 production default）

ADR-056 Formal Subtitle V2 的 projection handoff、`--degraded-release-handoff` 與 `--legacy-v1` 只可在
使用者明確要求調查歷史 artifact 時使用；不得出現在 fresh episode 的 commands，也不得 silent fallback。

## Step 2.5 — 邊界打磨（物化前，必做）

**Renee／persona 指出的開頭問題必須在這裡消化成動作，不是只寫進報告**
（2026-07-26 教訓：長2/長3 開頭殘留上一題收尾，lens 看到了但流程沒接住）。

對每個當選段落，讀首尾 cue 原文檢查：

1. **首 cue 含前題殘尾**（收尾語+轉場詞同 cue，或 miner 給了 `head_trim`）→
   寫 `highlights/line_moves_fix*.json`（`after_cue` = 該 cue 序號、`delta` 負數
   把殘尾留在前句）→ `E:\nakama\.venv-v2\Scripts\python.exe scripts\run_line_polish.py <episode>` 切開 →
   candidates.json 該段 `t_start` 改成新 cue 起點（**秒數換算要驗算**：
   28:17.886 = 1697.886，不要心算）
2. **尾 cue 話講一半** → `t_end` 移到上一個完整句尾
3. 已套用的 line_moves 檔改名 `applied_*` 避免重複套用（run_line_polish 會
   glob `line_moves_*.json` 全套一遍）

**單獨重建某條 timeline**（其他條不動、保護修修的剪輯）：暫存 winners.json →
過濾只剩該段 id → `--materialize` → 還原 winners.json。

## Step 3 — 物化 Resolve

```
& "C:\Users\Shosho\AppData\Local\Programs\Python\Python310\python.exe" `
  scripts\run_highlight_cut.py <episode> --materialize
```

- 當選長片 ×3：16:9 timeline（字幕樣式模板自動套）；短片 ×3：1080×1920 直式
  timeline（字幕先橫式樣式——修修調完第一支「Shosho Shorts」track style 後，
  用 build_resolve_project `--make-template` 概念存直式模板，之後自動）
- timeline 進 `Highlights` bin，命名 `長1 - <標題>`
- 每支 timeline 只從 verified `master.mp4` source range 建立；寫入
  `podcast-highlight-materialization-v1` receipt，綁 Master identity、source range 與 Timeline UID。
  核准的 Editorial Master 不可變，不再在主 timeline 寫 candidate marker
- 上軌 SRT 為**顯示層定版副本**（句尾零標點 + cue 間 ≤3s 空隙補平——修修
  2026-08-05 裁決；transcript.srt 本體不動，規則見 subtitle-correct skill）

## Step 3.1 — 來賓 identity-card placement（agent quorum）

Tightening 完成、`highlights/srt/<id>_tight_r*.srt` 實際最新版的 exact path 固定後，兩個互相隔離的
workers 各自判定
來賓第一個 substantive speech cue。Audit contract 是
`podcast-identity-placement-worker-audit-v1`；必須 raw exact copy Editorial Master identity、cut SRT
path/hash/bytes/cue count，並引用 exact cue number/start/end/text/text hash。兩份 audit 只能在 worker ID
不同且 exact cue agreement 時由下列命令接受：

```powershell
E:\nakama\.venv-v2\Scripts\python.exe scripts\podcast_identity_placement.py accept "<episode>" `
  --cut-id <id> --cut-srt "<episode>/highlights/srt/<id>_tight_rNNN.srt" `
  --audit-a "<episode>/highlights/identity-placement/<id>/identity-audit-a.json" `
  --audit-b "<episode>/highlights/identity-placement/<id>/identity-audit-b.json"
E:\nakama\.venv-v2\Scripts\python.exe scripts\podcast_identity_placement.py emit-event "<episode>" `
  --cut-id <id> --name "<guest-name>" --title "<guest-title>"
E:\nakama\.venv-v2\Scripts\python.exe scripts\podcast_identity_placement.py verify "<episode>" --cut-id <id>
```

Free-string 自報、same worker、不同 cue、stale SRT/Master hash、cross-episode/path escape、accepted cue
超過 180 秒，或 guest-namecard 早於／漂出 accepted cue 都 fail closed。只有兩 audit 衝突或皆無法可靠
判斷才是 HITL；一致時不得要求使用者再核准。林之晨 `value-L01` 的 regression fixture 是
guest cue/card start 43.0 秒、card end 48.2 秒。

`emit-event` 是 deterministic producer：以 accepted cue 起點與預設 5.2 秒寫入 canonical
`highlights/tighten/<id>_broll.json`；姓名／title 由 agent 從訪綱與前期報告取得。`verify` fresh 驗 recipe
與 identity lineage，再由 `run_short_broll.py` 使用既有 16:9 `chapter_label` 左對齊名牌 composition render，
不需新增視覺模板或一般 human gate。實際 production 順序固定為 camera/Timeline director →
Director skill → DP skill → same-Director second-pass semantic audit → materializer → titles。

## Step 3.2 — Podcast Highlight visual production truth（ADR-065）

這是 Podcast derivative 的 mandatory adapter；不要拿 standalone Video Production Line 的
`data/script_video/<ep>/storyboard.yaml` 代打。每個 cut 的 truth root 是：

```text
<episode>/highlights/visual-pipeline/<id>/
  PENDING.json
  CURRENT.json
  revisions/<revision-id>/
    DIRECTOR-WORK.json       podcast-highlight-visual-work-packet-v1
    DIRECTOR-PLAN.json       podcast-highlight-director-plan-v1
    DP-FULFILLMENT.json      podcast-highlight-dp-fulfillment-v1
    SEMANTIC-AUDIT.json      podcast-highlight-visual-semantic-audit-v1
```

`run_short_director.py` = camera/Timeline director，**不代表 `brook-director` skill** execution。
它完成機位與 derived Timeline 後，deterministic runtime 才能以 Editorial Master、winner/materialization
lineage與 preflight綁定的 exact tight SRT建立 work packet；agent不得自行找 mtime latest。接著由 subscription agent完整讀 `brook-director` skill產
Director plan；另一個 agent完整讀 `brook-dp` skill履約；再把 fulfillment交回**同一個 Director worker**做
exact event coverage與語意 audit（Director identity相同、DP identity不同）。普通 `awaiting_director`／`awaiting_dp`／
`awaiting_semantic_audit` 是 agent-owned next work，只有 ambiguity 才是 HITL。

`DIRECTOR-WORK.candidate.sections` 是上游完整論述地圖。Director 必須先讀它，再讀 tight SRT 校正原句錨點；
transition event 以 `transition_before=true` 為候選，不是硬性全收。Director 的責任是決定畫面與精確落點，
不是從零重做整支長 Highlight 的章節分析。

只有 fresh status `ready_to_materialize` 才能把 validated selection轉成 canonical broll recipe。
`run_short_broll.py` = materializer，**不代表 `brook-dp` skill** execution；它不得自行找素材、猜 intent、
或用 legacy receipt補過。缺少／stale／invalid 任一 receipt fail closed；既有 `_broll.json` 或素材數量
達標都不是旁路授權。`run_short_titles.py` = materializer，也只能 consume DP產的 title implementation。
Contract涵蓋所有 content visuals：Stock／Hero／keyword／quote／chapter／card；結構性 badge／camera correction／guest namecard
維持各自 deterministic contract。Bridge finished review只展示 fresh receipt
lineage，不新增正常人類 gate。

> ⛔ **已停用（ADR-066）**：`podcast_highlight_visual_orchestrator.py` 屬 ADR-065 revision-scoped
> visual DAG，已被 Finished Cut Production 取代。long 的唯一生產路線是
> `scripts/run_finished_cut_production.py`。下面的指令保留供讀舊 receipt 對照，不要執行。

Codex production route的 exact command（base不帶 request；Save Draft/reject必帶 job內 immutable request）是：

```powershell
E:\nakama\.venv-v2\Scripts\python.exe scripts\podcast_highlight_visual_orchestrator.py "<episode>" `
  --cut-id <id> [--revision-request "<episode-local immutable request.json>"]
E:\nakama\.venv-v2\Scripts\python.exe scripts\podcast_highlight_visual_pipeline.py status "<episode>" --cut-id <id>
E:\nakama\.venv-v2\Scripts\python.exe scripts\podcast_highlight_visual_pipeline.py verify "<episode>" --cut-id <id>
```

Orchestrator固定執行 `init → Director proposal/accept-director → 不同 DP proposal/accept-dp →
resume同一 Director session做 audit/accept-audit → verify`。Claude Code route也必須依同序 dispatch兩個隔離
subagents，audit回到原 Director handle；accept commands需使用 host觀察到的 worker/execution/session identity，
不可信任 proposal自報。Exact accept flags見 `brook-director`／`brook-dp`。`accept-audit`成功才切 CURRENT；
新 PENDING crash/invalid保留上一 CURRENT，同 immutable request retry只 resume同 revision。

## Step 4 — 標題（長短片分流，修修 2026-07-26 二修裁決；ADR-054 D4/D13/D16）

**miner 給的標題只是工作代號**（timeline 命名、報告索引用），**不是發布標題**。

**執行權歸屬（ADR-054 D16①，packaging 段已上線）**：標題發想由 **packaging 段**
（podcast-pipeline 末段）執行——長片走 `title-brainstorm --batch` 完整 7 步（深度
不可簡化 — D13）、短片 LLM 直出不跑 panel（D4）、產物單一落點 `packages.json`。
本 skill **不跑 title-brainstorm、不重跑**；miner 只產工作代號。長短片分流的完整
規則與理由見 `podcast-pipeline` SKILL.md「Packaging 末段」與 ADR-054 D4/D13。

## Step 4b — 選段企劃報告

寫 `highlights/選段企劃-<episode>.md`：

- 各 3 當選段：標題欄一行指向 packaging gate（`/bridge/packaging/<slug>`；候選
  單一落點 packages.json，不重複存 — ADR-054 D16①）+ hook 原句 + 選段理由 +
  persona 意見摘要 + Renee 留存風險（長片）
- 落選全列：分數 + 一句短評（撈遺珠用；主 timeline 藍色 marker 對應）

## Step 5 — 終檢（交付前必做）

派一個 QA agent，拿修修的歷史回饋清單（冷開場殘尾/結尾斷半句/斷句拆散/
說話者混切/標題超出原話/數據歸屬）逐條驗收每個 winner 的**實際上軌 SRT**
（`highlights/srt/<id>_rNNN.srt` 最新版）。發現寫 `highlights/qa_final.json`。

critical 必修才能交付。修法：`line_moves_*.json` 支援三種操作——
- `moves`: `{after_cue, delta}` 邊界移動
- `ops`: `{split_text, at, near_sec?}` 把混切 cue 切成兩個（附和語獨立）；
  `{merge_text, into: prev|next, near_sec?}` 孤兒 cue 併回
（ops 用文字定位不用序號——序號會飄；同文撞名用 near_sec 錨定）

改完 → `run_line_polish.py` → 主 timeline `--refresh-subtitles` +
精華 `--refresh-subs`。已套用的 moves 檔改名 `applied_*`。

**已知極限**：能量 Viterbi 判不出「附和語蓋在對方語流上」的混切（沒錯沒錯/
對對對），只有終檢的語意層看得到——這是終檢存在的理由。

## 長片線 Step 6–11 → `longform-cut` skill

長片線（8–12min 橫式）已獨立成 **`longform-cut`** skill（修修 2026-08-04
裁決：兩線邏輯差異大，不混在一起）。本 skill 到 Step 5 為止對長短片共同
負責（開採出的長片 winners 也在這裡產生）；長片的緊湊化/導播/視覺語彙/
SFX/QC 全部見 `.claude/skills/longform-cut/SKILL.md`。

新交接由上方 Stage 5 orchestrator 的 mutable state 與 targeted visual review 管理。舊 ADR-065 chain
只在明確採用 legacy adapter 時由該 adapter 自己處理，不是 orchestrator 的 outer gate。

Script 層部分共用 `run_short_*.py`（`FORMAT_*` 參數表：`short` 欄／`long` 欄）
——改共用 script 時兩線測試都要跑。短片線自己的入口是 `run_shortform_*.py`
（ADR-067 分家）。

## 短片線 Step 6–11 → `shortform-cut` skill

短片線（45–120s 直式 Shorts）已獨立成 **`shortform-cut`** skill（修修 2026-08-30
裁決：「把目前所有有關短影片的製作流程統整、收斂成一個確定的流程」）。本 skill
到 Step 5 為止對長短片共同負責；短片的緊湊化／導播／字卡／素材／音效／BGM／自檢
全部見 `.claude/skills/shortform-cut/SKILL.md`，分鏡的創意判斷見
`shortform-director` 與 `shortform-dp`。

原本寫在這裡的 Step 6–11 已整段搬過去——**不要在這裡再寫一份**，兩份會漂移
（ADR-067）。

## 修修換段時

改 `winners.json`（換 id/rank）→ 重跑 `--materialize`（冪等，30 秒）。
之後的重跑紀律各線自己管：短片見 `shortform-cut`「換段／改稿」、
長片見 `longform-cut`。

## v2 備忘（不做，見 plan 文件）

hyperframes 進階：`--batch` 一次渲整支的卡（省 npx 冷啟）、RenderStretch
（0.7.67+，可解 data-duration 固定 4s）、`--experimental-fast-capture`
（等出 experimental 再評）。升版流程：改 pin 版號 → 重渲樣張驗 → 進 PR。
cold-open 重排、直式字幕模板、訪談留言補掃校 persona。
（（緊）+ 導播流程套用到長片 —— **2026-08-03 已做，見上方「長片線」節**；
長片 Step 8–11 尚未開發。）
AI 生成素材管線（十八輪修修方向）：brand guideline 先行 → Higgsfield +
Seedance 產 Pixel 風 3D 動畫——自家設計定稿前，B-roll 一律實拍 stock。
短片設計資產層剩餘項（波旬範本還有、我們還沒做）：橘色塗鴉框版式
（重點段落講者縮進橘 doodle 紋理框）、片尾 EP 品牌卡（logo+金句+橘
zigzag）、字幕關鍵詞高亮、BGM/音效。B-roll/貼紙/概念卡已落地（Step 9）。
