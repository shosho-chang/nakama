---
name: Inter-agent delegation Option A — same-process tool-based pattern
description: Nami → Zoro 等 agent 間 delegation 的 Option A（同 process import + tool-based + sync），對 sync 對話模式 + 短 wall time (<30s) 是最簡 MVP；Option B (async via shared state) 升級觸發點明列
type: reference
created: 2026-05-03
---

Nakama 多 agent 之間做 delegation（A agent 接到不屬於範圍的 request，forward 給 B agent，回傳結果由 A 用自己 voice paraphrase），**Option A 是首選 MVP**：same-process import + tool-based + sync。Zoro/Brook/Robin 之間任何 sync 對話場景都套這套。

## Pattern

A agent 加一個 tool `ask_<agent>(query, capability)`：

```python
# gateway/handlers/<a>.py NAMI_TOOLS / BROOK_TOOLS / etc.
{
    "name": "ask_zoro",
    "description": (
        "把 X / Y / Z 類 query 委託給 Zoro。Zoro 能做：\n"
        "- capability_a: 描述 (快/慢)\n"
        "- capability_b: 描述\n"
        "**何時用**：船長問「...」「...」\n"
        "**何時不用**：你自己能做的（例：web_search/pubmed_lookup）\n"
        "**收到結果後**：用你自己 persona paraphrase，不要照貼結構化原文"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "capability": {"type": "string", "enum": [...]}
        },
        "required": ["query", "capability"]
    }
}

def _tool_ask_zoro(self, input_):
    capability = str(input_.get("capability", "")).strip()
    if capability not in (...):
        return _ToolOutcome(content=f"capability must be ...", is_error=True)
    try:
        if capability == "capability_a":
            from agents.zoro.module_a import func_a
            data = func_a(input_["query"])
            content = render_for_paraphrase(data)
        # ... other capabilities
    except Exception as e:
        logger.exception(...)
        return _ToolOutcome(content=f"Zoro 執行失敗：{e}", is_error=True)
    return _ToolOutcome(content=content, event={...})
```

實際 reference 實作：[gateway/handlers/nami.py:_tool_ask_zoro](../../gateway/handlers/nami.py)（PR #329 `49baadd`）。

## 設計原則

1. **Same-process import**（不過 HTTP / message queue）— 都在 nakama-gateway service 內，零跨服務 overhead
2. **Sync 等結果**（不 async callback）— 對話 mode 簡單；超時用 LLM loop _MAX_ITERS 自然 bound
3. **Single conversation point**（A agent 持續是窗口）— 修修看到的是 A persona 不是 B
4. **Persona handoff via paraphrase**（不直接外露 B 的 raw output）— prompt 教 A「收到結果用自己口吻 paraphrase」
5. **Loop guard 自然天然**（max depth = 1）— B agent 內部不會再 invoke A，不會 ask_zoro→ask_zoro recursion
6. **Capability enum + 錯誤白名單**（不是 free-form）— 邊界清楚，B 暴露什麼 A 才能呼叫
7. **Tool 描述包含「何時用 / 何時不用」**— 否則 LLM 會把 web_search 能做的事繞道 ask_zoro

## 何時不用 Option A — 升 Option B（async via shared state）

明確觸發點：

- **Wall time routinely > 30s**：Slack 對話會 timeout（修修觀感差）
- **跨 session 等結果**：keyword_research 跑 60s 但修修 ack 後想離線等
- **多 agent fan-out**：Nami 同時 forward 給 Brook + Robin 並行（fan-out / collect）
- **需要 state-of-task UI**：修修要看 「Zoro 跑到哪」進度條

Option B 設計（未來真做時參考）：
- 新 SQLite table `delegation_queue` (id, from_agent, to_agent, capability, query, status, result_json, created_at)
- B agent daemon 輪詢 queue → 跑 → 寫 result
- A agent 在 ask_<b> tool 內：
  1. INSERT row + 回 LLM「我已轉給 B，等通知」（pending state）
  2. LLM end_turn 回 Slack「我已轉給 B，完成會通知」
  3. B daemon 完成後 trigger Slack callback（Slack DM update / push notification）

## Option C 不要做（記錄避免誤入）

「A agent 在 Slack channel @B 等回應撈出」— abuse Slack as message bus，反正不要。除錯難、可靠性差、跨 channel 訊息順序亂、debugging trace 跨 message 困難。

## Cross-ref

- 應用案例 PR #329：[project_session_2026_05_03_evening_nami_polish.md](project_session_2026_05_03_evening_nami_polish.md)
- Architecture 討論完整 message log（修修問「Nami pass 給 Zoro 並回傳結果」起手）：本 session
- 設計時機可參考 [feedback_skill_design_principle.md](feedback_skill_design_principle.md) — skill 三層架構，agent 邊界 vs delegation 邊界
- Pattern 同源於 OpenAI Swarm / Anthropic Multi-Agent Research 的 supervisor pattern，Option A 是最 lightweight 變體
