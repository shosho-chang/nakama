---
name: feedback-iterative-playbook-refinement
description: 修修 偏好 run 過實際輸出再修 playbook / 系統，不接受 preemptive theoretical rewrite — 「我建議等我 run 過幾次之後，再慢慢修正 playbook」
metadata:
  type: feedback
---

修修 對 thumbnail playbook v1.1 panel audit 後續處理態度：**先 run 過幾次看實際輸出，再依觀察慢慢修正成穩定版本**，不在沒看過產出前做 preemptive 大改。

**Why**: 2026-05-27 panel audit 後 Gemini / Codex 提了多項建議（Anti-Playbook 負樣本、Portfolio Strategy 配比、Feedback Loop 動態 grade、TW-HK creator baseline 等）。修修原話「目前還沒有看到實際做出來的成品，所以我建議等我 run 過幾次之後，再慢慢修正 playbook」。理論 audit 推導的 fix 沒實際輸出 calibrate，可能改錯方向或過度工程化。

**How to apply**:
- 不要在系統「還沒被實際使用過」前提下，把 audit 推來的所有建議都 preemptive 落地
- v1.1 fix 套了 **mechanical + verifiable 部分**（如 §1.X anchor citation 錯位、frequency 數字漂移、causal-language softening）— 這些是 bug fix 不是 theoretical rewrite，照修
- v2 backlog 條目（portfolio strategy / anti-playbook / feedback loop / threshold tighten）**留著但不動**，等 修修 用幾輪後決定哪些真的痛
- 類似情境（playbook / agent / prompt 系統）也適用：先 ship 可用版本 → 修修 用過 → 累積具體 pain point → 才做下一輪 refinement
- 不適用：實際 bug、錯誤的 fact / 編號 / 引用、安全 / 法規問題（如 Taiwan Health Food Control Act 監管警告） — 這些屬於必要 fix，不需要等實際輸出

**Related**: [[feedback-no-chinese-creator-references]] [[feedback-hitl-gate-serves-subjective-taste]]
