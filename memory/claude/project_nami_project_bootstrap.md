---
name: Nami Project Bootstrap — branch 已推、待 VPS 部署測試
description: feat/nami-project-bootstrap 進度、VPS 部署待辦、Slack manifest 更新項目
type: project
tags: [nami, skill, project-bootstrap, pending-deploy]
created: 2026-04-19
updated: 2026-04-19
confidence: high
ttl: 30d
---

## 狀態：已 commit + push，等 VPS 部署測試

**Branch**: `feat/nami-project-bootstrap`（已推到 origin，commit `d2cde47`，未 merge）
**PR URL**: https://github.com/shosho-chang/nakama/pull/new/feat/nami-project-bootstrap

## 已完成

4 個 unit + Capability card + backlog：
- `shared/lifeos_writer.py` + `shared/lifeos_templates/*.md.tpl`（youtube/blog/research/podcast）— 純渲染 + 寫入
- `scripts/run_project_bootstrap.py`（CLI wrapper，含 `--vault` 測試覆寫）
- `.claude/skills/project-bootstrap/`（SKILL.md + 3 references + evals/test-cases.md + CAPABILITY.md）
- `gateway/conversation_state.py`（thread-scoped ConversationStore，30 分鐘 TTL）
- `gateway/handlers/base.py`（`Continuation` 契約 + `continue_flow()` 預設 raise）
- `gateway/bot.py`（`message` event 路由到 `handler.continue_flow`）
- `gateway/handlers/nami.py`（`create_project` intent + 兩態 state machine）
- `gateway/router.py`（15 個新 keyword → `create_project`）
- `prompts/nami/parse_project.md`（Haiku 解析 prompt）
- 340 tests pass（+43 新），ruff clean

## 下次要做的事（依序）

**1. VPS 部署**（修修重開機後做）
```bash
ssh <VPS>; cd /home/nakama
git fetch origin && git checkout feat/nami-project-bootstrap && git pull
# 無新依賴
```

**2. Slack App manifest 更新**（最容易漏）— https://api.slack.com/apps
- Event Subscriptions → Subscribe to bot events 加：`message.channels`、`message.im`
- OAuth & Permissions → Bot Token Scopes 加：`channels:history`、`im:history`、`im:read`
- Reinstall App

**3. 手動測試**（建議先 DM，不急著做 systemd）
```bash
cd /home/nakama
set -a && source .env && set +a
python -m gateway
```
Slack DM Nami：「幫我建立一個關於超加工食品的 project」→ 照 thread 回 research → 確認 → 檢查 `/home/Shosho LifeOS/Projects/超加工食品.md` 存在

**4. systemd service**（測通後）：`/etc/systemd/system/nakama-gateway.service`，user=root、WorkingDirectory=/home/nakama、ExecStart=`/usr/bin/python3 -m gateway`、EnvironmentFile=/home/nakama/.env

**5. PR → code-review → squash merge**（照 `feedback_pr_review_merge_flow.md` 流程）

## 未做但 plan 提過（獨立工作，可另開 branch）

- LifeOS `Templates/tpl-project.md` + `tpl-action.md` 同步更新（已跟 gold standard 脫節，見 [project_lifeos_template_drift.md](project_lifeos_template_drift.md)）
