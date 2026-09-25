---
name: VPS 的 LLM 路由設定不只在 .env
description: NAMI_USE_AGENT_SDK 在 systemd unit、Robin 的 model 在 model_overrides.json；只看 .env 會誤判 Nami 沒走 SDK
type: reference
---

VPS 上決定「LLM 走哪條路」的設定分散在三處，**只 grep `/home/nakama/.env` 會誤判**（2026-09-25 ADR-070 S0 盤點時差點以為 Nami 沒走 Agent SDK）：

1. **`/home/nakama/.env`**：`AUTH_*`、`LLM_TRANSPORT_*`、`ROBIN_MERGE_USE_AGENT_SDK`、`NAKAMA_CLAUDE_CLI`、各家 key / OAuth token
2. **systemd unit `nakama-gateway`**：`Environment=NAMI_USE_AGENT_SDK=1`（不在 `.env`）。查：`systemctl cat nakama-gateway`
3. **`/home/nakama/data/model_overrides.json`**：Bridge `/bridge/models` 寫的 override，優先於 env 與 registry（2026-09-25 內容：robin 7 個 task 全釘 `claude-sonnet-4-6`）

只看 key 名稱、不印值的盤點指令：

```bash
ssh nakama-vps "grep -oE '^(AUTH_|MODEL_|LLM_TRANSPORT|CLAUDE_CODE|ANTHROPIC|OPENROUTER|NAMI_|ROBIN_MERGE)[A-Z_]*' /home/nakama/.env | sort; systemctl cat nakama-gateway | grep -E 'Environment'; cat /home/nakama/data/model_overrides.json"
```

`AUTH_*` / `LLM_TRANSPORT_*` 的值是政策字串不是密鑰，可以印。

**How to apply**：判斷某 agent 在 VPS 實際走哪條 LLM 路徑前，三處都要看；ADR-070 全面遷移完成（S6）後 `AUTH_*` / `LLM_TRANSPORT*` / `NAMI_USE_AGENT_SDK` 會退場，屆時更新本條。

相關：[[reference_agent_sdk_supports_oauth]]
