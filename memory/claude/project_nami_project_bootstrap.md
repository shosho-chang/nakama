---
name: Nami Agent Loop — 主線狀態
description: LLM tool-use agent loop + 5 tools，全部 merge 至 main，VPS 待 git pull
type: project
tags: [nami, agent-loop, tool-use, vps-deployed]
created: 2026-04-19
updated: 2026-04-19
originSessionId: 6dc774b2-8ee7-4655-b691-eebe19832245
---
## 狀態：main branch（commit: 5b36437），VPS 待 pull

## Nami 工具清單（gateway/handlers/nami.py）
- `create_project` — 建 project + 3 個預設 task
- `create_task` — 建獨立或 project-linked task（支援 scheduled datetime）
- `update_task` — 修改現有 task（scheduled / priority / status / pomodoros）；by-title 模糊搜尋
- `delete_task` — 刪除 task（刪前必須 ask_user 確認）
- `delete_project` — 刪除 project（預設連同 linked tasks 一起刪）
- `list_tasks` — 列所有 to-do / in-progress task
- `ask_user` — pause loop，等使用者回覆後繼續

## 記憶系統（Phase 1-3 已部署）
- `shared/agent_memory.py` — user_memories table，(agent, user_id, subject) upsert
- `shared/memory_extractor.py` — Haiku 4.5 背景抽取，prompt 注入既有記憶確保 merge 不覆蓋
- Nami handle() 在 user message 開頭注入「## 你記得關於使用者的事」block
- VPS 實測通過：subject 去重 + content merge 無資訊遺失

## 重要設計決策
- 日期表注入 user message（非 system），確保 system prompt 可 cache
- end_turn 保留 Continuation（pending_tool_use_id=None）讓 thread 持續存活
- DM 不傳 thread_ts 給 say()，避免 thread UI；channel @mention 同樣直接回主對話
- yaml.safe_load 把 date 字串解析成 date 物件，寫回前需 _stringify_fm_dates()

## VPS 部署
```bash
cd /home/nakama && git pull && systemctl restart nakama-gateway
```

## 下一步
- ⬜ VPS git pull 更新（含 update_task + DM/channel reply 修復）
- ⬜ morning-brief 功能（Nami 主動推送）

## 已知限制
- ConversationStore 是 in-memory，重啟後狀態清掉
- _find_task_by_title 是線性掃描，task 量大時會慢
