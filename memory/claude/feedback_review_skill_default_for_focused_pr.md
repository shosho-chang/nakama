---
name: focused PR 預設用 local Review skill — 不要問、不要 ultrareview
description: 我自己開的 PR 若 scope 收斂（small net diff / 單一 domain / tests 已綠 / behavior delta 文件化），直接 /review，不要每次問用哪個
type: feedback
originSessionId: a16bf522-add0-466b-abcf-6cce2af9d857
---
我自己（claude）開的 PR 若符合以下 shape，**直接** 跑 local Review skill（`/review` 或 `/code-review`），不要問用 `/ultrareview` 還是 local，不要等修修選。

**Shape**：
- Net diff 收斂（~100 LOC 以內 — 大重構不算）
- 單一 domain / 模組（compliance / single skill / single agent）
- All tests already green when PR opened
- Behavior delta 在 PR body 明寫
- 無 cross-system architectural change

**Why**：修修不想為了「要不要 review、用哪種」做決策。Local review 對 focused PR 已足；ultrareview 多視角價值在跨系統 / 大 PR / security-sensitive 才付得回成本。每次問是 cognitive 浪費。

**How to apply**：
- 我開完 PR、貼出連結後，緊接著就 invoke `/review` skill（不用宣告「我等你 review」之類的話）
- Review 結果回來後直接在 thread report 給修修，他可以據此決定 merge / fix / discuss
- **何時例外**（升級到 ultrareview 或先問）：cross-service 改動、security-sensitive path、>1000 LOC、migration / data-shape 變動、ADR 級設計改動
- 修修自己開的 PR 不適用 — 那是修修決定 review tooling，我不主動代為決策
