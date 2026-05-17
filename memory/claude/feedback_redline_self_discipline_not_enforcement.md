---
name: redline-is-self-discipline-not-architecture-enforcement
description: 紅線（如「LLM 不可代寫修修聲音」）靠修修自律守，不靠架構強制；系統角色是 reminder 與 default path 設計，不是物理 enforcement
type: feedback
tags: [red-line, architecture, self-discipline, brook, adr-027]
created: 2026-05-17
originSessionId: adr-027-panel-grill
---

紅線（如 `CONTENT-PIPELINE.md:36` 的「Line 2 心得是修修自己的聲音，不可被 LLM 取代」）屬於修修的自律邊界，不屬於系統可強制的 invariant。

**Why:** ADR-027 panel session 2026-05-17，Gemini 對 Brook 2b 提出嚴格 enforcement 架構（claim-atomic schema 強制 / Interactive Scaffolder UI 禁止 LLM 寫 prose 進 canvas）。修修明確 reject：
> 「這個紅線也要靠我自己的自律，LLM 能做到的有限，它只要提醒我就好了。如果要做得那麼嚴格，我如果要去跨越紅線，不管怎麼樣我都可以做得到的。」

對應的設計帶來兩個原則：
1. 系統設計上讓「不跨線」的路徑摩擦比「跨線」低（default path / desire path 設計）
2. 在跨線疑慮處顯示 reminder 信號（無 citation 段標 ⚠️、inline 警示）
3. **不**用 hard fail / 物理隔離宣稱可防止有意繞線（無法）

**How to apply:** 設計 LLM-assisted workflow 時，遇到「為了防 owner 自己跨線而加嚴格 enforcement」的提議要打住 — 過度架構不可 enforce 的 self-discipline 是浪費 effort。改用 reminder + path-of-least-resistance 設計：
- 對的路徑要好走、context 完整（不要為了「純」砍掉 useful 的 context bridge）
- 錯的路徑要顯式繞道（不是物理擋，是讓修修選擇時看得到差異）
- LLM 跨線疑慮處標記給審稿時看（best-effort，不 hard fail）

對應 [[feedback_quality_over_speed_cost]] 的對偶：品質 > 速度 > 省錢，但**品質的 enforcement 點在修修審稿，不在 LLM 自我約束**。

對應 [[feedback_aesthetic_first_class]] 同樣 framing：美學 first-class 是設計選擇，不是 lint rule 能強制。
