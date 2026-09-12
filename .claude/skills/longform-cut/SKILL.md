---
name: longform-cut
description: >
  Podcast 長精華影片製作線（8–12min 橫式 YT）：吃 highlight-cut 開採出的長片
  winners（punch-L5 等），跑長片專屬的緊湊化 → 機位導播 → 證據驅動・稀疏視覺
  語彙（滿版轉場卡/名牌/hero/論文卡/stock 演算法）→ SFX → QC。Use when the
  user says 「長片」「長精華」「longform」或指名長片 cut id（*-L*）的 Step
  6–11 工作。與短片線（highlight-cut）邏輯差異大，獨立成冊（修修 2026-08-04
  裁決）。
---

# longform-cut — Podcast 長精華影片線

**上游**：`highlight-cut` skill 的 Step 1–5（開採/盲審/物化/標題/報告）產出
`highlights/winners.json` 裡的長片當選段（id 形如 `punch-L5`）。本 skill 從
「長片 winner 已物化成 Resolve timeline」開始接手。

**與短片線的關係（修修 2026-08-04 裁決：獨立成冊）**：兩線邏輯差異大——
短片語彙是為了留住滑動的人（高密度、細切、燒字幕），長片觀眾已經坐下
（稀疏、呼吸、CC 字幕）。**Script 層仍共用** `scripts/run_short_*.py` 六支
（`FORMAT_*` 參數表：`short` 欄 = 已驗收 identity、`long` 欄 = 長片覆蓋），
拆的是工作流程知識，不是 code——改 script 時兩線都要跑測試。

## ADR-066 實跑手冊（2026-09-10 首次真的跑通，蘇予昕 L2/L3）

> 在這之前 20260901 這一集的 L1 是**繞過 ADR-066** 用舊路做的：episode 裡沒有
> finished-cut runtime、`highlights/review/` 是空的。第一次真的從 register 跑到
> Resolve 物化，沿路踩到六個「規格與現實對不上」的洞（見 commit `4c29fffc`）。
> 下面是修好之後、實際會通的順序。

### 0. 環境（三個都缺一不可）

```bash
# Resolve Studio 開著、External scripting = Local、專案已開啟
# 直譯器一定是 cp312；3.10 與 3.14 都會在 import DaVinciResolveScript 當下崩潰
PY=E:/nakama/.venv-v2/Scripts/python.exe
export PYTHONPATH="C:\ProgramData\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting\Modules"
export RESOLVE_SUBTITLE_TEMPLATE="E:
akama\data
esolve\subtitle-template.drt"
```

`PYTHONPATH` 沒設 → `Resolve scripting module is unavailable`。

### 1. 綁定檔（每次物化之後都會失效，先同步）

```bash
"$PY" scripts/sync_resolve_config.py --config <runtime>/config/resolve-<ep>.json --check
```

`project_uid` **不是** Resolve UI 上那個 UID，是 locator 的雜湊；而 canonical
timeline 的 UID 每物化一次就換一個（交易把舊的改名成 `__fcp_backup__…`、新的頂上
原名）。手填一定會卡在 `resolve_project_identity_mismatch` 或 `canonical_binding_unknown`。

### 2. 登錄 → Director → DP → 建置 → 視覺審查

```bash
python scripts/run_finished_cut_production.py --runtime-root <rt> --episodes-root G:/Footages   --episode-id "<ep>" --semantic-worker handoff register-approved-cut --input <reg.json>
python scripts/run_finished_cut_production.py ... advance <command_id>   # 反覆跑
```

`--semantic-worker handoff` 會把 packet 攤在 `<rt>/semantic-handoff/<request_id>/`
然後**停下來等 `response.json`**——語意工作由當下這個 agent 自己做，不開 Codex。
`media/` 子目錄裡是每個 component 的預覽畫格：**視覺審查一定要逐格看過再回答**。

`intentional_aroll` 事件不會有畫格（刻意不上視覺），那一關直接放行是誠實的；
只要有任何一個 built component 就必須人眼看。

### 3. 物化（Resolve）

```bash
"$PY" scripts/run_finished_cut_production.py ... --resolve-config <cfg> advance <command_id>
# → state: preview_ready，preview.mp4 落在
#   <ep>/highlights/staging/finished-cut/<hash>/
```

成品就是那支 `preview.mp4`（1920x1080 / 30fps / H.264+AAC，與舊路匯出同碼率），
複製到 `highlights/exports/<cut>.mp4` 即可上架。

### 4. 一定會擋下來的政策門檻（Director 階段就要照著設計）

| 規則 | 值 | 踩到會怎樣 |
|---|---|---|
| asset-backed B-roll 間隔 | **≤75 秒**（含片頭到第一支、最後一支到片尾） | `b_roll_cadence_gap_exceeded`；**修正機制沒辦法新增事件**，只能重新登錄重跑 |
| distinct stock 事件／素材 | 各 ≥3 | `distinct_stock_video_minimum_not_met` |
| Hero 卡 | ≤4，title-like 密度 ≤2/分，任 15 秒內 ≤2 張 | `hero_title_limit_exceeded` 等 |
| 章節卡文字 | 逐字等於該 section 的 `transition_title` | `chapter_transition_projection_mismatch` |
| section 標題 | 不可有「修修：」這種發言人前綴、不可用第三人稱代名詞開頭 | 登錄當下就被 `ApprovedCutRegistrationError` 擋 |
| stock 素材 | 必須**恰好 16:9**（4096x2160 的 DCI 4K 會被擋）、native landscape | `stock_not_landscape_16_9` |
| photo b-roll | **目前做不到**：pass-through 要求素材本身就是 1920x1080，庫裡沒有照片是 | `derived media is not a pre-rendered 1920x1080 canvas` |

**490 秒的片子＝至少 7 支 B-roll，550 秒＝至少 8 支。** 這個要在寫 Director
events 的時候就算好，不是事後補——補不了。

### 5. 素材驗收：容器證明不了畫面是正的

`cb530d56…`（L1 買的）容器寫 1920x1080、沒有 rotation metadata，但畫面裡的人
整個橫躺。**每一支素材在挑進 events 之前都要真的抽一格出來看**，不要只看
`visual_summary` 和寬高。2026-09-10 這支差點跟著 L2 上架。

### 5b. 建置失敗要怎麼查

`inspect-run` 只會說 `build_state: failed`，不說是哪一條指令、也不說為什麼——
`LongDerivedAssetBuilder` 只回一個 `error_code`，而 `_advance_derived_build`
連那個都沒存進 view。要看原因用：

```bash
python scripts/diagnose_derived_build.py --episode-id "<ep>" --command-id <cmd>     --contact-sheet <out.png>
```

逐條跑同一個 builder，印出哪一條 FAIL、以及那支素材的實際寬高與是不是 16:9。
`--contact-sheet` 把這條 run 用到的每支素材抽一格拼成一張——**側躺、黑邊、認錯人
只有肉眼看得出來**，`cb530d56…` 在對照表上一眼就看得出整個人是橫的。

### 6. 修正窗口什麼時候關掉（2026-09-10 改了）

**現在關在「封存成 Release」，不是「鑄出 plan」。**

舊行為是 plan 一生出來就拒絕 `request-correction`。那條規則在這條線上是**反的**：
Resolve 的 timeline 與 preview 只在 plan 生出來之後才存在，也就是說
**等修修看得到成品，窗口已經關了**——他唯一的出路是重新登錄整支，把已經付掉的
語意工作再付一次。修修 2026-09-10：「我希望長片也能快速改。」

所以他在 timeline 上說「這支 B-roll 換掉」時，直接下 targeted correction：

```bash
python scripts/run_finished_cut_production.py --runtime-root <rt> --episodes-root G:/Footages --episode-id "<ep>" request-correction <command_id> dp <event_id> "timeline 上看起來不對，換一支素材。"
```

⚠️ 四個參數都是**位置參數**，不是 flag（`command_id` / `stage` / `event_id` / `feedback`）。
`stage` 只吃 `director` / `dp` / `visual_review`。

correction 會把 `materialization_plan` 清成 `None`、run 退回 `pending`，然後只對
**那一個 event** 重派 DP（`scope=event_retry`），其餘 acceptance 原封不動。
回答完再 `advance` 幾次就會鑄出**新的** plan——而 plan 決定 staging 工作區與 Resolve
transaction 的身分，所以不會覆蓋上一版的產物。

**唯一還擋著的**：這份 plan 已經封存成 current Release。那要走 `request_revision`
（會鑄新 run 並保留整條收據鏈），錯誤訊息會直接告訴你是哪一個 Release。
實務上目前不會遇到——Release 封存是 ADR-066 的另一個階段，這條線還沒接上
（20260901 蘇予昕 全碟 0 個 sealed Release、0 個 current pointer）。

真的要整支重來才用重新登錄：把 `registrations/<episode>/<cut>.json` 的 `approved_at` 換一個
新時間即可（command_id 是整份 payload 的雜湊）。

登錄檔放在 **`registrations/<episode_id>/<cut_id>.json`**——`cut_id` 是每集各自
產生的，`punch-L03` 不只一集有。舊的扁平 `registrations/<cut_id>.json` 還讀得到，
但只有 payload 的 `episode_id` 對得上那一集才採用（章節查詢 `resolve_chapters`
會核對）。

## ⛔ 已停用：Stage 5 Long Highlight orchestrator（ADR-065）

> **本節描述的 ADR-065 orchestrator 已停用，不要照著跑。**
> long 的生產唯一路線 = **ADR-066 Finished Cut Production**：
> `scripts/run_finished_cut_production.py`（`register-approved-cut` / `advance` /
> `request-revision` / `cutover`）。`AcceptedStage` 是唯一語意權威，
> `state.json` 已降級為可重建的 view。
>
> 以下保留為歷史說明，供讀舊 receipt／舊 state 時對照，**不是操作指示**。

新 long Highlight 不再要求操作人依序手跑下面所有 scripts。由
`scripts/run_long_highlight_orchestrator.py` 維護單一可修正 state：

```powershell
python scripts/run_long_highlight_orchestrator.py start <state.json> `
  --episode-id <episode-id> --srt <master.srt> --media <master.mp4> --dry-run
python scripts/run_long_highlight_orchestrator.py resume <state.json>
python scripts/run_long_highlight_orchestrator.py status <state.json>
```

`DirectoryStageRunner` 是 host exchange adapter：只把 stage/event request JSON 寫進 exchange directory，
讀取 host workers 放回的 response JSON；它不自行啟動 LLM process 或 network call。
它送給 Director／DP 的新版 payload 以
`long_highlight_contract.route="long_highlight_orchestrator_v2"`、
`long_highlight_contract.validation_profile="semantic_visual_minimal"` 明確分流；兩個 skill 收到 marker 後不得回落 ADR-065
immutable revision／receipt route。

它協調 source → story/punch/value parallel mining → tolerant merge → 阿哲/凱文/淑芬/Renee
parallel review → 修修 winner gate → tighten → Director → DP → targeted visual review/fix →
Resolve/Preview 與 Packaging readiness。沒有額外品牌 lens。LLM JSON 只要求核心欄位；extra 忽略、
optional missing 正規化、單一 malformed row quarantine。reviewer 漏評只警告，保留其餘有價值判斷。

human approve 前 candidate/state 可直接修正。外層不建立 immutable attempt/revision chain，也不要求
workers 回報檔案指紋或完整 provenance。停止條件以 `highlight-cut` 新 orchestrator 的 canonical 清單
為準：source 不可讀；winner／tighten 實際保留長度 <480 秒；winner sections 真正為空；source ranges
非法、越界或 multi-range tighten 改邊界卻沒回新 ranges；asset 不可播放；Resolve operation 具破壞性；
preview 缺失、實際 <480 秒或時長未知；以及後續 chapter 沒有可靠 cut-local／source／cue 時間。
human winner gate 與 all-candidates-quarantined human-attention 是有意停點。除此之外的 schema 小漂移只
warning／局部修正；visual event 失敗只用 `retry-event --stage visual_fix --event-id <id>` 修該 event，
不能整輪重跑。

Director 已指定 fixed stock authority 時，DP 可以只交該一個可信 candidate，不必補無意義的
A/B/C。下方 scripts 與視覺語彙仍是實作知識，不再構成另一套外層 gates。

（原本此處的 `adopt-existing` / `adopt-winner` 匯入用法隨 ADR-065 orchestrator 一起停用。
ADR-066 沒有「匯入既有 JSON」這個入口——既有成品要進 ADR-066 只能走 migration，
新製作一律從 `register-approved-cut` 開始。）

### Long Highlight 的 section → 畫面契約（2026-08-28）

highlight-cut 交下來的 `candidate.sections` 是整支長精華的**論述地圖**，也是後續章節、
Full-screen Transition 與 YouTube timestamps 的共同來源。Director 只依 exact transcript 驗證這份
canonical map；若 section 邊界或標題不正確，回報 targeted upstream／human correction 並停止該
downstream，不得在 Director events 另改時間、另造 map，也不得只看局部句子重新平均撒轉場：

- `transition_title` 只落在 section map 中真正的新 chapter 起點；公開 description 的
  timestamp 使用同一組 chapter id、標題與起點。任一邊新增、移動或刪除，另一邊必須同步。
- 「方向一／方向二」「第一種／第二種」「步驟一／步驟二」若仍在同一論述、共同回答同一
  問題，屬於章內列舉：依重要性用 compact Hero 或 supporting keyword title，**不是** chapter。
- 禁止兩個 Full-screen Transition 連續出現。若 section map 造成相鄰轉場，先回上游合併或修正
  section；不得用兩張滿版卡代替中間不存在的內容。
- 不以固定數量、平均間距或 `content_gaps` 補 Full-screen Transition；空白區只可觸發 Stock／
  overlay 候選。章節是否成立看論述任務是否改變，不看時間走了多久。

## 執行環境

同 highlight-cut：**只能在 Claude Code + 本機跑**（Resolve 官方 Python
Scripting API，CoWork/Computer Use 做不到逐幀精度）。

- **Resolve Studio 執行中** + External scripting = Local
- Resolve script 一律 `E:\nakama\.venv-v2\Scripts\python.exe`（3.12.10）。Resolve 21.0.3 的
  `fusionscript.dll` 是 cp312：**3.10 與 3.14 都會在 `import DaVinciResolveScript` 當下
  ACCESS_VIOLATION 崩潰**（2026-09-03 實測；舊文件寫的 `py -3.10` 已失效）。pytest/ruff 同一個直譯器
- 從 worktree 跑要帶 `RESOLVE_SUBTITLE_TEMPLATE=E:\nakama\data\resolve\
  subtitle-template.drt`——`data/` 是 gitignored，只存在主倉庫，缺了字幕會無樣式
- hyperframes 卡片 render 可外包，疊軌仍要本機

## Adapter Step 6–7 — 緊湊化 + 機位導播（修修 2026-08-03 開工）

同一批 script，格式由 `candidates.json` 的 `format` 欄自動判定：

```
python scripts/run_short_tighten.py <episode> --detect --id punch-L5
# → agent 複審 keep=null（長片只會有 stutter，filler/backchannel 不偵測）
python scripts/run_short_tighten.py <episode> --apply  --id punch-L5
python scripts/run_short_director.py <episode> --id punch-L5 --stills <dir>
```

參數表：`run_short_tighten.FORMAT_TIGHTEN` / `run_short_director.FORMAT_CFG`。

### A-roll 機位：Master 預設 + 局部 video-only correction

ADR-064 production 預設仍由 Editorial Master 決定保留內容、時間基準、字幕與最終
聲音；`run_short_director.py` 沒有 camera plan 時必須原樣使用 Master program 畫面。
只有成片 review 已指出明確區間，或同一位 Director 標出少數高價值 reaction/wide cue
時，才在 `<episode>/highlights/tighten/<cut-id>_broll.json` 加結構性
`camera-correction`。它疊在 video track 2，plan 外 Master program 與整條 Master audio
完全不動；不要為一個局部問題重導整集，也不要新增 camera-audit Agent。

```json
{
  "kind": "camera-correction",
  "slug": "opening-host-camera-1",
  "subject_role": "host",
  "source_path": "Video/1_CAMERA 1.mp4",
  "src_in": 1260.700,
  "t0": 0.000,
  "t1": 20.933,
  "note": "主持人開場提問"
}
```

- `t0/t1` 是（緊·導播）cut-local 秒；`src_in` 是同步 raw camera 的源內秒。
- `subject_role` 只可為 `host` / `guest` / `wide`，並分別對映 episode director config
  的 CAM1 / CAM2 / CAM3。來源只可在 episode-local `Video/` 且必須存在；不為數十 GB
  raw camera 重算 SHA-256。
- camera-correction 彼此不得重疊，可相鄰形成自然切換；沒有 row 就不改畫面。
- 只套機位修正用
  `python scripts/run_short_broll.py <episode> --id <cut-id> --camera-corrections-only`；這條
  路徑不要求重跑已通過或已 drift 的 content-visual DP/B-roll receipt。

機位文法不是「誰說話就硬切誰」。主要說話者只是預設：短促「嗯／對／好」由
minimum-shot/hysteresis 吸收，不做乒乓切換；長回答可在自然句尾、停頓、語意轉折或
可讀的情緒反應處短切聽者，亦可在段落中受控加入全景。全景和反應鏡頭從合理候選點
選擇，避免固定秒數模板；同一狀況太久時才提高換鏡必要性。Full-screen transition、
Hero 或 Stock 正在遮滿畫面時，不在底下做無意義快速換鏡。

這個局部 raw-camera video override 是 ADR-064「Master program feed 為 sacred A-roll」
文字的窄例外：仍禁止 raw fallback、禁止替換 Master audio/timebase，且必須是明確
cut-local plan。正式擴大成全片自動重導播前，需先 amendment ADR-064；本流程不可默認
把局部例外擴成整集重建。

長片與短片的四個刻意差異（依據 `docs/research/editing-grammar/2026-07-18`）：

| 面向 | 短片 | 長片 | 為什麼 |
|---|---|---|---|
| 緊湊化 | filler ≥0.4s、附和整刀剪 | **只剪真口吃 + ≥0.8s 靜音** | §1.5 呼吸節奏、建議 7 反 over-editing。連接詞與附和是訪談的自然感 |
| 構圖 | zoom 3.2 + Pan 鎖臉裁成 9:16 | **滿幀不裁**（`reframe: false`）| 16:9 源進 16:9 timeline，機位本身就是單人中景 |
| 全景機位 | 無（合成上下分割開場）| **CAM3 直接用**：開場一顆 + 反應鏡頭交替 | 12 分鐘一路在兩張臉之間乒乓會膩；全景定期重建兩人關係 |
| 字幕 | 細切 8 字呼吸單元 | **不細切**（`fine_subs: false`）| 長片不燒字幕只上 CC，切碎反而讓 CC 難讀 |

**⚠️ 長片緊湊化的機械層天花板很低——實測 punch-L5（759s）只移除 1.2%。**
759 秒裡 ≥0.8s 的靜音只有 10 個；0.3–0.8s 那 133 個是說話節奏，剪掉就是
over-editing。長片真正的緊湊化槓桿在**語意刪段**（拿掉離題、重複、沒營養的
來回），不在毫秒級停頓。`cuts.json` 加一筆 `{"kind":"passage","t0","t1",
"keep":true}` 即可——補集運算不分 kind，**零 code 改動**（見
`test_passage_cut_needs_no_special_casing`）。

**⚠️ 修修手改 timeline 尾端後勿重跑 director**（會洗掉手改）。字幕補齊走
`run_short_director.py --refresh-subs`——偵測 manual tail delta 延長最後保留段。

## Step 8′ — 視覺語彙：證據驅動・稀疏（修修 2026-08-04 grill 定案）

**⛔ 血淚在先（2026-08-04 v1 全版打掉）**：第一版把短片的 Step 8–11 語彙
（41 張 tier2 卡、12 組貼紙、概念卡、B-roll、全程 BGM，189 個視覺事件）
換個 16:9 畫幅直接套上長片——修修裁決「太 repetitive，都不能用」，整版
roll back。根因有二：(1) 短片語彙是為了留住滑動的人，長片觀眾已經坐下，
那套節奏在跟內容搶注意力；(2) 素材庫 9 張貼紙硬排 12 個點，必然重複——
**素材不夠支撐密度時要停下來問，不是硬湊**。

### 統一設計系統（修修 2026-08-04 七輪定版）

三件套（滿版轉場卡 / 來賓名牌 / hero）共用「**半透明紙 + 手繪橘 + 逐元素
進場**」：同材質 `rgba(251,250,247,0.85)` 半透明紙白（轉場卡紙紋底 92%
不透明度）、同筆觸 PANTONE 165 `#e98965` 手繪 SVG 描邊（9px round、WOBBLE
定值序列非 Math.random——同 variables 同畫面 cache hash 才有效）、同動線
逐元素 stagger 進場 + 明確退場動畫。

**長片視覺語彙 = 換鏡為主體，視覺事件六種，每種都有明確工作**：

| 事件 | 工作 | 形式 | 數量/集 |
|---|---|---|---|
| **滿版轉場卡** | 章節導航 | `transition_title_wide` + `style:"paper_hand"`（**B2 定版**）＝**紙紋 motion bg 滿版蓋掉畫面（92% 不透明）**＋ink 字置中＋手繪 kicker 短槓＋**等字長手繪底線**（字數×字級算，不量 DOM——字型載入時序在 capture 下不可靠）＋退場動畫（title 上滑出）。只落在 canonical section map 的真正 chapter 起點 **3.0s**（退場收在 span 內），並與 YouTube timestamp 共用 chapter id/title/time；禁止連續出現、禁止把章內列舉當 chapter。`kind:"concept"` + `comp:"transition_title"`。⚠️ 滿版底必須是「元素」：body 背景在 alpha 渲染會被丟掉（v1 黑字裸壓實拍壓臉 bug 的根因）；paper 系是透明字卡，`run_short_broll` 以 ffmpeg 疊 `assets/broll/paper-texture.mp4`（源：Envato beige paper texture，4K 預縮 1080p crf12）預合成 `_tex.mov` | **依 section map；不設配額** |
| **品牌 badge** | 識別 | 左下角 logo，**只出現開場（收在名牌進場前，如 7.4s）+ 每個轉場卡結束後 ~8s**。`kind:"badge"` + slug `brand-badge-7s`/`brand-badge-8s`/`brand-badge-10s`：**定長 fade 預合成**（180px、alpha fade in/out 0.5s——源動畫動作區僅 ~87×48px，150px loop 版感知不到「沒有動」）。ffmpeg `-stream_loop` + scale=180 + pad 66:840 + fade，鋪 track 5。源檔 `E:\Projects\張修修的AI創作者新世紀\output\podcast-logo-animation\` | 開場+轉場後 |
| **來賓名牌** | 介紹 | `chapter_label_wide` `align:"left"` + `sub` + `style:"paper"`＝半透明紙卡＋手繪橘豎筆觸＋**逐元素進退場**（卡落→tick 畫出→姓名滑入→頭銜淡入；修修：「整個區塊一起跑出來沒經過設計」）。落**來賓第一個實質單獨鏡頭內**（查 timeline v1 軌首個 ≥3s 的 CAM2 段，貼切點進、退場收在段內；碎片鏡頭 <1s 掛不了名牌）。⚠️ 開場 badge 窗必須在名牌進場前收掉——左下角同框=擠 | 1 |
| **論文第一頁卡** | 信任感 | 真 PDF 第一頁彈入（`sticker_pair_wide` center 模式；禁 stock 代打）。唯一的證物類型（書封/人名/數據卡都不做——grill 裁決） | 提到具體研究時 |
| **Hero 大字卡** | 章內錨點 | 長片唯一配方：`punch_card_wide` tier1 + `style:"paper"`，1080p 每行字級上限 **96px**；紙卡放在說話者負空間，避免壓迫臉部。只留短橘色 accent，不用滿寬大劃線；禁止同一支片混入黑底、橘底或其他 Hero style。方向／步驟等章內列舉可用 compact Hero 或 supporting keyword title，不能升格為滿版轉場。**agent 自裁**（選轉折點、貼原話、驗語檢查把關） | 2–4 |
| **Stock Video（Stock Village）** | 情境具象化 | 描述情境的時刻滿版實拍。**每支 long Highlight 至少 3 個真正 stock footage events**；guest-namecard、Hero Title、transition、badge、紙紋、photo 與 generated card 都不計數。選點走演算法不逐支請示：①先找「比方說/例如」舉例句與具體可拍的動作／地點；抽象論述本身不硬配隱喻，但必須繼續在片內其他具體段落找滿 3 個，找不到就維持 revision-required，不得讓 finished review 假裝完成 ② `content_gaps`（>75s 無強事件）只輔助找 Stock 分佈，每段 1 支、≥100s 可 2 支且間隔 ≥40s ③來源檔本身必須是 native landscape（寬 > 高；4K 優先、1080p 可用），Long Highlight **禁止直式或方形素材裁成橫式的例外**；同支素材全片唯一，長度切齊被強調句、`src_in` 跳廢頭 ④逐支確認動作、人物關係與情緒極性都符合完整句段；不看字幕也應讀得出語意。例如「工作很忙、上有老下有小」要呈現忙亂／負荷，不能用開心家庭團聚代打 | **至少 3；之後依 content gap 加量** |


#### B-roll 的媒材選擇：**能用影片就用影片**

`photo` 是**沒有合適影片時才用**的退路，不是跟 `stock_video` 平起平坐的選項。
靜態照片停在畫面上 3–5 秒會讓片子瞬間變成投影片；同一個語意只要 stock 找得到動態
素材，就一律用動態。修修 2026-09-09 review 20260901 punch-L04：

> 10:02 這裡放的是一張照片，為什麼要放照片？**能夠用影片就用影片。**
> 這裡的影片應該也很好找，就找一個人在煩惱糾結的樣子就好了。

只有這幾種情況才容許 `photo`：
- 畫面本身就是靜態物件（論文第一頁、書封、截圖、圖表）
- 該語意在 stock 庫裡確實找不到動態素材，且已經換過至少兩組搜尋詞
- `person_inset`（人物去背嵌入）依規格本來就吃 photo

**素材選得「不知所云」比空窗更糟。** 泛用的企業意象（旋轉門、握手、走廊快走、
白底概念棚拍）不要用來頂替具體語意——修修同一天退掉「商務人士穿過旋轉門」代表
「不斷重複模式」：那是搜尋詞想不到畫面時的敷衍，不是隱喻。想不到就標
`intentional_aroll`，不要硬塞。

#### 視覺要承載這一段的**主張**，不是圖解句子裡出現的名詞

修修 2026-09-09 從 timeline 上刪掉三段，理由是「這些都對影片沒有加分，還會造成困惑」。
三段都不是素材爛，是**落點錯**：

| 被刪 | 落在 | 那一句實際在說 | 錯在哪 |
|---|---|---|---|
| 女子撐頭放空 | 0:54.92 | 「我出這一門原生家庭的課程」（下一句是「絕對不是要大家對號入座」） | 圖解「課程」這個名詞，而這一段的主張是**那個但書** |
| 女子恍然大悟 | 5:23.51 | 「堆疊的我爸」 | 素材的 `visual_summary` 寫明它是為 3:50「他就會突然幫我連結到」買的，被挪到 84 秒後的另一個 beat |
| Hero「原來這一切的源頭是我爸」 | 3:50.29 | 「他就會突然幫我連結到／喔我爸就是這樣」 | 見下一節 |

規則：
- **每支素材的 `visual_summary` 已經寫明它是為哪一句買的**（「…；對應『……』」），
  就放在那一句上。要挪去別段，必須先確認畫面內容真的承載得住那一段的主張。
- 一句話裡出現的名詞（課程、報告、相機）**不是**配畫面的理由。先問「這一段在主張什麼」，
  再問「這個畫面有沒有讓那個主張更清楚」。答不出第二題就標 `intentional_aroll`。

#### Hero 大字卡：不准提前講結論，第一人稱只能是講者本人

同一次 review 被刪掉的 Hero「原來這一切的源頭是我爸」落在 **3:50.29**，但講者真正說出
「因此他看到**原來源頭**」是在 **5:13.96**——卡片比音檔早了 84 秒把結論講完，觀眾還沒
跟著走到那裡就先被告知答案。而且 3:50 那一段是予昕在**轉述個案**（「他就會突然幫我
連結到，喔我爸就是這樣」），卡片卻用第一人稱「我爸」，讀起來像是予昕自己的爸爸。

- **落點**：Hero 的主張必須落在**講者已經說出那個主張的位置或之後**，不能提前。
- **人稱**：卡片裡的「我」必須是講者本人。轉述第三人的故事時，不准把對方的「我」
  搬到卡片上——這跟轉場卡的主詞規則是同一條（見 `_approved_cut._THIRD_PERSON_OPENER`）。

#### Hero 大字卡的驗收標準：**沒有 punchline 就不要放**

修修 2026-09-09 定版：「**如果 hero title 沒有一個很強的 punchline 的話，那出來其實是
沒意義的。**」Hero 不是「這段很重要所以標一下」，是**觀眾會截圖的那一句**。

三個測試，任一不過就不要放這張卡：

1. **拿得走嗎？** 這句話單獨貼出去，沒看過影片的人看得懂、而且有感。
2. **會驚訝，或會被說中。** 反直覺的數字、悖論、或「這根本是我」。
3. **不是複述。** 拿掉這張卡，觀眾會少掉什麼？答不出來就是複述。

同一集 punch-L04 的四張，修修的判決與理由：

| Hero | 判決 | 為什麼 |
|---|---|---|
| 一天六七千個念頭都在罵自己 | **留** | 反直覺的量級，而且那個數字指向觀眾自己；它是「拖延不是懶」的證據，不是論點的複述 |
| 被允許不做，才有力氣做 | **留** | 悖論結構，是整段收束的地方，觀眾可以直接拿走用在自己身上 |
| 為了準備，設備買到一百萬 | **砍** | 「沒有什麼存在的必要」——主持人的軼事細節，不是觀眾的處境；音檔已經講得很清楚，卡片只是複述 |
| 原來這一切的源頭是我爸 | **砍** | 提前 84 秒劇透，而且第一人稱指向被轉述的個案 |

**密度**：全片 2–4 張，實際上就是「觀眾的理解被改變」的那幾個轉折。**同一節已經有章節卡
講同一件事時，不要再放 Hero。** punch-L04 前半段的 punchline——「情緒像粽子，要一顆一顆
鬆開」「她不是你爸」「拖延症不是懶，是想法太勤勞」——本來就由章節卡在扛，所以砍完剩
2 張是對的密度，不是缺口。

**落點已經由程式鎖住**：`_context.derive_visual_placement` 要求 `hero_title` 的
`placement_cue_ids == semantic_cue_ids`（跟章節卡同一條鎖）。Director 說哪句話是這張卡
的證據，卡片就出現在那句話上，DP 不能從一段長證據裡挑最前面。



**stand-in 鐵則（修修 2026-08-06）**：stock 描述「修修本人做某事」的情境時，
一律用固定 stand-in 模特兒（Envato `YuriArcursPeopleimages` 帳號、臉部參考與
找片工法見 brook-dp skill）——不同男模特兒輪流充當修修是視覺 bug。

**選片鐵則（修修 2026-08-06，Christina 集 44 位整版打槍後固化）**：任何
stock 上軌（含手剪情境）必守 brook-dp〈選片鐵則〉節——①同支 footage 全片
只准出現一次（「主題不重複」不夠，大忌）②in/out 切齊被強調的那句話，不是
固定秒數、不是落在鄰句 ③畫面＝語意（不看字幕也讀得出那句話才合格）④抽象
專有名詞無對應畫面→字卡＋音效，不硬湊隱喻（騎馬≠半人馬）⑤負面意象禁用
（小孩用平板/3C）⑥情緒極性要對齊完整句段，不以「有同樣人物／物件」取代情境
⑦書封類靜態圖去背＋動畫＋不遮臉。人物 headshot 一律是縮小、帶進退場動畫的
`person_inset`，擺在負空間；不得因手上只有一張照片就擴成滿版。只有本身具有環境／
證物語意，且 Director 明確指定 full-screen 的 image，才做有意圖的滿版 composition。
ad hoc 掃字幕就近配對
＝整版報廢的根因，見 brook-dp 教訓紀錄。

**退場語彙**（長片不用）：橘塊 style（保留當 `style:"orange"` 比較基準）、
章節籤（center 模式保留在 composition 裡但語彙上退役）、tier2 卡、貼紙、
概念卡、Ken Burns、BGM。聲音只配重點事件（hero=ding / 證物=pop / 轉場卡
=swish / badge 與 stock 無聲）。**稀疏是設計**，自檢包只看 QC（遮擋/同步/
樣式），密度指標無意義——但 `content_gaps` 的素材真空掃描有意義（見上表）。

### 執行

```
# titles.json 只放 tier1 Hero；broll.json 放轉場卡/名牌/論文卡/badge/stock/person inset
python scripts/run_short_titles.py <episode> --id punch-L5
python scripts/run_short_broll.py  <episode> --id punch-L5 --validate-only
python scripts/run_short_broll.py  <episode> --id punch-L5 [--stills <dir>]
python scripts/run_short_sfx.py    <episode> --id punch-L5     # 不跑 bgm
python scripts/run_short_review.py <episode> --id punch-L5
python scripts/build_finished_review_manifest.py <episode> --verify
```

`--validate-only` 是不連 Resolve 的 deterministic gate：0／1／2 個 Stock Video、缺檔、同檔重用、
path escape、glob slug 或 hash drift 都 fail closed。只有通過後才能真正上軌；`run_short_review`
會 fresh verify materialization receipt，finished manifest 再次要求至少 3 個並顯示真實 count。
`build_finished_review_manifest.py --verify` 是 finished review／revision worker／Bridge Approve 共用的
唯一 authoritative verifier：從 current plan、materialization receipt、實檔 hash 與 events fresh rebuild，
再和 manifest exact compare；不得只相信 manifest 自報的 `asset_category` 或 count。

### Finished revision 的 trusted Stock Video handoff

Stock Video 下載與授權證據收集是上游 acquisition worker 的工作；Finished Revision
Agent 只生 plan，不得自報來源或授權。Acquisition 交付 `trusted_asset_sources.json`，
並將各 `filename` 的影片放在 JSON 同目錄。Finished Revision／Resolve 必須使用
FusionScript 相容的 Python（cp312、`.venv-v2`，見上方「舊文件寫的 `py -3.10` 已失效」），
不可由跑其他 app 邏輯的直譯器消費：

```powershell
$resolvePy = 'E:\nakama\.venv-v2\Scripts\python.exe'
```

先 dry-run：

```powershell
& $resolvePy scripts/finished_review_watcher.py --episodes-root "G:\Footages" `
  --reconcile-episode "<episode>" `
  --trusted-asset-sources "<acquisition-dir>\trusted_asset_sources.json"
```

Dry-run 會 fresh 驗 schema、path containment、bytes、SHA-256、ffprobe、source/license URL 與
timezone-aware `acquired_at`，但不寫 episode。確認後在同一命令加 `--apply-reconcile`：

```powershell
& $resolvePy scripts/finished_review_watcher.py --episodes-root "G:\Footages" `
  --reconcile-episode "<episode>" `
  --trusted-asset-sources "<acquisition-dir>\trusted_asset_sources.json" `
  --apply-reconcile
```

Apply 會先建 episode-local content-addressed handoff，再把 sources hash 綁入 request ID。
Bridge 日後 Save Draft 會自動讀這個 handoff。0-stock long Highlight 若還沒有已核准
素材，job 必須顯示 `awaiting_stock_assets`；不得派 worker 後才模糊地 failed。
`highlights/revision-inputs/current.json` 是可重用的核准來源指標，不是一次性 queue message；
成功後不得清除。後續 revision 可重用 episode 內完全相同 filename／bytes／SHA-256／ffprobe／
provenance 的素材，但不得覆寫；任何漂移都必須在 Agent 或 Resolve 啟動前 fail closed。

若 worker 在 Agent／Resolve 前失敗，不可手改 feedback JSON。先做只讀 rollback 驗證：

```powershell
& $resolvePy scripts/finished_review_watcher.py --episodes-root "G:\Footages" `
  --retry-episode "<episode>" --retry-failed "<request-id>"
```

只有輸出 `rollback_verified: true` 才可在同一命令加 `--apply-retry`。Retry 保留原本的
content-addressed request ID 與舊 failure receipt，下一 attempt 寫入獨立子目錄；來源 manifest、
preview 或 rollback inventory 有任何漂移就拒絕 requeue。

若舊 worker 因 ABI `SystemExit` 留在 `running`，先用同一支 cp312 直譯器（`.venv-v2`）做只讀 recovery：

```powershell
& $resolvePy scripts/finished_review_watcher.py --episodes-root "G:\Footages" `
  --recover-episode "<episode>" --recover-running "<request-id>"
```

只有 PID/session 已不存在、agent logs 完整、backup 與 pre-snapshot 一致，且 Resolve read-only
probe 證明沒有 `__revision_backup__`／`__revision_work__` Timeline 時，才可加
`--apply-recovery`。Recovery 會 restore partial promotion、寫 recovery receipt 並標 failed；
之後再走上一段 dry retry → `--apply-retry`，不得手改 feedback。

軌道契約：v1 主鏡（導播）/ v2 滿版 stock / v3 Hero / v4 名牌+轉場卡+論文卡+
person inset / v5 badge；a1 對白 / a2 SFX。Bridge timeline lane 必須依 semantic
category／implementation component 分類，不可只看它來自哪個 JSON：`transition_title`
永遠進 Full-screen Transition lane，`punch_card_wide` tier1 才進 Hero lane；任何
`transition_title` 出現在 Hero lane 都是分類錯誤，不得靠改顯示名稱掩蓋。

### 工程陷阱（實測，勿重踩）

- **16:9 composition 是分家的檔案，不是參數**——hyperframes 畫布來自 root
  的 `data-width/height` 靜態屬性，JS 改不動 → `*_wide.html` 變體由
  `FORMAT_*` 的 `comp_suffix` 選檔
- 幾何/置中陷阱（CSS transform 與 GSAP 疊加、證物大圖要用 height 定尺寸、
  直式素材裁 16:9 只剩中央 32% 橫帶、全身鏡頭會裁成無頭軀幹）見各
  `*_wide.html` 檔頭註解——**直式 `concept_card.html` 的置中疊加 bug 還在**
  （動它會改到已出貨短片，待裁決）
- **hyperframes 連續第 ~5 次 render 偶發空 stderr 失敗**（兩輪重現）——非
  內容問題，`_render_card` 已內建冷卻 5s 重試一次
- **手動 render 單卡時，沒給的 variable 會吃 composition default**——
  punch_card_wide 的 line2 預設「你的耐心」，漏給就多印一行（2026-08-06
  半人馬卡實測）。所有欄位明給，空值明給空字串
- **卡片 cache 檢索不看 item 編號**——檔名 `<cid>_broll_<i>_<hash>` 的編號
  只是人類對照，插入新 item 位移編號時以 hash glob 撿舊檔（曾讓 5 張卡
  白渲 10 分鐘）

## Step 10–11 — SFX + QC

- SFX：`run_short_sfx.py` 依 broll/titles json 對位（轉場卡 swish 優先級 2、
  論文卡 pop、hero ding）；**長片無 BGM**
- QC 包（`run_short_review.py` `FORMAT_REVIEW.long`）：960×540 preview、
  縮圖牆 180s 分包、**不 ffmpeg 燒字幕**（長片走 Resolve 字幕模板，render
  已燒進 preview，再燒會雙層）、`gap_sec` 14s（弱事件節拍）+
  `content_gap_sec` 75s（強事件素材真空，附 transcript 供 stock 提案）
- 每輪改動交付：preview mp4 傳修修 + 關鍵事件幀自檢（`ev_*.png`）

## 改動代價表（修修在 timeline 上說「這裡改一下」時查這張）

他不會用 ADR-066 的語言講話，他會說「這張字卡改一下」「這支 B-roll 換掉」
「這一段整個不要」。這張表把那句話翻成**要重跑到哪裡**，先查表再動手。

長片的分界線跟短片不同：**貴不貴看的是「有沒有跨過 `materialization_plan`」**，
不是跨過哪一個 Step。

| 他說的 | 要重跑 | 前提 | 量級 |
|---|---|---|---|
| 「換一段別的」 | 改 `candidates.json` 邊界 → 重新登錄 → 整條走一次 | — | **最貴**，等於重做一支 |
| 「這一段整個不要上視覺」 | `request-correction <cmd> director <event> "<話>"`（改成 intentional A-roll） | 沒有封存成 Release | 分鐘級 |
| 「這支 B-roll 換掉」 | `request-correction <cmd> dp <event> "<話>"` | 同上 | 分鐘級 |
| 「這張字卡文字改一下」 | `request-correction <cmd> director <event> "<話>"` | 同上 | 分鐘級 |
| 已封存成 Release 之後 | `request_revision`（鑄新 run，保留收據鏈） | — | 半條線 |
| 「配樂／SFX」 | Step 10–11 | 不動 Release | 分鐘級 |
| 「標題／封面／描述」 | packaging，不碰 timeline | — | 秒級 |

**那條線落在哪裡**：2026-09-10 之前是「視覺審查全數通過」——plan 一生出來窗口就關。
現在移到**封存成 Release**，而這條線目前根本不封存 Release，所以實務上
**timeline review 之後想改什麼都是分鐘級**（見上面第 6 節）。

> `request_amendment`（對**已封存 Release** 做機械式修改）仍是 ADR-066 的 open
> follow-up。變換層已經一般化並證明與 `amendments/operations/` 那兩支釘死的腳本
> 等價（`_amendment.py`＋`tests/.../test_finished_cut_amendment.py`），驅動那條
> 交易鏈的部分還沒接——但那是給「已經發布出去的成品」用的，不是 timeline review。

## 下游

長片定版後 → 發布線（`publish_prep.py` render+登錄 → 描述生成 → uploader，
見 `docs/plans/2026-07-26-video-publishing-plan.md` + ADR-054）。

### YouTube description 文體（長 highlight 專用）

生成或改寫 `/bridge/publish` 的長 highlight 文案時，以下是發布契約：

- 第一段直接進入來賓面對的具體處境；不要先下「這支影片在談……」之類的總論。
- 禁用「不是 X，而是 Y」「不只 X，更是 Y」等 AI 對偶句，改成直接肯定句。
- 刪掉「這一段會從 A 一路談到 B」等自我導覽句；章節已負責導航。
- 每段只推進一件事；用具體人物、作品、數字與動作取代抽象形容詞。
- 不得捏造獨特性或動機；沒有逐字稿證據就不寫。
- description 固定結構：1–2 個短段 hook（約 200–300 個繁中字）→ 章節（**時間戳在行首**，YouTube 認不出來會整份靜靜地不生效）→
  可選的公開來源 → 精簡固定 footer。footer 一律讀
  `agents/usopp/templates/video_description_footer.md`，禁止在 prompt 裡複製舊版。
- `packages.json.citations` 可包含內部 provenance，但 SRT/VTT/JSON 路徑、vault 路徑、
  transcript timestamp 只留內部，絕對不得顯示在對外 description。只有人類可讀的
  論文、書籍或公開 URL 才可出現「本集引用」；沒有就整段省略。
- 交付前逐句掃描「不是／而是」「不只／更」「這一段會」「帶你看」「深入探討」；命中就重寫。
- **描述裡的章節時間戳要先切好。** `resolve_chapters` 的來源依序是 Release 對應表 →
  核准剪輯登錄的滿版轉場卡 → **agent 切的章節表** → 舊 broll 檔。前兩個常常接不上：
  完整版根本沒有轉場卡，長片也可能湊不到兩張（20260721 的 story-L02 與 value-L02
  各只有一張），這時描述裡會一個時間戳都沒有而且不報錯。所以**每一支（含 full）在
  進 Publish review 之前先跑**：

  ```bash
  python scripts/author_chapters.py "<episode>" --cut <cut_id> [--duration-sec <片長>] < chapters.json
  ```

  切章本身是語意工作——讀 `editorial-master/v1/master.srt`（或該支的 tight SRT），
  判斷話題在哪裡轉、用觀眾看得懂的話命名，由當下執行的 agent 做，腳本只驗規則。
  顆粒度：**10 分鐘的長片可以切細，87 分鐘的完整版不要**——修修 2026-09-12 看過
  29 章的版本後要求收斂，定版是 16 章（平均 5.4 分、最短 2.4 分）。
  schema 擋 YouTube 的硬性規則（首章 0:00、至少 3 章、遞增、每章至少 10 秒）；
  違反其中任何一條 YouTube 會整份忽略而且不會告訴你。

- Packaging 核准、正式 export 登錄成 Release 後，Bridge 會呼叫
  `scripts/publish_description.py <episode> --cut <cut> --auto`，只走
  `auth_policy="subscription_required"`。成功後才進 Publish review，description 保持可編輯。
- 畫面顯示 `DESCRIPTION_DRAFT_INTERRUPTED` 代表 provider／證據暫不可用；在同一頁按「重試產生
  Description」續跑。空白 description 不得直接進 Publish review。Release 已有非空 description
  一律視為人工稿，重跑不得覆蓋。
- prompt 與機械 AI-slop gate 的 canonical 實作在 `agents/usopp/video_description.py`；Skill、
  Claude Code 或 Codex 都必須呼叫這個 seam，不可另寫一份 prompt 漂移。

### 發布授權、進度與 CC 補傳

- worktree 的 uploader、Web App 與 OAuth 必須共用 `NAKAMA_DATA_DIR`；不可各自在
  worktree 產生 token 或 progress。
- token scopes 必須包含 `youtube.upload`、`youtube.force-ssl`。
- 明確核准初次 upload 後，CLI 路徑是
  `python scripts/publish_upload.py --approve <cut> --episode "<episode>"`，接著
  `python scripts/publish_upload.py --run --episode "<episode>" --cut <cut>`；Bridge 的
  「核准並上傳」按鈕走同一 worker。未取得該次明確核准前兩條都不可執行。
- 影片本體成功但 CC 失敗時禁止重傳影片；只跑
  `python scripts/publish_upload.py --cc-only <cut> --episode "<episode>"`。
- upload status 必須讀同一個 `NAKAMA_DATA_DIR/upload_progress/`。

## 收斂後的運行模式（修修 2026-08-04 裁決：剪輯線免 HITL）

util-L4 完成後修修裁決：「長影片的剪輯已經慢慢可以收斂了，以後都可以不用
經過 Human in the Loop。」自此每支長片 Step 6–11 的**製作動作自動跑完**（含
hero 自裁、stock 演算法、視覺語彙套定版系統），但產物仍需在三個 gate 停下來：

1. **Finished-cut gate**（`/bridge/highlights/<episode>/finished` 看 preview 與 final QA，
   核准後才觸發 full-resolution `publish_prep`）
2. **Packaging gate**（PACKAGING 頁選主打組——TF-duo 三組由 thumbnail 線自動產）
3. **發布審核頁**（/bridge/publish 看 preview、潤文案、明確核准排程／上傳）

preview 照樣交付（他要看隨時能看），但**不阻塞產線**。

### 事實更正卡（人工或 editorial review 發現數據問題時，util-L4 首用）

數據類 correction 的三件套處置：
1. **錯誤數據句 → 語意刪段**（passage cut；例：聽力 2–3% 實為 ~7%）
2. **比例性質誤讀 → 更正卡**：`chapter_label_wide` center + paper，label=
   正確表述（「45–47%＝族群層級可預防比例」）、sub=補充（「非個人風險降幅｜
   The Lancet 2024」）。**不標「（主持人引用）」**——修修：聽得出來、看得出來
3. **數據不掛來賓名下**：更正卡落在主持人鏡頭段內（導播本來就會切說話者）

### 字幕 QC（發布前必掃，錯字修源頭）

- 讀最新 tight SRT 全文掃 ASR 錯字；**修 `transcript.srt` 源頭**（CC 與未來
  重跑都繼承），再 `--refresh-subs` 換字幕軌（不動剪輯）
- 只修上下文能確證的錯（例：「捷思啊神力啊」→「節能啊省力啊」——修修
  同場說過「大腦是很節能的」）；不確定的列給修修，**不腦補**
- ⚠️ **passage cut 邊界落在 cue 內時，字幕文字不會跟著切**（util-L4 血案：
  剪掉的「2、3%」文字殘留 0.8s）——先把 source cue 在切點**拆成兩個 cue**
  再 refresh，讓 retime 自然丟掉被剪的那半
- stock 銜接：滿版不裁的前提下 1080p 源可用（畫質無損；4K 仍為預設優先）

## 物化之後：交給發布線

`preview_ready` 那支 `preview.mp4` 已經是全解析成品（1920×1080 / 30fps / H.264+AAC）。
但**不要手動複製到 exports 就當結束**——正規路徑是發布線的
`scripts/publish_prep.py`（ADR-055 Slice 1），它會同時登錄一筆 draft Release，
下游的文案、排程、核准、上傳全靠那筆記錄。

```bash
python scripts/publish_prep.py "<episode>" [--cut <cut-id>]
```

之後：packaging → `publish_description.py` → `/bridge/publish/<ep>/<cut>` 核准並上傳
→ Campaign Anchor 排程 → `publish_reconcile.py` 確認公開。全圖見 ADR-055。
