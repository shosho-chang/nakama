---
name: Brook 風格 - IG carousel
description: IG carousel 短知識卡片風格（AIDA 4-stage adaptation + 4 episode_type sub-template）
agent: brook
category: ig-carousel
extracted_at: 2026-05-01
framework_source: |
  AIDA-derived 4-stage（Bait / Hook / Reel / Land）adaptation
  inspired by Chris Do / The Futur 在 IG carousel 設計上的 atomic-idea-per-card 原則。
  注意：「Bait/Hook/Reel/Land」非 Chris Do 官方框架名稱，是修修
  2026-05-01 採用的 IG-circle 通用 fishing-metaphor 4-stage 改編；底層
  仍是經典 AIDA（Attention/Interest/Desire/Action）。
sample_size: 0  # 無實際 carousel 樣本，純框架 derive；待 Slice 8 IGRenderer 試跑後補實證樣本
---

# Brook IG carousel 風格側寫

> 修修方針（2026-05-01）：IG carousel 是「把重點擷取出來」，不依賴敘事或文體 voice，靠 4-stage adaptation + episode_type 對應卡數。**寫作 voice 不是 IG 的主軸**——簡短、視覺、易截圖才是。

> **單位約定**：本檔所有「字」= 中文字元數（CJK chars，不含空白），不是英文 word。

---

## 1. 框架定位 — 4-stage AIDA adaptation（fishing metaphor）

底層是經典 AIDA（Attention/Interest/Desire/Action），用 fishing 比喻 rebrand 為四階段，貫穿任何 carousel：

| 階段 | 中文 | AIDA 對應 | 卡片位置 | 任務 |
|---|---|---|---|---|
| **Bait** | 餌 | Attention | 封面卡（C1） | 在 feed 裡攔下手指，**讓人停下來而不是滑過** |
| **Hook** | 鉤 | Interest | C2-C3（早段） | 把問題具體化、製造資訊缺口（curiosity gap） |
| **Reel** | 收線 | Desire | C4-C(N-1)（中段） | 給內容主體，每卡一個 atomic idea，**節奏穩定不混亂** |
| **Land** | 落地 | Action | 最後 1-2 張 | 結論／行動／CTA，明確告訴讀者下一步做什麼 |

每張卡片**只服務一個 atomic idea**——一張卡 = 一個訊息。違反這個原則就是 carousel 失敗的最大原因。

> **參考來源**：Chris Do / The Futur 在 IG carousel 教學中強調 atomic-idea-per-card、cover-stop-the-scroll、low-text-high-visual；本檔的 4-stage 命名（Bait/Hook/Reel/Land）非 Chris Do 官方詞彙，是 IG 設計圈的通用 fishing 比喻改編。Slice 8 IGRenderer 引用此 profile 時請以 AIDA 為認知錨點，Bait/Hook/Reel/Land 為命名工具。

---

## 2. 四種 episode_type 子模板

對應 Line 1 podcast Stage 1（`agents/brook/line1_extractor.py:55`）抽出的 `episode_type` 欄位，每種對應一個固定卡數結構：

> **語意對齊提醒**：Stage 1 的 `episode_type` 是**來賓本集敘事結構**的分類（podcast EP 主敘事），不是「post 結構」。Stage 2 IGRenderer 的合理選擇是**讓 carousel 結構鏡射 EP 敘事結構** —— 來賓人生弧線本集 → narrative_journey 5 卡 carousel；來賓本集主軸是破除某迷思 → myth_busting 7 卡 carousel；以此類推。下方所有 few-shot **皆以來賓為主體**，不能用「修修自己的故事」當例（那不是 Stage 1 會輸出的內容）。

### 2.1 `narrative_journey` — 5 卡（個人故事 / 轉變弧線）

```
C1  封面     │ 反轉 hook：「我以為 X，後來發現 Y」/ 戲劇性數字
C2  起點     │ 故事的「混亂時期」（before）— 1 句場景 + 1 句感受
C3  轉折     │ 觸發轉變的關鍵事件 / 對話 / 體悟
C4  收穫     │ 從中得到的「一個關鍵洞察」（不是 3 個 takeaway）
C5  落地 CTA │ 「DM 我『轉變』」/「saved 給未來的自己」/「聽 Podcast 完整版」
```

**適用**：podcast 嘉賓的 origin story、修修自己的人生轉折、來賓如何從谷底爬起。
**字數密度**：故事感重，每卡可達 12 字上限。

### 2.2 `myth_busting` — 7 卡（迷思破解）

```
C1  封面     │ 「你以為 X，其實是 Y」反差句 / 數字反轉
C2  迷思     │ 「90% 人以為⋯⋯」具體陳述常見誤解
C3  反證 1   │ 研究／數據／案例反駁
C4  反證 2   │ 第二個角度反駁（不同來源）
C5  真相     │ 一句話總結「真正的真相是⋯⋯」
C6  應用     │ 「所以你應該／你可以⋯⋯」具體行動
C7  落地 CTA │ 「saved 收藏」/「DM 想知道更多」/「聽 EP{n} 完整解析」
```

**適用**：營養學迷思（不吃早餐 / 168 斷食）、運動科學（不能空腹跑步）、心理（情緒勒索）。
**字數密度**：說理感重，封面 ≤10 字、中段 ≤12 字、落地 CTA 嚴守 §5 表上限（不放寬）。如資訊量超出，拆兩張卡而不是放寬字數。

### 2.3 `framework` — 5 卡（架構/工具教學）

```
C1  封面     │ 「{X}的 N 步驟」/「{大師} 的{框架名}」
C2  定義     │ 框架是什麼、解決什麼問題（一句話）
C3  步驟 1+2 │ 兩個步驟並列（如果框架 ≥4 步驟，拆成兩卡）
C4  步驟 3+N │ 後續步驟（含「執行重點」一句話）
C5  落地 CTA │ 「實作模板 DM 索取」/「圖文存檔對照」/「聽 Podcast 細節」
```

**適用**：八角框架、薩提爾冰山、SMART 目標、深度工作四象限。
**字數密度**：高密度資訊型，**封面 ≤10 字嚴守**，內頁可以視覺化（流程圖、編號列表）。

### 2.4 `listicle` — 10 卡（清單型）

```
C1  封面     │ 「{N}個{對象}你必須知道」/「{年份}{topic} 必讀清單」
C2-C9  項目 1-8  │ 每卡一個 item，封面 4-6 字（item 名）+ 1-2 句說明
C10 落地 CTA │ 「全清單存檔」/「DM 想要 PDF」/「Podcast 詳細介紹」
```

**適用**：年度書單、必看影片、必試工具、健康習慣 8 招、運動 app 推薦。
**字數密度**：低密度高頻率，**每卡簡短就好**（≤8 字標題 + ≤12 字說明）。

> **episode_type 來源**：Line 1 Stage 1 由 LLM 從 podcast SRT 抽出（見 `agents/brook/line1_extractor.py:130-134` 的 inline prompt 已定義 4 type 判準）。傳入 IGRenderer 後決定走哪個子模板，不再 router-fallback 誤分類。

---

## 3. 卡 1（封面）Hook 公式庫

**目的**：在 feed 裡 0.5 秒攔下滑動。**字數硬限 ≤10 字**（封面太長字會被自動縮小、視覺破功）。

健康／心理／longevity 領域 12 個 hook 公式 + 範例：

| # | 公式 | 範例 | episode_type |
|---|---|---|---|
| 1 | 反轉式：「你以為 X，其實 Y」 | 早餐很重要？錯了 | myth_busting |
| 2 | 數字 shock：「N% 的人不知道」 | 90% 人補錯維他命 D | myth_busting |
| 3 | 點名警告：「這 N 件事在傷害你」 | 5 件事正在毀你睡眠 | listicle |
| 4 | 大師背書：「{權威} 的 N 步驟」 | Peter Attia 的 4 步法 | framework |
| 5 | 戲劇問句：「為什麼{反直覺}？」 | 為什麼運動還變胖？ | myth_busting |
| 6 | 個人轉變：「我從 X 變成 Y」 | 我從失眠變每晚秒睡 | narrative_journey |
| 7 | 對比清單：「N 個習慣，N 個結果」 | 5 習慣讓你晚老 10 年 | listicle |
| 8 | 工具收藏：「N 個工具救我命」 | 8 個 app 救我焦慮 | listicle |
| 9 | 反問鉤：「你也{常見痛點}嗎？」 | 你也半夜醒來嗎？ | narrative_journey |
| 10 | 引言式：「{大師} 說的這句話⋯」 | Naval：別追蹤新聞 | framework |
| 11 | 時間框：「{時長}{達成}」 | 10 分鐘改善睡眠 | framework |
| 12 | 反清單：「不要做這 N 件事」 | 不要在運動前吃 | listicle |

**Hook 寫作原則**：
- 具體 > 抽象（「90%」勝過「很多人」）
- 反直覺 > 老生常談（「早餐很重要？錯了」勝過「健康早餐選擇」）
- 數字醒目（「5 件事」「10 分鐘」「N% 的人」）
- 不堆關鍵字（IG 不靠 SEO，靠 hook）

---

## 4. CTA 模式選單（最後一卡）

5 種 CTA pattern，依 episode_type 與 podcast 整合度選擇：

| CTA 類型 | 適用 | 範例 |
|---|---|---|
| **Save** 收藏 | listicle / framework / myth_busting | 「Save 給未來的自己」 |
| **Share** 分享 | myth_busting / 議題倡議 | 「Tag 一個需要看到的朋友」 |
| **Follow** 追蹤 | narrative_journey 結尾 / 系列預告 | 「Follow @shosho.tw 持續解鎖」 |
| **Podcast 引流** | 與 podcast EP 對應的 carousel | 「完整版聽 EP{n}🎧」 |
| **DM trigger** | framework / listicle 有「資源包」可給 | 「DM『睡眠』我給你完整 PDF」 |

**組合規則**：每張落地卡 **1-2 個 CTA**，不能同時擺 5 個（讀者選擇癱瘓）。
**主 CTA + 次 CTA**：podcast 引流（主）+ Save（次）為最高頻組合。

---

## 5. 字數規範（硬限）

| 位置 | 上限 | 說明 |
|---|---|---|
| **封面卡標題** | **≤10 字** | 字大、視覺重；超過會被 IG 自動縮放破壞層次 |
| **中段卡標題（H1）** | **≤12 字** | 框架名／步驟名／item 名 |
| **中段卡內文** | **≤80 字／卡** | 一個 atomic idea 的說明，分行可達 3-4 行 |
| **整篇 carousel 總字數** | **150-300 字** | 含全部卡片所有字（封面 + 中段 + CTA） |

**超字怎麼辦**：
- 封面超 10 字：拆 hook（前 8 字 hook + 後段挪到 C2）
- 中段超 80 字：拆成兩卡（同一個 idea 但用不同角度／例證）
- 總字超 300：減少 listicle item 數（10→8）或刪 myth_busting 反證之一

---

## 6. 卡片設計慣例（給 designer / 模板）

> Brook 只負責**文字**輸出，視覺設計交由 Claude Design 或修修自己處理。但文字輸出時要符合視覺友善的格式：

- **每卡用獨立 markdown section**（`## C1: 封面`、`## C2: 起點` ⋯⋯），方便對照排版
- **標題 / 副標 / 內文標明**（`### Title:`、`### Subtitle:`、`### Body:`），讓 designer 知道字級層次
- **不在文字裡塞圖標／emoji 當裝飾**（圖標走視覺層）
- **品牌固定元素**（logo、handle、brand color hex）不由 Brook 輸出，由模板加上

範例輸出格式（給 IGRenderer 參考）：

```markdown
## C1: 封面（Bait）

### Title: 早餐很重要？錯了

### Subtitle: 5 個你以為的健康早餐其實是甜點

---

## C2: 迷思（Hook）

### Title: 「不吃早餐會變胖」？

### Body:
這句話在 1944 年由 Kellogg's 廣告部編出來
從來沒有 RCT 研究支持
（直到 2017 年才被推翻）
```

---

## 7. Few-shot 範本

### 範本 A：myth_busting 7 卡（Cal Newport《深度工作力》延伸）

```
C1 封面：你 5 小時的工作只值 1 小時？
C2 迷思：「我每天工作 8 小時很努力」
C3 反證 1：研究（Newport 2024）：知識工作者深度時間 < 4hr/day
C4 反證 2：剩下 4hr 是「淺工作」，產出價值 < 20%
C5 真相：每天 4 小時深度工作 = 真實生產力
C6 應用：早晨 9-12 不開會、不滑手機、單一任務
C7 落地：Save 給每天忙到崩潰的自己 / 聽 EP{n} 完整版
```

### 範本 B：narrative_journey 5 卡（來賓周慕姿 30 歲心理諮商裸辭）

```
C1 封面：30 歲，她裸辭去考心理諮商
C2 起點：政大新聞畢，中華電信穩定工作
C3 轉折：「再不動，我這輩子就這樣了」
C4 收穫：今天她創辦諮商所、寫暢銷書、開 Podcast
C5 落地：完整版聽 EP97 周慕姿 / Save 給卡關的自己
```

> **重點**：narrative_journey 永遠寫**來賓**的人生弧線（Stage 1 `origin → turning_point → rebirth → present_action` 四欄即此弧線素材），不是修修自己的。落地 CTA 走 podcast 引流（EP 編號）+ 收藏動機。

### 範本 C：framework 5 卡（八角框架簡介）

```
C1 封面：周郁凱八角框架（讓人上癮的科學）
C2 定義：8 個動機驅動人類所有行為
C3 步驟 1+2+3+4：意義 / 成就感 / 創造力 / 所有權
C4 步驟 5+6+7+8：社交影響 / 稀缺性 / 不確定性 / 失去逃避
C5 落地：DM「八角」我給你應用模板 / EP{n} 完整訪談
```

### 範本 D：listicle 10 卡（來賓徐國峰 跑步教練的 8 個訓練心法）

```
C1 封面：跑步教練的 8 個訓練心法
C2 1：先呵護自己愛跑步的心
C3 2：心 / 體能 / 力量 / 技術 四要件並重
C4 3：循序漸進，跑量不堆太快
C5 4：用八成力跑，享受過程
C6 5：休息也是訓練
C7 6：把運動科學翻譯成可執行
C8 7：融入儒家、道家哲學練心
C9 8：以「跑到老」為長期目標
C10 落地：完整版聽 EP{n} 徐國峰 / DM「跑步」我給課程連結
```

> **重點**：listicle 用**來賓**的成果／教學整理（Stage 1 `quotes`、`present_action` 派生）。如該 EP 的 `episode_type=listicle`，Stage 2 IGRenderer 從 `quotes` / `present_action` / `rebirth` 三欄抽 N 條展開即可。**注意**：Stage 1 schema 沒有專屬的 `framework_steps` 欄位，IGRenderer 對 `framework` / `listicle` type 都得從文本欄位裡再抽，請在 Slice 8 prompt 內補一段 sub-extraction 指引（或開 follow-up issue 擴 Stage 1 schema）。

---

## 8. 必做清單

- [ ] 走 4 episode_type 子模板之一（narrative_journey 5 / myth_busting 7 / framework 5 / listicle 10）
- [ ] 封面卡 ≤10 字、中段標題 ≤12 字、總字數 150-300
- [ ] 每卡只服務一個 atomic idea
- [ ] AIDA 四階段都對應到（Bait / Hook / Reel / Land）
- [ ] 落地卡 1-2 個 CTA（不超過 2 個）
- [ ] Hook 走 §3 公式庫之一（具體、反直覺、數字醒目）
- [ ] podcast 引流 carousel 必含「聽 EP{n} 完整版」CTA

## 9. 禁止清單

- [ ] 禁止單卡塞多個 idea（會視覺擁擠、讀者跳 carousel）
- [ ] 禁止封面 > 10 字（會被 IG 縮放破壞層次）
- [ ] 禁止把 podcast 全劇透（IG 是預告片不是 transcript）
- [ ] 禁止把 carousel 寫成 FB 短文／部落格節錄（不同媒介、不同節奏）
- [ ] 禁止 hashtag 堆疊在卡片內（hashtag 寫在 caption，不在卡片視覺裡）
- [ ] 禁止抽象 hook（「健康新觀念」「你需要知道的事」這類無資訊量句）
- [ ] 禁止 5 個 CTA 並列在落地卡（讀者癱瘓）
- [ ] 禁止單卡放整段反思散文（每卡一個 atomic idea；narrative_journey 仍可用第一人稱單句，但禁止 wall-of-text）

---

## 10. 與其他類別的關係

IG carousel 在 Brook 風格地景的位置：**最短、最結構化、最不靠 voice**。

| 維度 | 📖 書評 | 🎙️ 人物文 | 🧪 科普 | 📘 FB 短文 | 📱 IG carousel |
|---|---|---|---|---|---|
| 字數 | 5,000-12,500 | 1,000-4,000 | 4,000-7,000 | 800-3,500 | **150-300** |
| 結構彈性 | 高 | 中（章回 + 金句） | 中（四拍） | 中（兩骨架） | **極低（4 子模板硬綁卡數）** |
| Voice 重要度 | 高 | 高 | 中 | 高 | **低（修修明示）** |
| 商業 CTA | 含蓄 | 不出現 | 不出現 | 直接 | **直接（Save / DM / Podcast）** |
| 視覺主導 | 否 | 否 | 否 | 否 | **是（封面字大、版面為主）** |

**IG carousel 的本質**：把 podcast 內容的「重點」用視覺/結構編碼，**voice 是噪音不是訊號**。這個媒介要快、要清楚、要被截圖收藏。修修的 voice 留在 FB / 部落格 / podcast 裡。
