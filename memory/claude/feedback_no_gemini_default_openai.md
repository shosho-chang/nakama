---
name: 停用 Gemini，預設走 OpenAI
description: 修修裁決全面停用 Gemini model，LLM 呼叫預設改走 OpenAI（經 OpenRouter transport）
type: feedback
---

修修裁決（2026-08-17）：**全面停用 Gemini**，所有 LLM 呼叫預設以 OpenAI 為主，除非 Google 之後推出新 model 才重新考慮。

**Why：** 緊接在 Anthropic API 額度用完的事故（Nami/Franky/memory-reflection cron 一起中招）後提出；當時盤點順便發現 VPS `MODEL_ROBIN=gemini-2.5-pro` 是唯一一處把 Gemini 當生產路由在用。修修選擇不要繼續多方分散（Anthropic 訂閱/api + Gemini + OpenAI 三頭燒），統一收斂到 OpenAI（手上有現成額度，經 OpenRouter BYOK 消化）。沒有明講是成本考量還是別的，但語境是額度管理對話的延伸。

**How to apply：**
- 新增任何 LLM 呼叫時，預設候選是 OpenAI（`gpt-5.6-terra` 等，經 `LLM_TRANSPORT_<AGENT>=openrouter`），不要提 Gemini 當選項
- 三類現存 Gemini 依賴，處置不同：
  1. **生產路由**（VPS `MODEL_ROBIN=gemini-2.5-pro`）— 直接關，換 OpenAI，無 blocker
  2. **音訊多模態**（`shared.llm.ask_with_audio` 只有 Google 一家實作，`shared/multimodal_arbiter.py`、Brook podcast 字幕仲裁/audio_audit 依賴它）— **結構性卡住**，facade 完全沒有 OpenAI 音訊路徑，要拔掉 Gemini 得先新開發（OpenRouter 上有 `openai/gpt-audio` 可接，但要重寫）。這是獨立的 P9 規劃工作，不是關 flag 能解
  3. **ADR panel review 腳本**（`multi-agent-panel` skill 的 `assets/gemini_dispatch_template.py`）— 也要換成 OpenAI 當第三方視角，屬於 skill 層級調整（skill 檔在 `~/.claude/skills/`，不在本 repo）
- 若 Google 出新 model 讓修修想重新評估，此裁決才鬆動——不要自己判斷「這個場景 Gemini 比較適合」就默默用回去
