---
name: pytest monkeypatch 要 patch 使用處不是定義處
description: 測試 mock 要 patch 模組的 local name（`from X import Y` 進來的），不是原始定義處；module-level cache / top-level import 都是同類陷阱
type: feedback
---

pytest 寫 mock 時，`monkeypatch.setattr` / `patch` 的**目標要是 code 實際讀取該 symbol 的命名空間**，不是該 symbol 的原始定義處。module-level cache 的 boolean / object 同樣要 patch 所在模組，別 patch 依賴來源。

**Why:** 2026-04-24 一個 session 兩次踩到：

1. **`from X import Y` top-level import**（PR #118 TS brook router test）：
   - `thousand_sunny/routers/brook.py` 第 10 行 `from agents.robin.kb_search import search_kb`
   - 測試寫 `monkeypatch.setattr("agents.robin.kb_search.search_kb", mock_fn)` → **沒效果**
   - 因為 router 內部的 `search_kb` 名字已經指向 module load 時抓到的原始 function object，跟 `agents.robin.kb_search` module attribute 的 later 變更脫鉤
   - 正確：`monkeypatch.setattr("thousand_sunny.routers.brook.search_kb", mock_fn)`

2. **Module-level boolean cache**（PR #116 brook compose test）：
   - `agents/brook/compose.py` 有 `_tables_ready = False` module global + `_ensure_tables()` 只在 False 時建表
   - `tests/conftest.py isolated_db` autouse 每 test 切新 SQLite tmp file
   - 問題：test A 跑完後 `_tables_ready=True` **持續跨 test 存活在 module namespace**，test B 的新 DB 沒 brook 表、INSERT 炸 `no such table`
   - 解：autouse fixture `monkeypatch.setattr(compose, "_tables_ready", False)` 每 test 重置

**How to apply:**

- **Rule of thumb：看 code 怎麼拿到這個名字、就 patch 那個路徑**。
  - `from X import Y` in `foo.py` → patch `foo.Y`
  - `import X; X.Y()` in `foo.py` → patch `X.Y`（可以 patch 原始 module）
  - module-level `_cache = ...` → patch `foo._cache`（test 間 reset）
- **寫測試時先 assert mock 被呼叫**（`mock.assert_called_once()` 或 `captured["x"]` assertion），不要只 assert side effect。側效可能是舊資料 / cached state，mock 沒 reach 仍會「綠」但沒意義
- **遇到「本機綠、測試抓不到」pattern** 時首先想：
  1. 是不是 patch 到錯的命名空間？
  2. 是不是有 module-level state 在 test 間 leak？
  3. 是不是有 `from ... import` top-level 鎖住了 local ref？
- **PR #116 / #118 實例**，同一 session 兩次，代表這 pattern 在 nakama repo 會一直出現
- 類似前例：`feedback_windows_abs_path_silent.md`（Windows POSIX path 在 conftest autouse isolate）、`feedback_test_api_isolation.md`（conftest autouse mock 背景 LLM）— 都是「test 隔離 vs module state」的變體
