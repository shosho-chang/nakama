---
name: CI gate threshold 走「不退步」哲學，用 baseline round-down 不用 aspirational
description: Coverage / SLO / latency budget 等 CI gate 的 threshold 設定用 baseline round-down 至最近 5%/10%（regression prevention），不用 aspirational 數字，避免噪聲且打擊 incremental contribution
type: feedback
originSessionId: 3e68d271-320b-48b0-aaab-8d443f2e5ed5
---
CI gate threshold（coverage / SLO / latency budget / error rate）用 baseline round-down 至最近 5%/10% 設定，不用 aspirational 數字。Threshold dict 註解必附 `# baseline X.XX%`，要 raise 顯式開 PR + 跑 CI 確認新 baseline 才調。

**Why:** Gate 真實價值是抓 regression 不是逼 contributor 拉高。Phase 6 Slice 1 critical-path gate 採此哲學：baseline 96.77% → threshold 95%（+5% buffer），baseline 90.48% → threshold 90%。Aspirational threshold 噪聲多（小退步如 patch fix 改一行 logic 多 1 個未蓋 line 就 0.X% 退步觸發 CI 紅），打擊 incremental contribution；contributor 開始 game the gate（加無意義 test 拉 % 而非真補 critical path）。

**How to apply:**

1. 加新 critical-path / SLO / latency budget 時：實量 baseline → round-down 5%/10% buffer → 寫進 threshold dict
2. dict 每條附 `# baseline X.XX%（理由）` 註解，後續 reviewer 能看 buffer 多少
3. Raise threshold（不允許 lower）走顯式 PR：跑量測證新 baseline → PR description 附 before/after → CI green 證 gate 沒紅
4. 整體 % 不擋 CI，只在 critical-path 個別模組擋（plan A bar 用「critical-path ≥ 80%」承諾的兌現方法）
5. 同樣哲學適用 latency p95 budget / error rate alert threshold / SLO target — 不只 coverage
