---
name: subscription-first：付費 API 呼叫需明確 opt-in
description: 修修 2026-07-25 方針 — 互動 pipeline / skill 一律跑在 subscription quota（cowork + subagent）內；Anthropic API key / Gemini 等付費呼叫必須明確 opt-in flag，不做預設
type: feedback
created: 2026-07-25
---

**互動工作流（cowork / Claude Code 驅動的 skill）一律預設跑在 subscription quota 內；任何會打付費 API 的路徑（`shared/llm.py` → Anthropic API key、`shared/gemini_client.py` → Google key、其他計費 API）必須是明確 opt-in flag，且用之前提醒成本。**

**Why:** 2026-07-25 修修 review 字幕產線時發現 subtitle-correct 的 llm 模式打了 Opus API + Gemini 仲裁（測試兩輪 ≈ $5–10）後明確裁決：「想把所有的事情都在 subscription plan 的 quota 裡面做好，用 Cowork 來完成就好，不要動到會多花 API 錢的事情」，並指定「Cowork 呼叫 skill 時，讓它自己去派 Opus 或其他 SubAgent 來完成」LLM 工作。

**How to apply:**
- 設計新 skill / pipeline 時，LLM 工作預設走「機械 script 切工作 → cowork 派 subagent（model: opus）→ 機械 script 套用」的三段式，不在 Python 內呼叫 `shared/llm.ask`
- 付費路徑保留給無人值守批次等場景，一律藏在明確 flag 後（如 `--api` / `--arbitrate`），CLI 對誤用直接擋下而非默默扣款
- cowork 內做不到的能力（如 Gemini 聽音檔仲裁）→ 預設關閉、留 QC 給修修人工，不是默默改用付費 API
- 首例落地：`scripts/run_subtitle_correct.py` `--emit-chunks`/`--apply`（PR #1020）；[[feedback_sandcastle_default]] 的並行 dispatch 也同屬 subscription 資源
- 註：VPS 上的 production agent（Robin/Nami 等 cron/gateway）本來就靠 API key 跑，不在此方針範圍——這條管的是「修修互動中的 cowork / Claude Code 工作流」
