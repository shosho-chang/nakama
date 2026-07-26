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

## 修修換段時

改 `winners.json`（換 id/rank）→ 重跑 `--materialize`（冪等，30 秒）。

## v2 備忘（不做，見 plan 文件）

cold-open 重排、詞級去句間停頓、直式字幕模板、訪談留言補掃校 persona。
