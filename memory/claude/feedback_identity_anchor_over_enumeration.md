---
name: LLM voice drift / 風格 leak 用 positive identity anchor + sentinel + fallback，不要窮舉表
description: 修 LLM agent 的「風格 leak / 用語不對」問題（簡中 vs 繁中、AI slop 用語、tone 飄移）不能列窮舉對照表（永遠補不完）；改用 positive identity anchor + 反向 sentinel keyword + 不確定 fallback 三層
type: feedback
created: 2026-05-03
---

修 LLM agent 的「風格 leak / 用語不對 / tone drift」問題時，**禁止用窮舉對照表方法**（例：簡中 vs 繁中譯名清單）。改用 **positive identity anchor + 反向 sentinel keyword + fallback rule** 三層。

**Why**：2026-05-03 修 Nami「柳葉刀 vs 刺胳針」leak 時，第一版 propose 列窮舉表（Lancet→刺胳針 / NEJM→新英格蘭醫學期刊 / JAMA→...）。修修明確 push back：「列表整個列出來是個解決方法嗎？大陸用語、中國用語跟臺灣用語，怎麼列應該都很難窮舉吧？有沒有什麼方法可以一勞永逸？」

修修對 — 簡中→繁中 mapping 不只醫學期刊：「軟件→軟體 / 視頻→影片 / 網絡→網路 / 質量→品質 / 信息→資訊 / 激活→啟用 / 項目→專案 / 智能→智慧 / 缺省→預設 / 博客→部落格 / 計算機→電腦」隨便幾十個。每次新詞 leak 修修又要回來補列表，永遠不會收斂。

**How to apply**（三層解法）：

### Layer 1 — Positive identity anchor（取代「不要 X」negative instruction）

不寫「不要用簡體中文」「不要用 AI 樣板用語」，改寫「你的輸出對象是 ___」+ 給具體 reference identity（媒體 / 作家 / 風格名）。LLM 對 positive frame + concrete identity 反應遠比 negative + abstract rule 好。

範例（Nami case）：
> 你的輸出對象是台灣讀者。調性類似《科學人》《Hello醫師》《商業周刊》的台灣專業作家——不是中國大陸的科普號、也不是日文翻譯腔

範例（其他 use case）：
- AI slop 用語 → 「你的輸出像給工程師讀的 RFC，不是 marketing landing page」
- 中二 anime tone → 「你的口吻像專業同事談話，不像粉絲自介」

### Layer 2 — 反向 sentinel keyword（10-15 個高頻 leak 詞）

不當對照表，當 trigger fast-fail 信號：「寫出 X/Y/Z 任何一個 = 立刻改」。挑頻率最高、最 distinctive 的就好，不求窮舉。

範例（Nami 簡中 leak case）：
> **leak 警示詞**（寫出任一個立刻改）：質量→品質 / 軟件→軟體 / 視頻→影片 / 網絡→網路 / 信息→資訊 / 激活→啟用 / 項目→專案 / 智能→智慧 / 缺省→預設 / 博客→部落格 / 柳葉刀→刺胳針

10 個以內保持 prompt 輕量，新詞補進去也容易。

### Layer 3 — Fallback rule（不確定時怎麼辦）

對沒列在 sentinel 裡的灰色詞，給 escape hatch：保留原文 / 雙寫並列 / 標註不確定。讓 LLM 不確定時不要硬猜。

範例（Nami case）：
> **不確定的譯名**（特別是醫學期刊、學術術語、新詞）：保留英文原文 + 中文翻譯首次出現兩個都寫，例：「Lancet（刺胳針）」「NEJM（新英格蘭醫學期刊）」

這樣 leak 時也是英文+中文並列，不會單獨秀「柳葉刀」。

## 三層為何加起來 robust

- **L1 identity anchor** 解決「LLM 不知道 target audience 是誰」根因 — 給 concrete reference 後 LLM 自動 align 到對應 vocabulary 生態系
- **L2 sentinel keyword** 抓 L1 沒擋住的最常見 leak — 像最後一道網
- **L3 fallback** 處理「LLM 真的不知道對應詞是什麼」邊緣 case — 不硬掰

新詞自動覆蓋（L1 catch 大部分 + L3 帶 escape hatch），不像窮舉表要 owner 每次回頭補。

## Cross-ref

- 應用案例 PR #329：[project_session_2026_05_03_evening_nami_polish.md](project_session_2026_05_03_evening_nami_polish.md)
- 同源最高指導原則：[feedback_minimize_manual_friction.md](feedback_minimize_manual_friction.md) — 修修 rename / 補列表 = 摩擦力，要消除
- 美學 first-class（也是 anti-AI-slop）：[feedback_aesthetic_first_class.md](feedback_aesthetic_first_class.md)
