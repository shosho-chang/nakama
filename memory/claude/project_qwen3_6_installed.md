---
name: Qwen 3.6 A/B bench 勝出，切為 Robin Map step 預設本地 LLM
description: 2026-04-23 A/B bench 完成；Qwen 100% 可靠度 vs Gemma 95.7%，雖慢 6× 但 KB 給 Chopper 用品質 > 速度，config.yaml 已切
type: project
tags: [robin, local-llm, ingest, ab-bench, qwen, gemma]
---

## 決策（2026-04-23）

**Qwen 3.6-35B-A3B** 取代 Gemma 4 26B 成為 Robin ingest Map step 本地 LLM 預設。

| 樣本 | Gemma | Qwen | Qwen 時間倍數 |
|------|-------|------|---------------|
| Book（327K chars，21 chunks） | 20/21 (95.2%) / 13.7min | **21/21** / 74min | 5.4× |
| Paper（193K chars，14 chunks） | 13/14 (92.9%) / 8.9min | **14/14** / 60min | 6.7× |
| Article（203K chars，12 chunks） | **12/12** / 8min | **12/12** / 55min | 6.9× |
| 總計 | 45/47 (95.7%) / 30.6min | **47/47 (100%)** / 189min | 6.2× |

Gemma 2 次失敗都是 llama-server 層 `<|channel|>thought` parse error（server bug，非 Gemma 內容品質問題）。排除 server 因素 Gemma 等效 100%。

## Why Qwen 勝

- **可靠性**：47/47 全過，Gemma 95.7%（即使 server bug 也算）
- **品質維度**：中文流暢度、結構完整、專有名詞處理（修修讀 Book REPORT.md 前 5 chunks 確認 Qwen 優勢足以蓋過速度劣勢）
- **主用途 Chopper RAG**：KB summary 品質 > ingest 速度（KB 是長期資產）

## How to apply

- `config.yaml` `local_llm.model` = `qwen3.6-35b-a3b`，`timeout` = `900`（Qwen partial offload 單 chunk 可達 4-5 分鐘）
- 預設啟動：`scripts/start_qwen_server.bat`（**不再**用 `start_llm_server.bat` 的 Gemma 版本）
- `shared/local_llm.py` `DEFAULT_TIMEOUT` = 900（從 300 提升）
- VPS 目前不跑本地 LLM（Robin ingest 仍在本機做），此切換不影響 VPS 部署

## Bench 原始資料

- `data/ab_bench/Quiet-Your-Mind-and-Get-to-Slee---Colleen-Carney/{REPORT.md, gemma.json, qwen.json}`
- `data/ab_bench/Ultra-processed-foods-and-human-health-the-main-thesis-and-the-evidence/{REPORT.md, gemma.json, qwen.json}`
- `data/ab_bench/Low-back-pain/{REPORT.md, gemma.json, qwen.json}`

（`data/` 被 gitignore，留本機）

## 踩坑紀錄（2026-04-23 bench 過程）

- **llama.cpp breaking change**：`--flash-attn` 裸旗標改為 `--flash-attn on|off|auto`；`--reasoning on|off|auto` + `--reasoning-budget N` 新增，用來關 Qwen thinking mode（server bug：thinking on 時每 chunk > 15 分鐘）
- **timeout 300s 不夠**：Qwen chunk 1 首輪冷啟動 269s，第二次 302s 就 timeout，bump config.yaml + DEFAULT_TIMEOUT 到 900s
- **`_get_config()` 有 cache**：改 config.yaml 後要重啟 process 才生效，不能熱 reload
- **model name 僅 log 用**：`ask_local(model=...)` 這個 model 是 OpenAI API 相容的 hint，llama-server 實際用載入的 GGUF 模型，hint 不影響結果但影響 log 顯示
