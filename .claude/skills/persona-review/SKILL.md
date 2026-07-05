---
name: persona-review
description: >
  派多個 TA/受眾 persona subagent 平行「盲審」一份內容產出（課綱、逐字稿、銷售頁、文章、
  IG 圖卡、簡報），每位依 rubric 給分數＋附原文引句的回饋，主 agent 逐句查證後收斂成
  P0–P2 優先修改清單。Use when the user wants persona review / TA 審稿 / 讀者視角 /
  focus-group critique，says 「讓 TA 看一次」「派 subagent review」「目標受眾會怎麼看這份 X」，
  wants a draft scored from audience perspectives，wants a second-round re-review of a
  revision against前次疑慮，or wants multiple versions blind-compared to pick a winner.
argument-hint: [受審文件路徑] [persona set] [rubric]
---

# persona-review — 多 persona 盲審內容產出

一個人改自己的稿子會盲掉；真人焦點團體又貴又慢。這個 skill 用「多個互不知情的 persona 平行**盲審**＋主 agent 逐句**查證**」逼近焦點團體的效果，而且每一條批評都能追溯到原文的哪一行。沒有查證的 AI 回饋是幻覺產生器——**查證是整個流程的生死線**。

Skill 只出分數＋回饋＋修改清單，**不代改 artifact**——取捨權在委託人。

## 輸入契約（開跑前確認齊）

1. **artifact**：內容無關——文字檔、多檔拼裝（例「Ch1–3 舊版＋Ch4–10 新版」）、或逐卡 PNG 序列。多檔拼裝必須明確記下每份路徑與 persona 該讀的行數範圍。
2. **persona set**：從 [personas/](personas/) 選一組（每檔一個 persona，frontmatter 有 `set`）；沒有現成的走下面 Step 1。`status: draft` 的 persona 未經修修凍結，用之前提醒。
3. **rubric**：從 [rubrics/](rubrics/) 選一個 profile。**權重只住在 rubric 檔裡**——prompt、報告都引用它，不複寫。
4. **輪次**：一輪初審／二輪覆核（需前次報告路徑）／多版本盲選（≥2 版同一 artifact）。
5. **domain constraints**：呼叫方情境（品牌規則、目標行為）；有真實背景資料（訪談、策略文件）先讀。
6. **報告落點**：預設受審文件同層，`TA審稿-{對象}-{日期}.md`（二輪加 `二輪評分-`）。
7. **語言**：跟隨受審文件。模型預設繼承主對話。研究員 agent（查數據出處／探勘遺漏族群）預設不派，使用者要求才加。

## Steps

### 1. 建 persona（只在沒有現成 set 時）

依 [personas/_guide.md](personas/_guide.md) 從真實資料萃取五要素。寫好先給使用者過目確認再跑——persona 錯，全部白跑。新 persona 一律 `status: draft`。
背景資料只融進人設，**不要**餵給 persona 當「答案」（不要寫「你認為 5.6 有矛盾」）；發現要從人設自己長出來。
**完成判準**：每個 persona 都有「判準一句話＋流失點清單」，且使用者已確認。

### 2. 準備

讀完 artifact，記下總行數與章節結構；讀 persona set 與 rubric 檔。
**完成判準**：能對每個 persona 寫出精確的閱讀指示（哪些檔、哪些行、「517 行讀全部」這種精度），漏一份拼裝檔就是沒完成。

### 3. 平行盲審

**同一則訊息**發出全部 persona 的 Task（研究員如有也同批）——盲審的統計意義來自獨立性：persona 互不知情、fresh context、絕不讓 A 看到 B 的輸出；多版本盲選時 prompt 不含版本序。
Prompt 用 [references/prompt-templates.md](references/prompt-templates.md)（一輪模板 A、二輪模板 B、研究員模板 C），代入 persona 全文、閱讀指示、rubric。組 prompt 時逐項檢查：

- **佔位符**：artifact 裡的示意數字（時數、費用〔示意值〕）→ 指示 persona 評內容與節奏、評「有沒有這個機制」與「信任這些數字的條件」，不挑數字真偽。否則火力浪費在你本來就知道是佔位的東西上。
- **設計註解分流**：artifact 含給團隊看的註解（「為什麼這樣改」「〔待拍板〕」）→ 明示 persona 以內容為主判斷、註解當背景，否則會把設計理由當賣點來評。
- **卻步點**：輸出格式必含 1–3 個「即使高分仍卻步的地方」——防好好先生；9 分的卻步點往往是最後一哩的精確地圖。
- Obsidian `[[wikilink]]`／`![[image]]` 語法指示 persona 忽略，不當缺陷回報。

**完成判準**：全部 persona 同批發出、輸出全數回收。

### 4. 查證（生死線）

Persona 回來後逐條核對，subagent 會產生看似合理的虛構引用，這一步是本報告與「AI 隨便誇/隨便罵」的唯一區別：

1. 先跑 `scripts/verify_quotes.py --review <persona輸出> --source <受審文件>...` 自動比對引句與小節編號——跑在**每份 persona 原始輸出**上，不是收斂後的報告；
2. 未命中清單逐條人工判讀——改寫式轉述允許，虛構不允許。校準經驗（2026-07 課綱 fixtures）：未命中最常見的是 persona 的自述語錄（「這不是給我的」類內心話），那不是原文引用，直接放行；真正要抓的是「宣稱引自文件、文件卻沒有」的字句；
3. persona 聲稱「文件沒寫 X」的主張抽查確實沒寫。

發現虛構 → 該 persona **整份作廢**，換個說法重下 prompt 重跑，並記入報告「驗證備註」。
**完成判準**：每條引句有 pass／近似／虛構 判定，每個 persona 有查證結論。沒跑完這步，報告不可交付。

### 5. 收斂與報告

依 [references/report-templates.md](references/report-templates.md) 產出，存檔後回報摘要。

- **優先序規則**：多來源收斂＝升級——兩個以上 persona 命中同一點，或 persona＋真實資料命中同一點 → P0；單一 persona 的合理痛點 → P1/P2。每條標層級：內容本身／行銷素材／需委託人拍板。
- **persona 全文照錄**，摘要另放——委託人要讀原話的溫度，二手摘要會磨掉細節。
- 安全/合規類舉旗當「訊號」呈報，不自動改稿——persona 有時吹毛求疵，取捨權在人。
- 「驗證備註」必寫：查證方法與發現＋「分數是方向性訊號、非市場數據」免責，建議與真實 beta 使用者互相校準。

**完成判準**：報告含分數表、各 persona 全文、P0–P2 收斂清單、驗證備註四節，缺一不可。

### 6. 二輪覆核（輪次＝二輪時）

同一批 persona，**prompt 內附各自前次的疑慮清單與分數**（舊帳）——這是二輪的靈魂：逼 persona 逐條交代「已解決／部分解決／未解決」並引新版小節為證。沒有舊帳，persona 會被新版糖衣帶著走，分數膨脹但不可追溯。
查證步驟（Step 4）照跑。報告用二輪模板：分數對照（前→後→Δ）＋疑慮解決率＋**最後一哩清單**（剩餘扣分分成：可再修的／只有委託人能拍板的／行銷層的——讓委託人一眼知道下一步在誰手上）。
**完成判準**：每條前次疑慮都有三態標記＋新版小節引證。

### 7. 多版本盲選（輪次＝盲選時）

每 persona 對每版獨立盲評（不知版本序）後，把分數整理成 JSON 餵 `scripts/score_ledger.py`：分數軌跡表、drop-off 彙整、argmax 與 blind pairwise 交叉檢查。兩版無明顯差異時**如實回報 plateau，不硬選**。
**完成判準**：argmax 與 pairwise 結論一致，或不一致處已標出供人裁決。

### 8. 停損建議

迭代繼續的條件：分數還在漲、且新一輪有可行動的新發現。出現以下任一訊號，主動建議停：剩餘扣分全落「委託人拍板」欄（流程已收斂）；分數連兩輪持平；persona 開始重複同樣的話。
先例節奏：逐字稿三輪 6/6.5/6→7/7/7；課綱兩輪 4/6/7→7/8/8.5 後收斂。

## 變體：雙盲歸納

兩個 subagent 互不知情、各讀不重疊樣本、獨立歸納，兩邊都出現的 pattern＝真訊號。共用同一套「平行盲測＋收斂」骨架，v1 未實作，見 [references/double-blind-mode.md](references/double-blind-mode.md)。
