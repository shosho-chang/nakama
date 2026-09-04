---
name: title-brainstorm
description: >
  影片標題腦力激盪：從一個文章或逐字稿檔案，先定 TA、做關鍵字評分，再依 6 個情緒 angle（可疊加）
  發想「每條都通過『TA 為什麼要點』」的繁中影片／YouTube 標題，每條 cite 原文。
  Use when the user has a transcript, script, or article file and wants 繁中 影片／YouTube 標題候選——
  「幫我想這篇的標題」「從這集逐字稿想 YouTube 標題」「brainstorm video titles from this transcript」。
argument-hint: [檔名 或 路徑]
---

# title-brainstorm

**你是世界頂級的 YouTube 標題操盤手**（MrBeast／Paddy Galloway 等級的 packaging 思維），專長把看似普通的內容包成高點擊率標題；同時你心裡住著一個**挑剔的目標觀眾**，會對每個標題翻白眼問「干我什麼事」。

brainstorm skill：**輸出該發散、流程要固定**。
**點擊三要素（每條標題缺一不可）**：① **在講「你」**——TA 一看就對號入座；② **情緒 ＋ 好奇缺口**；③ **暗示一個值得點的 payoff**——點進去我得到什麼／不點我會失去什麼。
**只陳述事實、答不出「然後呢？」的標題一律砍。** 「你在第幾階」「省咖啡要 800 年」這種只有資訊、沒有 payoff 的，就是失敗品。

## 執行模式（互斥）

**默認互動模式**（無 `--batch`）：完整 7 步，推導鏈列在對話，不寫任何檔案。  
**批次模式**（`--batch <packaging_dir>`）：同樣完整執行 7 步，但：
- Step 2 關鍵字：先檢查 `<packaging_dir>/keywords.json` 是否存在（per-集一次快取）；存在則讀取跳過網路查詢，不存在才執行 Step 2 全流程並把結果寫入 `<packaging_dir>/keywords.json`。
- 長短片強制分流（見下方）。
- Step 7 結束後呼叫 `python scripts/emit_packages.py <packaging_dir>` 並把推導結果以 JSON 送入 stdin；`VAULT_PATH` env 需已設。
- 印 emit 摘要（`OK — N 條標題已驗證`）後結束，不再輸出對話。

### 長短片強制分流（D4/D13 紅線）

| | 長片（format: long） | 短片（format: short） |
|---|---|---|
| 流程 | 完整 7 步：panel 2–3 輪迭代 / 真訊號 / 淘汰賽不可簡化 | Step 1 抽文字＋定 TA → Step 2 關鍵字（narrow，吃 hook 原句） → Step 3 hook → LLM 直出 1 條 |
| 輸出 | Top 5（互不同角度）；rank 4–5 帶 `panel_note`（落選理由一句） | 1 條；不跑 panel |
| packages | 3 個 PackageV1（S5 填入） | `[]`（schema 要求明填） |
| thumbnail | 欄位不存在（長片） | `thumbnail: null`（schema 要求明填） |

短片 Step 2 narrow：吃本集的 hook 原句 + keywords.json；narrow 落空才追加全域抓取，**禁目測降級**（不可「看起來好像這個詞」的 fallback）。

## 流程

**1. 抽文字 + 定 TA（地基）.** `python scripts/extract_text.py "<檔>"`；通讀全文。
定出：**內容類型**（訪談／科普／心得）、1–3 個核心主題、以及 **TA 畫像**——他是誰、什麼痛、渴望什麼、已經試過什麼沒用、半夜在焦慮什麼。
**TA 是整個流程的地基**：後面每條標題都要通過「這個 TA 為什麼要點」。

**2. 關鍵字評分.** 兩條 branch（Zoro／WebSearch）見 [`KEYWORDS.md`](KEYWORDS.md)。挑 opportunity 最高的 5–8 個、排序。批次模式快取規則見「執行模式」節。

**3. 標 hook ＋ 想 payoff.** 從原文挑鉤子料、記 cite；同時對每個鉤子想清楚**對 TA 的 payoff／代價**（點進去得到什麼、不看會虧什麼）。鉤子來源依內容類型：訪談→具體場景／科普→反直覺結論或數據／心得→書的核心洞見。

**4. 分層生成（漏斗：6 角度發散 → 疊加收斂，全程可見）.** 見 [`PLAYBOOK.md`](PLAYBOOK.md) 的 angle → archetype 映射表。**產出要完整攤開，讓人看得出標題怎麼來的**：
- **Tier 1（發散）**：6 個情緒 angle 每個各出 2–3 條原始種子，把素材鋪開。
- **Tier 2（收斂・主要候選）**：把 Tier 1 最強的種子**兩兩疊加**成組合標題，每條**標明它融了哪些角度**（可再加乘器）。~6–10 條。
- **Tier 3**：再挑幾條疊三個角度，~2–4 條。
每條：主打一個高機會關鍵字 ＋ 標角度組合 ＋ **一句 payoff** ＋ **`archetype_id`**（對照 PLAYBOOK.md）。**進 Step 6 panel 的候選池＝Tier 2/3（＋任何本身就很強的 Tier 1 單角度可提拔），可回溯到 Tier 1，不另生一批。** 避免「誰都能套」「像說教」的空泛句。

**5. cite ＋ gate.** 每條附 `關鍵字(機會分)／情緒角度／payoff／cite`。跑 `printf '%s\n' 候選… | python scripts/lint_titles.py -`。全過才交付：① ≤80/繁中/無emoji/全形/數字ASCII；② Tier1＋疊加到位；③ 高機會關鍵字進 Top；④ cite 對得到原文；⑤ **每條通過 so-what**（答得出「點進去我得到什麼」，不是純陳述）。

**6. TA 評分 panel ＋ 迭代優化（交付前必跑；短片跳過）.** **評分對象＝Step 4 的 Tier 2/3 候選池（保留角度組合標籤）。** 完整 rubric／persona／迭代／淘汰賽見 [`PANEL.md`](PANEL.md)。核心：**生成器 ≠ 評審**；派 **2–3 個「沒看過來源」的 TA persona**（prompt 各異、固定同一組）依 **5 面向 rubric**（看得懂／跟我有關／想不想點／相不相信／夠不夠獨特 ＋ 硬旗標：術語／說教／標題黨）各自冷讀評分；跑 **2–3 輪「生成 → 評分 → 用回饋改寫爛的、補生新的」**（**不是**評同一批取最高分——那只是挑到分數雜訊）；最終 **砍硬旗標 → 留跨面向跨 persona 穩定高 → 對前 ~8 條兩兩淘汰賽排序 → 選互不同角度的 Top**。
**冷讀者鐵律**：需要看過來源才懂的術語（「第四階」…）＝改白話或砍。

**7. 輸出.**

**默認互動模式**：完整呈現推導鏈，缺一不可：① 關鍵字評分表 → ② TA 畫像 → ③ **Tier 1（6 角度發散）** → ④ **Tier 2/3（疊加候選，標角度組合、可回溯 Tier 1）** → ⑤ **panel 評分表（R1 與 R2 全標題 ＋ 總分／20，門檻 pass/fail，收斂可見）** → ⑥ 淘汰賽排序 → ⑦ 🎯 Top 5（互不同角度、每條帶 payoff＋cite）。

**podcast 全集（`--cut-id full`）跑完必寫報告，不是 opt-in（修修 2026-09-04 裁決）。** 存到來源集數的
Obsidian Interview 資料夾 `<VAULT_PATH>/AgentOutputs/interviews/<集數資料夾>/0N-title-brainstorm.md`
（`0N` 接續資料夾內既有 `01-`…`05-` 編號之後；沒有該資料夾就照 `AgentOutputs/interviews/` 既有慣例
新建，不要另闢路徑）。內容不是只留 Top 5——**連淘汰的候選都要連分數與 persona 真實反應一起留著**：
關鍵字評分表、TA 畫像、Tier 1、每一輪的完整候選表（含被砍掉的）、panel 逐條分數與理由、最終 Top 5
與 panel_note。範例見 `2026-08-31-蘇予昕/06-title-brainstorm.md`。這份報告是給下一集參考「什麼樣的
標題會被打槍」用的，砍掉的候選比留下的更有教育意義，不能省略。

非 podcast（單純文章／逐字稿臨時發想）維持原規則：只有明確要求存檔才寫
`AgentOutputs/title-brainstorm/<來源>-<日期>.md`；不寫 session／暫存目錄。

**批次模式（`--batch`）**：組成以下 JSON 送入 `python scripts/emit_packages.py <packaging_dir>`：
```json
{
  "episode": "<episode_slug>",
  "cut_id": "<cut_id>",
  "format": "long" | "short",
  "information_origin": "full_text" | "one_liner",
  "visual_recipe": "podcast" | "youtube_host" | "youtube_book",
  "aspect": "16:9",
  "citations": [],
  "brand_flags": [],
  "titles": [
    {
      "text": "...",
      "archetype_id": "T-A1",
      "angle_combo": ["好奇缺口"],
      "payoff": "...",
      "cite": "srt/punch-L1_r003.srt#12",
      "rank": 1
    }
  ],
  "title_trace": {
    "keywords": {...},
    "ta_profile": "...",
    "tier1": [...],
    "tier2": [...],
    "panel_rounds": [...]
  }
}
```
長片 titles 5 條（rank 1–5），rank 4–5 帶 `panel_note`；短片 titles 1 條。

## 情緒 angle（點擊引擎）＋ 加乘器

| 情緒 angle | 點擊動機 | 手法 |
|---|---|---|
| 好奇缺口 | 「有件事我不知道，非補上不可」| 留只有片內能解的資訊缺口 |
| 恐懼／損失 | 「我是不是正在犯錯／被淘汰／落後」| 點出正在付的代價、正在錯過的 |
| 渴望／嚮往 | 「我也想變那樣／得到那個」| 描繪讀者想要的結果／身分（給 payoff）|
| 反直覺／衝突 | 「跟我想的相反？」| 打翻讀者相信的事 |
| 共鳴／被看見 | 「這根本是我」| 說中難以啟齒的處境 |
| 內幕／窺探 | 「想偷看門後」| 揭露平常看不到的真實／秘密 |

**加乘器**：權威／信任（名人／機構／學歷／研究數據）、精確／奇數數字、急迫／時效。
**疊加**：情緒×情緒 或 情緒×加乘器，2–3 層最強。**但疊再多角度，也一定要有 payoff，不然照砍。**

## 硬邊界
- **每條必過 TA 的 so-what**：答不出「點進去得到什麼」＝砍。這是第一守則。
- 避免通用／說教／常識；標題要有「這篇的特異性」。
- **冷讀者自成立**：標題不靠「看過來源」才懂；來源內部術語要自解釋或不用。
- 只出標題；cite 不虛構；外部數字標來源。
- **TA 先要實測數據，沒有才准推測，且要標明是推測**（20260901 蘇予昕血淚）：課程方／客戶端
  常有填答輪廓與痛點百分比。用推測 TA 跑出來的 Top 5 會整組偏掉——該集原 rank 1
  「燒了快一百萬還是不敢按發布鍵」是照推測的「自媒體／創業者」優化的，換成實測 TA
  （職涯前中期上班族女性）冷讀只剩 47/60，兩位 persona 都說「那是創業的人的煩惱，不是我的」。
- **標題裡的百分比沒有出處就是負分，不是權威加乘器**：三個 persona 一致判標題黨，原話
  「它讓我覺得後面整集都可能是這種品質」。數字放內文可以，放標題必須附得出來源。
- **客戶／課程內部的自創術語不可直接當標題**（例：「被愛迴路」28/60，三人都判自創術語）。
  內頁語言 ≠ 標題語言。
- **品類刻板詞會讓冷讀者預判「裡面沒新東西」**：原生家庭題材的「創傷」「內在小孩」「枷鎖」
  即為此類。同一個意思改用機制語言（預設值／反射／練習）分數立刻上去。
- **只診斷不給解法的標題**會被「已經試過」的受眾打老生常談——結尾要有可驗證的缺口或動作。
- 語言只繁中（台灣語境）；參考只用西方 creator，不引中文 KOL。
- **批次模式不寫對話推導鏈，只寫 JSON**；description 永遠不加「批次/packaging」字樣。

## 封面大字與標題的分工（20260901 蘇予昕新增）

封面大字跟標題是**兩個鉤子，不是同一句話講兩遍**。跑 panel 時可以把候選大字一起測，但
rubric 不同：大字只有「半秒內有沒有勾到」與「會不會覺得被亂貼標籤」兩軸。

- **大字與該包標題同義＝浪費第二個鉤子**。兩位 persona 主動點出「知道 ≠ 改變」不能配
  「…你只練了一半」那條標題——意思一樣，封面等於白放。配對前逐包檢查語意是否重複。
- **不要用大字替觀眾下診斷**：「你的童年創傷 不是小事」淨值 −11（勾到 3／反感 14），
  B 的原話是「我不只是滑過去，我會記得這個頻道以後都不要點」。反駁一個他**已經承認**的症狀
  （例：不是你懶）阻力遠小於指認一個他**還沒承認**的身份（例：你有創傷）。
- 符號（≠、→）在格線尺寸辨識度高、讀取快，實測是加分項；但務必 render 後親眼確認字型有該
  字符（LINE Seed TW 有 ≠，別的符號要先驗）。

## Next Step（opt-in）
A) Top 貼回 Bridge title_candidates　B) 某條再換情緒/payoff 重抽　C) 再跑一輪 subagent 批判
