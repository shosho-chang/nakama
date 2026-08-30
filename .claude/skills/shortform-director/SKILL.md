---
name: shortform-director
description: >
  短片（60–120s 直式 Shorts）專屬分鏡導演手冊（ADR-067）。Triggers:
  /shortform-director、「短片排 B-roll」、「跑短片 Director」。讀該支的 tight SRT →
  逐 cue 決定哪幾句要具象畫面 → 產 `<id>_broll.json` 的意圖層（source_cues、shot、
  情緒極性、景別）交 shortform-dp 落地。**不服務長片**——長片走 brook-director。
  創意判斷在本手冊；gate 與物化契約歸 shared/shortform_broll.py 與
  scripts/run_shortform_broll.py，本 skill 只呼叫、不重新發明。
---

# shortform-director — 短片分鏡導演手冊

**版本：v1.0（2026-08-30，ADR-067 長短分家）**

修修 2026-08-30 裁決：

> 「短影音的 Director 跟 DP 應該是要分開才對，因為他們做出來的素材跟剪輯的
> 緊湊程度完全都是不一樣的。」

所以這本手冊**只服務短片**。長片的分鏡在 `brook-director`，兩本不共用節奏預算、
不共用畫幅、不共用素材庫。改這本不會動到長片，反之亦然。

## 短片與長片的差別（為什麼要分開）

| | 短片（本手冊） | 長片（brook-director） |
|---|---|---|
| 畫幅 | **9:16 直式**——素材必須是直的，橫的裁進去只剩中間一條 | 16:9 橫式，素材鎖 Horizontal |
| 片長 | 45–120s | 8–12 min |
| 字卡 | mode B：逐子句字卡承接**全部**逐字稿，不上字幕軌 | 選擇性金句卡＋底部字幕 |
| 視覺事件密度 | 見下「密度在 mode B 之後怎麼算」 | 4.5–5.5 事件/分 |
| stock 支數 | 全片 **2–3 支**，每支 1.5–4s | 逐章鋪，數十支 |
| 章節/Hero/書封 | 沒有——短片沒有導航需求 | 有 |
| 素材授權 | ADR-067 gate（收據＋SHA-256＋直式＋逐字稿錨定） | ADR-065 Director/DP/語意稽核收據鏈 |

## 紅線

1. **直式是硬條件**（修修 2026-08-30 兩度強調）：候選頁就要鎖 Vertical，
   `height > width`。橫的素材連進候選清單都不該進——不要想著「裁一下就好」，
   ADR-064 cutover 造成的「硬把橫的切成直的」就是這樣來的。
2. **落點對齊那句話**：每個素材宣告 `source_cues`，t0/t1 必須包在那幾句的時間裡。
   gate 會驗（`shared/shortform_broll.py`）；驗不過不是調數字，是重新想這支素材
   要對哪句話。
3. **寧缺勿猜**：沒有語意精準的直式素材就留 talking head。**錯的 stock 比沒有差**
   ——短片只有 2–3 個 cutaway，錯一個就是三分之一。
4. **不可蓋掉 punch zoom**：punch 是視覺重音，被 B-roll 蓋住等於白做。衝突時
   **縮短 punch 讓位 footage**（改 `<id>_zoom.json`），不是讓兩層疊上去。
   gate 會擋。
5. **不可壓到開場上下分割**：開場 4 秒的雙人分割佔的就是 track 2，素材沒有位置。
6. **不改逐字稿**：字卡是 mode B 的逐字承接，Director 不碰字卡文字；本手冊只決定
   「哪幾句要有畫面、畫面要表達什麼」。
7. **跨集不重複**：選定素材前掃各集 `assets/broll/` 的 SHA-256 與收據 `source_url`，
   命中就換候選。全片一片一用。

## 密度在 mode B 之後怎麼算

highlight-cut SKILL Step 9 寫的是「短片每分鐘 6–9 個視覺事件（B-roll＋貼紙＋
概念卡＋**字卡**合計）」。那是**字卡稀缺時代**的算法——mode B 之後字卡逐子句出現，
46 秒就有 20 個 state，換算 26 事件/分，這條指標永遠自動達標，於是失去意義。

**mode B 的密度改用另一把尺：具象覆蓋率。**

逐 cue 掃一遍，把句子分成兩類：

- **具象句**：有可拍的名詞／動作／場景（「狗也愛玩」「連鳥都愛玩」「小孩子喜歡學習」）
- **抽象句**：論證、連接、定義（「玩是一種模擬」「玩其實就是一種學習」）

目標是**每個具象句都有畫面**，抽象句一律留 talking head（字卡已經在扛）。
一支 45–60s 的短片通常只會挑出 2–3 個具象落點——那就是正確的數量，
不要為了湊密度把抽象句硬配 stock。

## 前置條件

| 檔案 | 來源 | 缺了怎麼辦 |
|---|---|---|
| `highlights/srt/<id>_tight_r*.srt` | `run_shortform_director.py` | 先跑導播 |
| `highlights/tighten/<id>_titles.json` | `author` 字卡企劃 | 先排字卡（要知道 `split_opener_sec`） |
| `highlights/tighten/<id>_zoom.resolved.json` | `run_shortform_director.py` | 先跑導播（要知道 punch 區間） |

三份都是**同一輪**的產物。`_zoom.resolved.json` 裡的 `srt` 欄位必須等於最新的
tight SRT 檔名，否則 `run_shortform_broll.py` 會擋——舊的 punch 區間對不上新的句子。

## Step 1 — 逐 cue 標具象落點

讀最新 tight SRT，對每一句問：**不看字幕，這句話有沒有一個畫面能讓人讀懂它？**

| 稿面信號 | 決定 | 備註 |
|---|---|---|
| 具體名詞＋動作（狗在玩、鳥在玩、小孩在拼積木） | `video` | 短片的主力，1.5–4s |
| 具體物件／證物（書封、論文首頁） | `photo` | Ken Burns 慢推 |
| 抽象概念、論證、連接句 | 無 | 字卡已經在扛，留 talking head |
| 情緒轉折但無可拍主體 | 無 | 交給 punch zoom，不要用 stock 代打 |

每個落點寫下四件事，缺一項就是沒想清楚：

1. **`source_cues`**：對哪幾句（連續）
2. **`shot`**：畫面裡有什麼——主體、動作、景別。「狗」不算，「狗叼玩具甩頭、
   直式中景」才算
3. **情緒極性**：這句話是興奮／困惑／壓力／輕鬆？畫面極性必須一致
   （brook-dp〈選片鐵則〉3 的判準跨格式共通）
4. **為什麼是這句而不是隔壁句**：B-roll 強調的是「正在陳述的那句話」

## Step 1.5 — 開場品牌 LOGO（修修 2026-08-30）

上下分割那 4 秒放頻道 LOGO 動畫。素材是品牌資源裡的
`Logo/animation/podcast_rounded_card_*.mp4`——**1080×1080 淺灰底、沒有 alpha**，
直接疊會出現灰方塊。先跑：

```
python scripts/build_brand_logo_badge.py <episode> [--width 620]
```

它把白卡切出來、依圓角半徑重建 alpha、pad 進 1080×1920 透明畫布，位置烘焙在檔案裡
（同 `brand-badge-8s.mov` 慣例）。預設落在**上下分割的接縫**上——避開兩個人的臉，
也不撞底部字卡（opener 期間字卡被強制在 pos_y ≥ 0.84）。

然後在 `<id>_broll.json` 加一筆 structural item，**不需要授權收據**（自家品牌資產）：

```json
{"kind": "badge", "slug": "brand-logo-opener", "t0": 0.0, "t1": 3.933}
```

`t1` 取 LOGO 動畫的實際長度；它走 video track 5（最上層），一次播完不循環。

## Step 2 — 排落點與時間

- 每點 **1.5–4s**。短片沒有 6.5s 的貼紙那種長度
- t0/t1 必須落在 `source_cues` 的時間範圍內（容差 0.35s）
- 兩個 cutaway 之間留談話呼吸——不要連著兩句都切走
- 避開：punch 區間、開場 0–`split_opener_sec` 秒
- cue 很短時（例如「狗也愛玩」只有 0.93s）可以把相鄰同語意的句子併成一個
  `source_cues` 區段，讓畫面有 1.5s 以上——但併進來的句子必須真的同語意

## Step 3 — 產意圖層

寫 `highlights/tighten/<id>_broll.json`：

```json
{
  "_intent": "整支的具象落點盤點與「為什麼其餘段落不配」的理由",
  "items": [
    {
      "kind": "video",
      "slug": "<素材 slug，與 assets/broll/<slug>.* 同名>",
      "t0": 19.2, "t1": 21.25, "src_in": 2.0,
      "source_cues": [9],
      "_shot": "玄鳳鸚鵡在遊戲架上撥玩具球——「甚至連鳥都愛玩」的字面畫面",
      "_mood": "輕快、好奇"
    }
  ]
}
```

底線開頭的欄位是給人看的意圖紀錄，gate 不驗但**必須寫**——沒有它，下一輪
沒人知道這支素材為什麼在這裡。`slug` 由 DP 在取得素材後回填；Director 階段
可以先寫預期的 slug 並在 `_wanted` 區塊記下還沒有素材的落點。

交給 **shortform-dp** 找片、驗收、回填。DP 覺得某個意圖畫不出來 → 退回本手冊
重判，不自己改意圖。

## Step 4 — 驗收

```bash
py -3.10 scripts/run_shortform_broll.py <episode> --id <id> --validate-only
```

過了才 `--stills` 實跑。樣張逐張看：構圖有沒有被裁掉主體、有沒有綠幕/浮水印、
有沒有蓋到字卡。

## 每支寫回

`_intent` 裡累積「這集為什麼這樣配」；手冊層級的教訓（新的負面清單、新的
觸發信號）寫回本檔並記日期。
