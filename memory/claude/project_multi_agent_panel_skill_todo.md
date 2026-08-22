---
name: Multi-agent panel skill — 已改為 subagent 版 (2026-08-22 rewrite)
description: user-level `multi-agent-panel` skill at C:/Users/Shosho/.claude/skills/；2026-08-22 修修裁決退場 Codex/Gemini 外部 CLI（燒外部 credit + plugin 過期），改為 subscription quota 下的 parallel subagent（Fable/Opus/Sonnet 各一 lens）
type: project
created: 2026-05-06
updated: 2026-08-22
status: frozen (v2)
---

## 現況：v2 = subagent panel（2026-08-22 修修裁決）

**修修原話**：「Panel Review 的 plugin 已經過期了…我現在不要再花 Gemini 跟 CodeX 的 credit，
你直接派三個 sub-agent 去給他們不同的任務…可以派一個 Fable、一個 Opus、一個 Sonnet，
讓他們依照他們的能力，給他們不同的任務去 review。」

Skill 位置：`C:/Users/Shosho/.claude/skills/multi-agent-panel/SKILL.md`（已改寫）

**v2 panel 組成**——依任務難度而非年資配 model：

| Model | 適合 | 典型 lens |
|---|---|---|
| `fable` | 最難、最開放式的推理；長期失效模式；抓出「題目本身問錯了」 | 對抗性架構批判 |
| `opus` | 嚴謹的事實查核與窮盡交叉比對 | 事實查核＋可實作性驗證 |
| `sonnet` | 務實、範圍明確的分析 | 維運現實（負擔／腐爛／複雜度預算） |

**去偏誤的誠實聲明**（已寫進 skill）：同家族 subagent 的去偏效果**弱於**跨廠商。
仍然有效的機制依序是：①**context 獨立**（subagent 看不到 drafter 的推理過程，冷讀 artifact
——所以**絕不能用 `subagent_type: "fork"`**，fork 會繼承 context 連帶繼承偏誤）；
②**lens 專門化**（panel 實際價值大半來自這裡）；③能力分層；④對抗性 framing。
遇到「共同盲點的代價是災難性且不可逆」的決策（存放使用者資產的資料模型、安全邊界、
有法律效力的契約），要明講並讓修修決定要不要花外部 credit。

## 退場的東西

- `assets/gemini_dispatch_template.py` — 隨外部流程退役
- `references/dispatch-prompts.md` — 為 Codex/Gemini 寫的；6-section 結構與 push-back
  invariants 仍可轉用，dispatch 機制已失效
- Codex 走 ChatGPT subscription auth、Gemini 走 API key 的偵測與 graceful degradation matrix

## 跟既有 memory 的關係

- [feedback_multi_agent_review_three_lens.md](feedback_multi_agent_review_three_lens.md) —
  **重要前例**：PR review 早就在用「3 個 parallel general-purpose agent 分不同 lens」
  （PR #320 實證 2 blocker + 6 major）。v2 panel 等於把這個已驗證的模式擴用到策略決策，
  並加上 model 分層。該筆的 lens 分工紀律（**明列 skip list 避免 reviewer 重疊**）直接適用
- [feedback_panel_triangulated_judgment.md](feedback_panel_triangulated_judgment.md) —
  panel 完由 Claude 直接判、不 defer 回 user
- [project_multi_model_panel_methodology.md](project_multi_model_panel_methodology.md) — 舊三家方法論（歷史）

## 歷史（v1，2026-05-06 ADR-020 實證）

v1 是 Claude draft → Codex (GPT-5) push-back audit → Gemini 不同推理鏈 audit → 整合矩陣。
Codex 當時抓到 Claude 自審漏掉的 3 件 contract drift，Gemini 抓到多語言面向——
**這證明的是「獨立視角有效」，不是「必須跨廠商」**，v2 用 context 獨立 + lens 分工承接同樣效果。
