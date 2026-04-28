---
name: facade source mock 不替代 caller binding mock
description: caller 用 `from shared.llm import ask` 後 mock 必 patch caller-module binding，patch facade source 不生效（Phase 4 LLM facade 試 facade-layer mock 失敗的教訓）
type: feedback
originSessionId: 69da2044-bfc8-455c-95b1-f58070344259
---
caller 用 `from shared.llm import ask` 把 `ask` bound 進自己 module namespace 後，**`patch("shared.llm.ask")` 完全不生效** — caller 內部 lookup 走自己的 binding，不會 reach back to source module。

**Why**：`from X import Y` 是 attribute copy，不是 reference indirection。Module load time `Y = X.Y` 已 bind；之後 patch X.Y 只改 source module 上的 attribute，caller 自己的 namespace 還是原本 object。

**How to apply**：
- Mock LLM caller 必 patch caller-module binding（`patch("agents.brook.compose.ask_multi", ...)`、`patch("shared.translator.ask", ...)`），不是 facade source（`patch("shared.llm.ask", ...)`）
- 唯一例外：caller 用 `import shared.llm` + `shared.llm.ask(...)` form（lazy lookup），那 patch source 才有效；但 production code 都用 `from X import Y`
- `feedback_pytest_monkeypatch_where_used.md` 是同一條 rule 的 generalize：「monkeypatch 要 patch code 實際讀名字的 namespace」
- 教訓觸發於 LLM facade Phase 4：plan PR #4 寫「mock 改在 `shared.llm.ask` 層」實際 invalid — caller-binding mock 是正確設計，不是 brittle pattern；plan 描述要修正
