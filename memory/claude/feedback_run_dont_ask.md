---
name: 修修是 CEO+PM 角色：能跑就直接跑，不問
description: 修修預設要 agent 直接執行任務不要先問「要不要代跑」；他要結果不要過程；review 用最方便格式
type: feedback
originSessionId: 70b01225-94d6-4548-a285-53270c088e26
---
修修把自己定位為 **CEO+PM**：他要親自執行專案然後看成果，不想被 agent 把步驟「教學」給他做。

**Why**：2026-04-27 SEO Phase 1.5 production benchmark 我寫完 [docs/plans/2026-04-27-seo-phase15-acceptance-checklist.md](../../docs/plans/2026-04-27-seo-phase15-acceptance-checklist.md) 後給他三選項（A 我代跑 / B 他自跑 / C skill 互動）問他要哪個，他直接說「能幫我跑以後就直接跑，給我看結果。不用問。中間需要我 review 用最方便方式」。三選項問題本身對他就是多餘 friction。

**How to apply**：
- 任務只要 agent 能代跑（命令列、Python、API 調用、smoke test、benchmark、產 doc），**不要先問「要不要幫你跑」，直接開始跑**。
- 結果整理用 **review-friendly 格式**：markdown 表格 / 瀏覽器連結 / 一行 summary / 寫進檔案 → 用 file path 引用，不是 raw stdout 倒給他自己解讀。
- 真正需要他**親自 review** 的點才停下：要他開瀏覽器看視覺/體感、授權真實 API key、拿錢儲值、對外 publish、改設計選型。停下時明確列「請你看這幾個點」+ 連結 + 預期的 yes/no 判斷。
- 命令列 / Python REPL / 寫 code / 跑 benchmark / 量 wall clock —— 全部 agent 端做，不要叫他自己打。
- 這條覆寫 [feedback_no_premature_execution.md](feedback_no_premature_execution.md) 在「執行型 / 量化 / 純自動」任務的 default：那條規矩是針對「設計 / 架構 / 跨檔重構」這種 ambiguous scope 才停下確認；純執行任務直接跑。
