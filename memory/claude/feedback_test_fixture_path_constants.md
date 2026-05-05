---
name: test_fixture_path_constants
description: 測試 fixture 寫入路徑必走 production 用的同一個常數，不能複製 hardcoded literal — 否則 fixture + 壞掉的常數內部一致、tests 全綠、production 寫到不存在的目錄（PR #419 Slice 5 教訓）
type: feedback
---

Slice 5 v2 sync 的 `_KB_CONCEPTS_SIMPLE = "KB/Concepts"` 是 hardcoded literal（少了 `Wiki/` 子層）；`tests/agents/robin/test_annotation_merger_v2.py` 也直接寫 `vault / "KB" / "Concepts" / f"{slug}.md"`。**兩邊內部一致，tests 全綠**，但真實 vault 路徑是 `KB/Wiki/Concepts/`。生產 sync 對著不存在的目錄 silently no-op，沒人發現直到 manual QA 跑端到端才暴露。

**Why:** Hardcoded fixture path 跟 hardcoded production literal 配對，會形成「測試 + bug 自洽」的封閉循環。所有 assertion 都是「production 寫到 X，fixture 在 X 看到 X」— 如果 X 在生產上根本不存在，tests 永遠不會發現。這是 path/config 版本的「monkeypatch 錯 namespace」（見 `feedback_pytest_monkeypatch_where_used`）。

**How to apply:** 任一 fixture 觸碰路徑、URL、config key 必須**從 production module import 同一個常數**，不複製 literal：

- ❌ `vault / "KB" / "Concepts" / f"{slug}.md"`
- ✅ `from shared.kb_writer import KB_CONCEPTS_DIR`；`vault / KB_CONCEPTS_DIR / f"{slug}.md"`
- ✅ 或退一步：`vault / "KB" / "Wiki" / "Concepts"`（路徑跟 production 對得上、靠結構 review 而非 import — 但比較弱）

review 時：grep test 內 `"KB/"` `"data/"` 等字串 literal — 出現多次就是警訊，要不要 import constant？

對 ADR-017、ADR-011 之類的 path 規範 — production constant 是 single source of truth，fixture 也必須引用，不能各自寫死。
