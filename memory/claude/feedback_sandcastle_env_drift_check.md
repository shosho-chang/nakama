---
name: Sandcastle .env API key drift — sync from main repo .env before run
description: ANTHROPIC_API_KEY rotated in nakama/.env 不會自動同步到 sandcastle-test/.sandcastle/.env；first run AgentError "Invalid API key"。每次 sandcastle dispatch 前對齊 / 每次 .env rotation 後 sync。
type: feedback
created: 2026-05-04
---

Sandcastle 跑前若 nakama/.env 在上次 sandcastle run 後 rotation 過 ANTHROPIC_API_KEY，sandcastle 容器讀 stale key → claude-code CLI exit 1 with "Invalid API key · Fix external API key"。

**Why**：sandcastle Docker container 從 `.sandcastle/.env` 拿 key（main.mts `docker({ env: { ANTHROPIC_API_KEY: process.env.ANTHROPIC_API_KEY!, ... } })`），這個檔案不會自動跟 `nakama/.env` sync。

**How to apply**：

每次 sandcastle dispatch 起手前（或在 first AgentError "Invalid API key" 時），sync key：

```bash
grep "^ANTHROPIC_API_KEY=" E:/nakama/.env > E:/sandcastle-test/.sandcastle/.env
echo "GH_TOKEN=$(gh auth token)" >> E:/sandcastle-test/.sandcastle/.env
```

mac 對應路徑改 `~/Documents/...`。

**Detection signature**：first sandcastle run output 含
```
AgentError: claude-code exited with code 1:
Invalid API key · Fix external API key
    at <anonymous> (...sandcastle/src/Orchestrator.ts:136:11)
```
就直接 sync .env retry，不 debug 容器內部。

**整合進 runbook**：可考慮 `docs/runbooks/sandcastle.md` 加一句「**每次 dispatch 前必先 sync .env，避免 stale key**」當 step 0。
