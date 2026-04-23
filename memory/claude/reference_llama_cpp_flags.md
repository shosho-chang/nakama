---
name: llama.cpp server CLI 2026 breaking change + reasoning flag 參考
description: llama.cpp 新版 --flash-attn 要帶 on/off/auto、--reasoning 控 thinking、--reasoning-budget 控 token 上限
type: reference
tags: [llama-cpp, local-llm, cli-flags, reasoning, flash-attention]
---

## 踩過的坑

2026-04-23 跑 Qwen/Gemma A/B bench 當天遇到兩次 llama.cpp 升版 breaking change：

### 1. `--flash-attn` 從裸旗標改為帶值

**舊寫法**（~2025）：
```bat
F:\llama.cpp\llama-server.exe -m MODEL.gguf --flash-attn -ctk q8_0 -ctv q8_0
```

**現版會炸**：
```
error while handling argument "--flash-attn": error: unknown value for --flash-attn: '-ctk'
```

**新寫法**：
```bat
F:\llama.cpp\llama-server.exe -m MODEL.gguf --flash-attn on -ctk q8_0 -ctv q8_0
```

支援值：`on` / `off` / `auto`（預設）。

### 2. Reasoning 模型的 thinking 控制

**Qwen 3.5+ / GPT-OSS / DeepSeek R1** 等 reasoning 模型：chat template 預設會產生 `<think>...</think>` 段，token 爆多，對於「純摘要 / 純抽取」任務完全是負擔。

**關掉 thinking**：
```bat
F:\llama.cpp\llama-server.exe -m MODEL.gguf --jinja --reasoning off --reasoning-budget 0
```

啟動 log 會顯示：
```
srv init: chat template, thinking = 0
```

**flag 意義**：
- `--reasoning on|off|auto` — chat template 是否啟用 reasoning 分支（短：`-rea`）
- `--reasoning-budget N` — 允許幾個 thinking tokens（-1 無限 / 0 立即關 / 正數為上限）
- `--reasoning-format` — 回傳格式：`deepseek` 分 `message.reasoning_content` / `deepseek-legacy` 保留 `<think>` tag / `none`

**棄用警告**：舊 `--chat-template-kwargs '{"enable_thinking":false}'` 已 deprecated，改用 `--reasoning off`。

## Nakama 的 production script

- `scripts/start_llm_server.bat` — Gemma 歷史版（A/B bench 2026-04-23 後棄用為預設，留作 fallback）
- `scripts/start_qwen_server.bat` — **現 production 本地 LLM**（Qwen 3.6-35B-A3B + reasoning off + flash-attn on + partial offload -ngl 35）

## How to apply

- 未來再升 llama.cpp 前先跑 `llama-server.exe --help | grep -i "reason\|think\|attn"` 確認 flag 沒變
- 新增 reasoning model 進 pipeline 時，先確認是否 thinking 有開，Robin ingest 類純摘要任務一律 `--reasoning off`
