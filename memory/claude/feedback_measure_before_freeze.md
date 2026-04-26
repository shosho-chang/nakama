---
name: 凍結 task prompt 前先實量 baseline
description: Plan / proposal 寫的「目前 X%」「現況差 N」估計常常不準（甚至差到單一個 phase 的整個方向），凍結 task prompt 之前必須實際量出 baseline，揭露 plan vs 現狀差距
type: feedback
originSessionId: 3e68d271-320b-48b0-aaab-8d443f2e5ed5
---
凍結 task prompt 前必須實際量出 baseline / 現狀，不可直接照 plan / proposal 寫的估計數字當 input。揭露 plan vs 現狀的差距，寫進 decisions doc 跟 task prompt header。

**Why:** Phase 6 plan 寫「目前 coverage <60%」實際量出來 **81%**（差 20+ 百分點）。如果照 plan 凍結「補 8 模組到 80%」task prompt 直接動工，會做大量無謂的 test（7/8 已 90%+），甚至 misframe 整條 chunk 的目標（從「補洞」變成「鎖 baseline」）。同樣的差距在 alert_state（plan 寫 FSM 實際只是 dedupe table）跟 SSE 路徑（plan 寫不存在實際在 thousand_sunny/）。Plan 寫的時間越久前，估計越不準。

**How to apply:** 開新 chunk task prompt 凍結流程必含「實量現狀」步驟：

1. 任何「補某模組到 X%」「抽出某 module」「重構某 schema」chunk，動工前實際跑量測命令（`pytest --cov=...`、`grep -r`、跑 endpoint 看 response shape）
2. 把 baseline 數字寫進 decisions doc / task prompt §3 Inputs / PR description before-after
3. 揭露的 plan vs 現狀差距寫成「⚠️」段在 decisions doc 開頭，不藏在 PR body 末尾
4. 如果差距 > 20% 或核心假設崩盤（如 plan 寫的 module 不存在），停下來重新 frame chunk 範圍，不假裝照 plan 跑
