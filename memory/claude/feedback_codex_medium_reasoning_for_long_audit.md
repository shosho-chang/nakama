---
name: Codex 跑長 audit 用 gpt-5 + medium reasoning effort，不要 default xhigh / gpt-5-codex
description: 2026-05-25 ADR-032 panel 第一次 dispatch 用 `codex exec -m gpt-5-codex`（預設 reasoning xhigh）→ 連線斷 5 次 ERROR Reconnecting...，0 output；換 `codex exec -c model_reasoning_effort=medium`（無 -m flag）→ 9.5 min 跑完 ~2000 字 6-section audit + code grounding 大破發現
type: feedback
---

`codex exec` 跑長 ADR audit 時，model + reasoning effort 組合會決定能不能跑完。

**踩過的兩種失敗模式**：

1. **`codex exec -m gpt-5-codex` (預設 reasoning xhigh)** — 連線中斷 5 次 reconnect 都失敗，0 output 出來。看起來是 codex-variant model 在長 inference 中 socket idle timeout
2. **`codex exec` (predefault model)** — 行為跟修改前一樣會卡住 / 失敗

**Works**：

```bash
codex exec --skip-git-repo-check -c model_reasoning_effort=medium - < prompt.md > audit-raw.txt 2>&1
```

- **不指定 `-m`**：用 codex CLI 預設模型（2026-05 是 gpt-5 / non-codex variant）
- **`-c model_reasoning_effort=medium`** 而非 high/xhigh：long audit 不需要 max reasoning，medium 足以做 code grounding + 6-section 結構化輸出 + push-back，重點是要跑完不斷線
- **`-`** stdin 接 prompt：避免 shell 過長 arg quoting 問題

實證：2026-05-25 ADR-032 audit 32KB prompt → medium reasoning → exit 0 + 4235 行 raw output（前 549 行是 prompt echo + 一輪失敗 xhigh log，第 549 行後是真正 audit 內容 ~169 行 markdown）

**How to apply**：

- panel review 跑 Codex audit 一律用 `medium` reasoning，不要試 high / xhigh
- 提取 audit 內容：`grep -n "^codex$" audit-raw.txt` 找最後一個 `codex` 行，從那行 +1 開始到 EOF（或到 `tokens used` marker）是 final audit 文字
- xhigh 連線斷的情況可能未來 codex CLI 修，但目前（v0.128）的安全選擇是 medium
- 如要 high reasoning 用 Claude 自己（Opus 4.7 + extended thinking）而非透過 codex CLI
