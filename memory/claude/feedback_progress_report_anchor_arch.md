---
name: progress_report_anchor_arch
description: 每次報告進度先列七層架構表（Lines × Stages + Agents × Stages），讓進度對齊到結構性 anchor 而非散裝 todo
type: feedback
---

報告進度（「現在進度 / 收工 / 下一步要做什麼」類問句）一律先列出兩張架構矩陣表的當前狀態，再講軸線細節跟議題。

**Why**：修修要看的是「哪個 stage / 哪條 line / 哪個 agent 卡住」這種結構性訊號，散裝列軸線會 mask 真實 bottleneck。對齊到 [CONTENT-PIPELINE.md](../../CONTENT-PIPELINE.md) 的兩張矩陣（Lines × Stages 主矩陣 + Stage 5 sub-matrix + Agents × Stages 矩陣），才能跟 4 個結構性觀察 + 3 條優先序對得起來。pipeline-anchored planning 的一致性延伸（[feedback_pipeline_anchored_planning.md](feedback_pipeline_anchored_planning.md)）。

**How to apply**：
1. 報告進度開頭直接 paste / refresh 當前矩陣狀態（圖例 ✅ 🚧 ⬜ ❌ n/a），不省略
2. 矩陣的 cell 值要對齊最新 PR / issue 狀態（不是抄 5/4 凍結版）
3. 矩陣下面才是軸線細節 + 開放議題 + 下一步候選
4. 候選下一步必標註所屬 stage（對照矩陣）+ 對應的結構性觀察 # 或優先序 #
5. 不適用：純技術問題（「這個 bug 怎麼修」）/ 單一 PR review / 對話內 follow-up clarification
