---
name: Qwen 3.6 已下載 + A/B bench 工具就緒
description: Qwen3.6-35B-A3B Q4_K_M GGUF 已下載，A/B bench（vs Gemma 4 26B）已上 main，等週四 2026-04-23 測試
type: project
---

## 現況（2026-04-20）

- **模型檔**：`F:\llama.cpp\models\Qwen3.6-35B-A3B-UD-Q4_K_M.gguf`（22.1GB）
- **Gemma 現況**：`F:\llama.cpp\models\gemma-4-26B-A4B-it-Q4_K_M.gguf`（16.8GB），仍為 Robin Map step 使用中（`config.yaml` 的 `local_llm.model`）
- **啟動腳本**：`scripts/start_qwen_server.bat`（partial offload：-ngl 35/40 層）
- **Bench 工具**：`scripts/ab_ingest_bench.py` run/report 子指令
- **PR #47**：已 merged（2026-04-20）

## Why

Qwen 3.6 的研究顯示：中文訓練重點、document benchmarks 領先 Gemma 4 20+ 分、Apache 2.0、1M context（vs Gemma 256K）。VRAM 16GB 限制下 Qwen 要 partial offload，Gemma 全進 VRAM，所以速度 Gemma 略勝、品質需實測。

## How to apply

- 週四測試完後看 `data/ab_bench/<slug>/REPORT.md`
- 若 Qwen 勝：開 PR 改 `config.yaml` `local_llm.model` → `qwen3.6-35b-a3b`、改 `scripts/start_llm_server.bat` 指向 Qwen GGUF
- 若差不多：留 Gemma 省 VRAM 壓力
- 若中文明顯更流暢但速度輸：考慮混合策略（某些步驟 Qwen、某些 Gemma）
- Task 檔：`F:/Shosho LifeOS/TaskNotes/Tasks/Robin ingest A B 測試 - Qwen vs Gemma.md`（scheduled 2026-04-23T09:00-10:00）
