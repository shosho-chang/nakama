---
name: 用戶提「另一條路 / 替代 / 改走 X」要先讀 ADR，不靠 grep model name
description: ADR 替代路徑 lookup 規則：先 ls docs/decisions/ADR-*.md 找最新 ADR + alternative paths section，不靠 grep model/feature name 第一 match（舊同名 memory 可能 stale）
type: feedback
created: 2026-04-30
---

用戶說「另一條路 / 替代 / 改走 X / D2 路徑」是 ADR 用語 = 該 decision 的替代選項。lookup 必先：

1. `ls docs/decisions/ADR-*.md` 找相關 ADR（最新一份優先）
2. 讀該 ADR 的 "Alternatives considered" / "Decision" section 找替代路徑名單
3. 對照 `docs/research/YYYY-MM-DD-<topic>-prior-art.md` 看完整 candidate landscape
4. **最後才** grep memory 找補充細節

**Why**：2026-04-30 對話踩到 Qwen2.5-Omni vs Qwen3-ASR 混淆 — 用戶說「另一條路 = Qwen」，我抓 MEMORY.md 第一 match `project_local_multimodal_audio_models.md`（2026-04-17 寫，標 Qwen2.5-Omni 為「首推本地方案」），開始討論 Qwen2.5-Omni。實際 ADR-013（2026-04-30）的 D2 是 **Qwen3-ASR-1.7B**（2026-01 阿里官方，Mandarin WER 2x 領先 Whisper V3），記在 `docs/research/2026-04-30-asr-engine-prior-art.md` 但**沒進 MEMORY.md 索引**。

三層根因：
1. **索引斷層**：新 research doc 沒進 MEMORY.md
2. **舊 memory 沒退場**：兩週前的 entry 框架 stale 沒人更新
3. **行為 gap**：grep 第一 match 而非走 ADR 結構化 lookup

**How to apply**：
- 任何用戶 hint「替代 / 另一條路 / 改走 / 不用 X 用 Y」→ 第一動作 `ls docs/decisions/`
- 同 model/feature name 在不同時期可能指不同東西（Qwen2.5 vs Qwen3、Sonnet 4.5 vs 4.6）— 別假設 grep 命中等於正確 entry
- 此規則同 [feedback_design_rationale_trace.md](feedback_design_rationale_trace.md) 一脈相承（憑直覺不 trace 真實 pipeline）

附帶教訓：寫 ADR / research doc 後**必須** index 進 MEMORY.md，否則 stale memory 會在下次對話覆蓋新事實。
