---
name: 修修的開發機硬體配置
description: 本機 GPU RTX 5070 Ti 16GB + 64GB RAM，用於 FunASR 推理和本地 LLM
type: user
created: 2026-04-15
updated: 2026-04-15
confidence: high
---

- **GPU**: NVIDIA RTX 5070 Ti — 16GB VRAM
- **RAM**: 64GB 系統記憶體
- **用途**: FunASR 語音辨識（20 min podcast GPU 10 秒）、本地 LLM 推理

**How to apply:** 評估本地模型時以 16GB VRAM 為上限，64GB RAM 可做 CPU offload。
