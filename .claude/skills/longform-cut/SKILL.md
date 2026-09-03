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
FusionScript 相容的 Python 3.10，不可由 `.venv-v2` 的 render watcher 消費：

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

若舊 worker 因 ABI `SystemExit` 留在 `running`，先用 Python 3.10 做只讀 recovery：

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

## 修修換段時

同 highlight-cut：改 `candidates.json` 邊界 → 重跑 Step 3 物化該段 →
Step 6 起重走。修修手改過的 timeline 尾端見 Step 6–7 的 `--refresh-subs` 註記。

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
- description 固定結構：1–2 個短段 hook（約 200–300 個繁中字）→ `⏱` 章節 →
  可選的公開來源 → 精簡固定 footer。footer 一律讀
  `agents/usopp/templates/video_description_footer.md`，禁止在 prompt 裡複製舊版。
- `packages.json.citations` 可包含內部 provenance，但 SRT/VTT/JSON 路徑、vault 路徑、
  transcript timestamp 只留內部，絕對不得顯示在對外 description。只有人類可讀的
  論文、書籍或公開 URL 才可出現「本集引用」；沒有就整段省略。
- 交付前逐句掃描「不是／而是」「不只／更」「這一段會」「帶你看」「深入探討」；命中就重寫。
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
