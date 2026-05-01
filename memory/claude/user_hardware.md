---
name: 修修的開發機硬體配置
description: 桌機 GPU RTX 5070 Ti 16GB + 64GB RAM；MacBook Pro 副機（出門 AFK 用，無 CUDA）
type: user
created: 2026-04-15
updated: 2026-05-01
confidence: high
---

**桌機（家裡，主要開發機）**：
- **GPU**: NVIDIA RTX 5070 Ti — 16GB VRAM
- **RAM**: 64GB 系統記憶體
- **用途**: WhisperX + pyannote diarization、FunASR 語音辨識（20 min podcast GPU 10 秒）、本地 LLM 推理（Qwen3 等）

**MacBook Pro（副機，出門用）**：
- **無 CUDA / 無 NVIDIA GPU**；whisperx + pyannote.audio 通常未裝
- **能跑**：所有 API-bound 工作（Anthropic / Gemini / OpenAI）、pytest、bridge dev server、Bridge UI 驗收、文件 / 記憶 / planning 工作
- **不能跑**：transcribe + diarization、本地 LLM 推理（Qwen3）、任何吃 CUDA 的 pipeline

**How to apply:**
- 評估本地模型時以 16GB VRAM 為上限，64GB RAM 可做 CPU offload
- 修修在外時推薦工作流程要先檢查是否吃 GPU；如吃 → 切到 API-only 路徑或排到他回家做
- Brook Line 1 driving example：Phase A (transcribe + diarization) 必等回家；Phase B-F (API + UI + 品質 review) MBP 全跑得動。一條 pipeline 拆 GPU vs API-only 兩段是 standard pattern
