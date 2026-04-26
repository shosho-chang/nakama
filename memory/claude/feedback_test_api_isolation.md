---
name: 測試隔離真實 API call 的模式
description: 新增背景任務（例：LLM 抽取）會讓測試不小心打真的 API，用 conftest autouse 自動 mock + pytest marker 例外
type: feedback
originSessionId: 6dc774b2-8ee7-4655-b691-eebe19832245
---
當 handler 觸發「背景 thread 呼叫 LLM」的 side effect（例：Nami end_turn → 背景抽取記憶），既有測試不會知道要 mock，會**靜默打真的 API**（花錢 + flaky）。

## 解法

1. `tests/conftest.py` 加一個 autouse fixture，預設把背景 entry point mock 成 no-op
2. 要真的跑那個函式的測試，加 `@pytest.mark.xxx` 跳過 mock
3. `pyproject.toml` 註冊 marker 避免 unknown-mark warning

## 實作範例（Nami 記憶抽取）

```python
# tests/conftest.py
@pytest.fixture(autouse=True)
def _prevent_real_memory_extraction(request, monkeypatch):
    if request.node.get_closest_marker("real_extractor"):
        return
    monkeypatch.setattr(
        "shared.memory_extractor.extract_in_background",
        MagicMock(return_value=MagicMock(is_alive=lambda: False)),
    )
    # 也 patch 被 handler 匯入的 name
    import gateway.handlers.nami as nami
    monkeypatch.setattr(nami, "extract_in_background", MagicMock())
```

```python
# pyproject.toml
[tool.pytest.ini_options]
markers = ["real_extractor: ..."]
```

**Why**：side-effect side-channel（daemon thread、event loop）難追蹤；autouse mock 是最省力的 default-safe 策略。

**How to apply**：只要新增「handler 觸發背景 LLM / 對外 API / DM」的功能，就加對應的 autouse mock + marker。

## 已踩過的端點

| 端點 | autouse fixture | marker | PR |
|---|---|---|---|
| `shared.memory_extractor.extract_in_background` | `_prevent_real_memory_extraction` | `real_extractor` | 原始 |
| `agents.franky.slack_bot.FrankySlackBot.from_env` → `_NoopSlackStub` | `_prevent_real_slack_alerts` | `real_slack` | #174 |

## Slack leak 案例（PR #174，2026-04-26）

`shared.alerts.alert("error", ...)` lazy-import `FrankySlackBot.from_env`。dev 機 `.env` 有 `SLACK_FRANKY_BOT_TOKEN` + `SLACK_USER_ID_SHOSHO`，`from_env()` 回真 bot 而非 `_NoopSlackStub`。`tests/scripts/test_backup_nakama_state.py` failure-path test 直接打 production Slack，把 pytest tmp_path（`pytest-of-Shosho/pytest-XXX/...does-not-exist`）leak 進修修 DM 9 條訊息。

教訓：「lazy import」不會被 module-level mock 抓到 — 需要 patch class method 本身。pattern: `monkeypatch.setattr("agents.franky.slack_bot.FrankySlackBot.from_env", MagicMock(return_value=_NoopSlackStub()))`。
