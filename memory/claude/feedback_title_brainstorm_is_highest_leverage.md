---
name: 長片標題是最高槓桿環節 — 不得為批次化而簡化；短片標題 LLM 直出
description: 修修 2026-07-26 優先序裁決（同日二修）— 長片 title brainstorm 對影片成效影響最大，批次化不得砍深度；短片標題重要度可忽略，LLM 直出不跑 panel
type: feedback
created: 2026-07-26
updated: 2026-07-26
---

**長片的 `title-brainstorm` 是整條 packaging 流程的重心，任何批次化簡化都不准動它的生成與評審深度。短片標題相反 —— LLM 直出即可，不跑 panel、不跑淘汰賽。**

## Why

修修 2026-07-26 在 packaging grill（ADR-054）先裁「title brainstorm 功能要最完整，因為它影響
影片成效的程度最大」；同日稍晚在 persona-pass 成本討論時**主動限縮範圍**：

> 「我說 title 的槓桿高是**對於長影片而言**，對於短影片，title 的重要程度可以被忽略。
> 短影片的 title 直接用 LLM 決定就好了，我其實不太在意。」

支撐這個分流的平台事實：YouTube Test & Compare **不支援 Shorts**（官方明文），所以短片標題
沒有客觀 A/B 裁判；Shorts feed 的點擊由前 3 秒內容決定，標題不是 CTR 變數。
長片則相反 —— 標題是 CTR 主變數，且 combined package A/B 判 watch time per impression。

## How to apply

- **長片（不准砍的三項）**：panel 的 2–3 輪「生成 → 評分 → 改寫」迭代（不是評同一批取最高分）、
  關鍵字層的真實訊號（不得退回 `KEYWORDS.md` Branch B 目測估分）、前 ~8 條的兩兩淘汰賽排序
- **長片（可以砍的）**：對話輸出的完整推導鏈（批次時只印摘要，推導鏈寫進 JSON 讓 UI 顯示）
- **短片**：標題／caption 由 LLM 直出（單次生成，吃該段 hook 原句 + 關鍵字表即可），
  不派 persona panel、不跑迭代。在 gate 上可改，但不值得為它加深度
- 之後若流程在時間或 quota 上撐不住，砍的順序：封面側 → 影片支數 → **永遠不砍長片標題深度**
- ⚠️ 連動：`highlight-cut/SKILL.md` Step 4 的舊 mandate「**每個當選段落**必經 title-brainstorm、
  跳過＝違規」是同日上午的裁決，已被本條**部分推翻**（僅長片必經）— 兩份文件要同步改，
  見 ADR-054 v3

相關記憶：[[feedback_subscription_first_no_api_spend]]（LLM 工作走 subagent，所以「完整」不等於「貴」）
/ [[feedback_aesthetic_first_class]] / [[project_content_pipeline_arch]]（Stage 5 多 channel 製作）
