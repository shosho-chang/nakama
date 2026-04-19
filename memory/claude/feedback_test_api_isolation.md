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

**How to apply**：只要新增「handler 觸發背景 LLM / 對外 API」的功能，就加對應的 autouse mock + marker。
