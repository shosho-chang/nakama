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

## 執行環境

同 highlight-cut：**只能在 Claude Code + 本機跑**（Resolve 官方 Python
Scripting API，CoWork/Computer Use 做不到逐幀精度）。

- **Resolve Studio 執行中** + External scripting = Local
- Resolve script 一律 `py -3.10`（3.14 會 segfault）；pytest/ruff 用 `python`
- 從 worktree 跑要帶 `RESOLVE_SUBTITLE_TEMPLATE=E:\nakama\data\resolve\
  subtitle-template.drt`——`data/` 是 gitignored，只存在主倉庫，缺了字幕會無樣式
- hyperframes 卡片 render 可外包，疊軌仍要本機

## Step 6–7 — 緊湊化 + 機位導播（修修 2026-08-03 開工）

同一批 script，格式由 `candidates.json` 的 `format` 欄自動判定，**沒有新
CLI 旗標**：

```
python scripts/run_short_tighten.py <episode> --detect --id punch-L5
# → agent 複審 keep=null（長片只會有 stutter，filler/backchannel 不偵測）
python scripts/run_short_tighten.py <episode> --apply  --id punch-L5
python scripts/run_short_director.py <episode> --id punch-L5 --stills <dir>
```

參數表：`run_short_tighten.FORMAT_TIGHTEN` / `run_short_director.FORMAT_CFG`。

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
| **滿版轉場卡** | 導航 | `transition_title_wide` + `style:"paper_hand"`（**B2 定版**）＝**紙紋 motion bg 滿版蓋掉畫面（92% 不透明）**＋ink 字置中＋手繪 kicker 短槓＋**等字長手繪底線**（字數×字級算，不量 DOM——字型載入時序在 capture 下不可靠）＋退場動畫（title 上滑出）。落每個主題切換點 **3.0s**（退場要收在 span 內）。`kind:"concept"` + `comp:"transition_title"`。⚠️ 滿版底必須是「元素」：body 背景在 alpha 渲染會被丟掉（v1 黑字裸壓實拍壓臉 bug 的根因）；paper 系是透明字卡，`run_short_broll` 以 ffmpeg 疊 `assets/broll/paper-texture.mp4`（源：Envato beige paper texture，4K 預縮 1080p crf12）預合成 `_tex.mov`；`style:"scrim"`（半透明暖灰+白字）自帶底不合成 | ~3–5 |
| **品牌 badge** | 識別 | 左下角 logo，**只出現開場（收在名牌進場前，如 7.4s）+ 每個轉場卡結束後 ~8s**。`kind:"badge"` + slug `brand-badge-7s`/`brand-badge-8s`/`brand-badge-10s`：**定長 fade 預合成**（180px、alpha fade in/out 0.5s——源動畫動作區僅 ~87×48px，150px loop 版感知不到「沒有動」）。ffmpeg `-stream_loop` + scale=180 + pad 66:840 + fade，鋪 track 5。源檔 `E:\Projects\張修修的AI創作者新世紀\output\podcast-logo-animation\` | 開場+轉場後 |
| **來賓名牌** | 介紹 | `chapter_label_wide` `align:"left"` + `sub` + `style:"paper"`＝半透明紙卡＋手繪橘豎筆觸＋**逐元素進退場**（卡落→tick 畫出→姓名滑入→頭銜淡入；修修：「整個區塊一起跑出來沒經過設計」）。落**來賓第一個實質單獨鏡頭內**（查 timeline v1 軌首個 ≥3s 的 CAM2 段，貼切點進、退場收在段內；碎片鏡頭 <1s 掛不了名牌）。⚠️ 開場 badge 窗必須在名牌進場前收掉——左下角同框=擠 | 1 |
| **論文第一頁卡** | 信任感 | 真 PDF 第一頁彈入（`sticker_pair_wide` center 模式；禁 stock 代打）。唯一的證物類型（書封/人名/數據卡都不做——grill 裁決） | 提到具體研究時 |
| **Hero 大字卡** | 錨點 | `punch_card_wide` tier1 150px + `style:"paper"`（FORMAT_TITLES long 預設）＝半透明紙白卡＋ink 字＋**橘色手繪畫線動畫**（SVG 描邊，進場後 0.75s 逐行由左畫到右）。**我提案附原話+時間點 → 修修裁決 → 才 render** | 2–4 |
| **滿版 stock** | 情境具象化 | 描述情境的時刻滿版實拍。**選點走演算法不逐支請示**（修修：「這套策略要能應用在之後的長影片」）：① `run_short_review` 的 `content_gaps` 掃真空段（>75s 無強事件；換鏡是弱事件不算）② 每段塞 1 支、段長 ≥100s 最多 2 支且間隔 ≥40s ③ 選句優先序：**「比方說/例如」舉例句 > 具體可拍場景（動作/地點）**，抽象論述不選、關鍵論點段不蓋 ④ 橫式 4K 實拍（Envato）、主題不與已用素材重複、3.5–4.5s、`src_in` 跳廢頭 ⑤ 密度=分佈優先，非固定總量；塞完重掃殘餘真空迭代到收斂 ⑥ **觸發 ≠ 必塞**：真空段內找不到具體情境句（全是抽象論述/書的內容）→ 保留 talking head 並記錄——硬湊是 v1 整版被打掉的根因。直式素材只有特寫類能裁著用 | 每 75s+ 真空段 1–2 |

**退場語彙**（長片不用）：橘塊 style（保留當 `style:"orange"` 比較基準）、
章節籤（center 模式保留在 composition 裡但語彙上退役）、tier2 卡、貼紙、
概念卡、Ken Burns、BGM。聲音只配重點事件（hero=ding / 證物=pop / 轉場卡
=swish / badge 與 stock 無聲）。**稀疏是設計**，自檢包只看 QC（遮擋/同步/
樣式），密度指標無意義——但 `content_gaps` 的素材真空掃描有意義（見上表）。

### 執行

```
# titles.json 只放 tier1 hero；broll.json 放轉場卡/名牌/論文卡/badge/stock
python scripts/run_short_titles.py <episode> --id punch-L5
python scripts/run_short_broll.py  <episode> --id punch-L5 [--stills <dir>]
python scripts/run_short_sfx.py    <episode> --id punch-L5     # 不跑 bgm
python scripts/run_short_review.py <episode> --id punch-L5
```

軌道契約：v1 主鏡（導播）/ v2 滿版 stock / v3 hero / v4 名牌+轉場卡+論文卡 /
v5 badge；a1 對白 / a2 SFX。

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
