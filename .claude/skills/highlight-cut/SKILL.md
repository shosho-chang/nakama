---
name: highlight-cut
description: >
  訪談集精華選段：整集 transcript（說話者已切）開採長片（8–12min 橫式 YT）與
  短片（60–120s 直式 Shorts）候選段落，persona 盲審評分各選 top 3，物化成
  Resolve timeline + marker + 選段企劃報告。Use when the user says 「選段」
  「切精華」「highlight」「剪長片/短影片段落」, or after resolve-project
  completes in the podcast pipeline. 一鍵到底，修修只看最終報告。
---

# highlight-cut — 訪談集精華選段

設計凍結：`docs/plans/2026-07-25-highlight-cut-plan.md`（grill Q1–Q7）。
**零 API 錢**：miner 與 persona 全走 Cowork subagent。

## 前提

episode 已完成 podcast-pipeline 至 resolve-project（`transcript.srt` 說話者已切、
Resolve 專案存在且 Resolve 開著）。

## Step 1 — 開採（3 miner subagent 平行）

派 3 個 Opus subagent，各讀完整 `transcript.srt` + `refs/` 訪綱，視角分工：

- **故事弧**：起承轉合完整、能獨立成篇的論述段
- **金句爆點**：反直覺、情緒強、可當 hook 的瞬間，往外擴到自然邊界
- **實用價值**：觀眾能帶走方法/清單/protocol 的段落

每個 miner 提長片 ≥3、短片 ≥3。規格（寫進 miner prompt）：

- 長度：長片目標 8–12min（容忍 6–18）；短片目標 60–120s（容忍 40–180，硬上限 180）
- **內容邊界優先於長度**：絕不在論述中間切；段落開頭必須是說話者輪替點或提問，
  結尾必須是觀點落地；容忍帶外不提；偏離目標帶要寫一句「為什麼值得破格」
- **冷開場必須乾淨**（修修 2026-07-26 血淚 ×2）：段落第一句**不可含上一話題的
  收尾/反應語**——「對啊我就覺得超級有趣的」「再講X會講一整集」這種殘尾。
  訪談主持人的慣性是「先收上一題再轉場」，收尾語常跟轉場詞（那現在／我們來講
  下一個／接下來）黏在**同一個 cue**——這種情況段落要從轉場詞起算，並在輸出裡
  給 `head_trim` 欄位標出該 cue 內要剔除的殘尾字串（例：`"head_trim": "對啊我就
  覺得超級有趣的"`）
- 輸出（每候選）：`{id, format(long/short), t_start, t_end, title, hook(段內第一個
  抓人的原句), rationale, miner}`——id 格式 L1/L2…（長）、S1/S2…（短），秒為單位

合併三家提案 → 寫 `highlights/candidates.json` → 跑：

```
python scripts/run_highlight_cut.py <episode> --validate
```

（吸附 cue 邊界、長度帶檢查、同格式重疊 >50% 標 **variant 群組**——
**不淘汰**。2026-07-26 教訓：評分前用 rationale 長度去重，害「數位排毒+
睡眠運動」整塊從未被評分就消失。重疊候選是同素材的不同切法，全部進盲審）

## Step 2 — persona 盲審（進 persona-review skill）

呼叫 `persona-review` skill：

- artifact：candidates.json 內每個候選段的 transcript 節錄（附時間軸），長短分開審
- persona set：`yt-audience`（阿哲-YT／凱文-YT／淑芬-YT 評分 + brand-lens +
  Renee 兩個 lens；Renee 只審長片）
- rubric：長片 `yt-longform`、短片 `yt-shorts`
- 評選規則（grill Q6）：三位評分 persona 各給總分 → **取中位數排名**；同分
  新觀眾判準強的 persona 分數優先；lens 不計分；**brand-lens 可標否決**
  （斷章取義/害來賓）——否決段標紅進報告等修修裁決，不自動排除
- **同 variant 群組只取最高分者佔排名**（評分後才去重；落選 variant 照常
  進報告與 marker）
- 各選 top 3 → 寫 `highlights/winners.json`：`{winners: [{id, rank, score}],
  vetoed: [{id, reason}]}`；修修欽點的額外段落可以 rank 4+ 加進 winners
  （原始需求：精彩就可以超過預設數量）

## Step 2.5 — 邊界打磨（物化前，必做）

**Renee／persona 指出的開頭問題必須在這裡消化成動作，不是只寫進報告**
（2026-07-26 教訓：長2/長3 開頭殘留上一題收尾，lens 看到了但流程沒接住）。

對每個當選段落，讀首尾 cue 原文檢查：

1. **首 cue 含前題殘尾**（收尾語+轉場詞同 cue，或 miner 給了 `head_trim`）→
   寫 `highlights/line_moves_fix*.json`（`after_cue` = 該 cue 序號、`delta` 負數
   把殘尾留在前句）→ `python scripts/run_line_polish.py <episode>` 切開 →
   candidates.json 該段 `t_start` 改成新 cue 起點（**秒數換算要驗算**：
   28:17.886 = 1697.886，不要心算）
2. **尾 cue 話講一半** → `t_end` 移到上一個完整句尾
3. 已套用的 line_moves 檔改名 `applied_*` 避免重複套用（run_line_polish 會
   glob `line_moves_*.json` 全套一遍）

**單獨重建某條 timeline**（其他條不動、保護修修的剪輯）：暫存 winners.json →
過濾只剩該段 id → `--materialize` → 還原 winners.json。

## Step 3 — 物化 Resolve

```
python scripts/run_highlight_cut.py <episode> --materialize
```

- 當選長片 ×3：16:9 timeline（字幕樣式模板自動套）；短片 ×3：1080×1920 直式
  timeline（字幕先橫式樣式——修修調完第一支「Shosho Shorts」track style 後，
  用 build_resolve_project `--make-template` 概念存直式模板，之後自動）
- timeline 進 `Highlights` bin，命名 `長1 - <標題>`
- 主 timeline 全候選打 marker（當選紅／落選藍），冪等（重跑先清舊）

## Step 4 — 標題（必經 title-brainstorm，修修 2026-07-26 裁決）

**miner 給的標題只是工作代號**（timeline 命名、報告索引用），**不是發布標題**。
每個當選段落各自跑一次 `title-brainstorm` skill：

- input = 該段落的逐字稿節錄（`highlights/srt/<id>_rNNN.srt` 或 review pack 的
  該段文本存成暫存檔）
- 走它完整流程（TA 定位 → 關鍵字評分 → 6 角度發散 → panel 冷讀）產 Top 5
- 產出寫進選段企劃報告該段落的「標題候選」欄

miner 標題自產、跳過 title-brainstorm = 違規（曾產出段內未出現關鍵詞的標題）。

## Step 4b — 選段企劃報告

寫 `highlights/選段企劃-<episode>.md`：

- 各 3 當選段：3 個標題候選 + hook 原句 + 選段理由 + persona 意見摘要 +
  Renee 留存風險（長片）
- brand-lens 否決項**標紅**置頂等修修裁決
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

## Step 6 — 短片緊湊化（修修 2026-07-26：短影片節奏要快狠準）

短片開頭的「那、那」口吃**絕不能出現**，中間停頓/贅詞也要剪，jump cut 越緊
越好。每支當選短片跑 `run_short_tighten.py`，產出**新** timeline
`短N - <標題>（緊）`（原 timeline 不動，供對照）：

```
python scripts/run_short_tighten.py <episode> --detect --id <winner-id>
# → agent 複審 highlights/tighten/<id>_cuts.json 的 keep=null 項
python scripts/run_short_tighten.py <episode> --apply --id <winner-id>
```

複審準則（機械偵測會誤報，語意層把關）：

- **filler「那/啊/喔」拖 ≥0.4s**：連接詞用法照剪（口語遲疑），但要確認剪掉
  後字幕文字仍通順（script 會同步從 cue 文字移除該字）
- **stutter 重複字**：真口吃（那那/他他）剪第一個；APP 拼字/數字/疊詞誤報
  已被 ASCII + 首字時長 ≥0.25s 濾掉，殘餘誤報標 keep=false
- **backchannel 整句附和 cue（對/嗯/沒錯）**：先用
  `ffmpeg silencedetect`（-22dB 粗探）看該區有無縫隙——**緊貼前後語流無縫
  隙 = 重疊型附和，剪了會斬到來賓語音，keep=false**；有獨立空檔才整刀剪
  （刀口留 60-90ms 護墊）
- **假起手（裡面就是裡面裡面他他）**：雙字詞重複偵測不到，人工掃該短片
  頭 10 秒的 cue 文字，發現用 `{"kind":"manual","t0","t1","strip_text"}` 手
  動下刀（strip_text = 同步從字幕刪除的字串，詞級時間戳定刀口）

字幕重對時規則（script 自動）：cue 跨刀不拆行（塌縮後 min-max 合一行）、
被剪贅詞同步從文字移除、整句被剪的 backchannel cue 自然消失。

## Step 7 — 短片雙機位導播（修修 2026-07-26：畫面切分要更細緻）

短片不用機器導播混好的單一 source，改用原始機位（`Video/1_CAMERA 1.mp4`
=修修、`2_CAMERA 2.mp4`=來賓；全景機位不用）。Step 6 cuts.json 複審完後
每支短片跑：

```
python scripts/run_short_director.py <episode> --id <winner-id> --stills <dir>
```

產出**新** timeline `短N - <標題>（緊·導播）`（Step 6 的（緊）版與原版
都保留對照）：

- 誰講話切誰的機位（mic 能量詞級說話者，同 speaker-split 那套）；
  <1s 的附和不切鏡（切過去再切回來會閃屏）
- **反應鏡頭**：同人 run 每 ~9s 插 1.8s 聽者點頭畫面再切回（audio 不斷）——
  修修 2026-07-26 二輪回饋「畫面變化太少」的解法，範本語法
- **內容驅動 punch（五輪裁決，取代機械 max_shot/zoom 交替）**：agent 從
  tight SRT 標「講重點」的區間寫 `<id>_zoom.json`（timeline 秒）→ 講重點
  時 **speed-ramp zoom-in**（0.4s 平滑曲線、+15%）、講完 ramp-out。機制：
  shot item 加 Fusion comp（MediaIn→Transform→MediaOut）、Size 關鍵影格，
  與 item 靜態 ZoomX 疊乘——一下子跳 zoom 是違規
- 開場 4 秒上下分割雙人畫面（來賓上、修修下——參考 E:\\data 鐘穎範本
  的開場語法），`--no-opener` 關閉
- **字幕細切**（修修 2026-07-26 三輪）：cue 再切成 5–9 字呼吸單元，詞級
  時間戳定界、單元首尾相接；空格 clause 優先切、括號不拆、助詞不開頭、
  連接詞（或/跟/但…）不懸行尾——範本的字幕翻頁節奏
- audio 與 Step 6 相同（同一份 cuts.json 保留段）

**執行順序**：director 重跑會整條重建 timeline——titles 巢狀層會被洗掉，
**必須 director → titles 順序重跑**。

**換集校準**：機位固定、臉部座標全集通用，但**換集必校**——抓各機位一幀
量臉部中心 x，寫 `highlights/tighten/director.json` 覆蓋 `face_x`（格式見
script DEFAULT_CFG）；先跑一支 `--stills` 看樣張確認構圖再跑其餘。

**Resolve transform 語意是實測出來的**（Crop=fit 畫布 px 隨 zoom 縮放、
Pan 1:1、Tilt ×0.3164 且不隨 zoom——見 script 常數註解），改構圖參數後
必用 `--stills` 樣張驗證，不能只信計算。

## Step 8 — punch 卡（graphics 層，修修 2026-07-26 四輪裁決）

title 不走 subtitle track（單一 style 天花板），走 Fusion Text+ graphics：
橘底大字（#E87000 逐行塊）、白 Noto Sans TC Black、fade+pop 進出場。
每支短片 2–3 張，**文字必須是講者原話**（範本語法：verbatim 片段）。
卡片紀律（修修五輪）：**顯示 ~2s 就退**（太慢消失會煩）、`pos_y` 0.63
下移避臉（punch zoom 時臉佔上半屏，0.55 會擋臉）。

流程：agent 從 `<id>_tight` SRT 選 punch 時間點 → 寫
`highlights/tighten/<id>_titles.json`（t0/t1 = 緊·導播 timeline 秒）→

```
python scripts/run_short_titles.py <episode> --id <winner-id> --stills <dir>
```

機制限制（實測，見 script docstring）：InsertFusionTitle 是插入模式，卡片
放獨立「titles - <id>」timeline 巢狀疊 track 3（巢狀空白透明）；生成器
固定 5s，顯示窗口靠 Opacity 關鍵影格（Lua comp:Execute）——**相鄰卡片
t0 至少差 5 秒**，違反 script 會擋。樣張必驗（--stills）。

## 修修換段時

改 `winners.json`（換 id/rank）→ 重跑 `--materialize`（冪等，30 秒）。
（緊）版重出：改 cuts.json → 重跑 `--apply --id`（冪等，同名重建）；
（緊·導播）版同理重跑 `run_short_director.py`。

## v2 備忘（不做，見 plan 文件）

cold-open 重排、直式字幕模板、訪談留言補掃校 persona、（緊）流程套用到
長片（長片節奏容忍度高，等修修看完短片版成效再決定）。
短片設計資產層（參考 E:\\data 鐘穎範本解剖，2026-07-26 分析）：橘色塗鴉框版式、字幕關鍵詞高亮、B-roll 插入點——等修修看完
（緊·導播）版再定。
