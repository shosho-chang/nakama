---
name: reuse 既有 module 前先 inspect inner text，不只看 signature
description: task prompt 寫「reuse X」前要 grep X 的 prompt content + docstring + hardcoded literal；prompt 寫死的 caller context 會綁死 reuse；signature 看不出限制
type: feedback
created: 2026-04-26
---
寫 task prompt / ADR / design doc 時宣稱「reuse 既有 module X」，必須先 grep X 內部的 prompt content / docstring / hardcoded literal / SEED 註解，**不能只看 signature**。signature 看不出 module 內部的 caller-context assumption，prompt 寫死的場景會綁死 reuse。

**Why:** 2026-04-26 SEO Phase 1.5 task prompt PR #167 review 抓到兩個 over-claim:
1. **`agents/robin/kb_search.py:57`** — `search_kb(query, vault_path, top_k=8)` signature 看起來通用，但 line 57 prompt 寫死「使用者正在製作一支 YouTube 影片，主題是：」。Phase 1.5 task prompt §D.2.3 寫「reuse for SEO audit internal link suggestion」是 over-claim — Haiku 在錯誤 context（YouTube 創作者）下排序 KB 結果會給錯誤 internal link 建議
2. **`agents/brook/compliance_scan.py:7-13`** — module docstring 明標 SEED 版本，`MEDICAL_CLAIM_PATTERNS` 只 6 條（治好 / 99.9% / 肝癌 / 乳癌等）。Phase 1.5 task prompt §C L9 寫「reuse for 台灣藥事法 / 醫療法 compliance」是 over-claim — audit 場景需要更完整詞庫，直接 reuse 會給假陰性

兩個都是 review agent (general-purpose)「rule coverage」角度跑出來才抓到 — doc-level review agent 沒抓到（因為 signature 看起來合理）。是 [feedback_design_rationale_trace.md]「保留 X 是為了 Y 前要 trace pipeline」的同類延伸：寫「reuse X」前要 trace X 的 inner text。

**How to apply:**

1. **task prompt / ADR 寫「reuse X」前的 self-check checklist**：
   - `grep -n "purpose\|context\|user\|為.*影片\|hardcoded" X.py` — 看 X 是否假設特定 caller context
   - Read X 的 module docstring — 找 SEED / WIP / Phase X / TODO 標記
   - Read X 的 prompt template（如果是 LLM module）— 找寫死的場景描述
   - Read X 的 const list（如果是 rule-based module）— 看 coverage 是否完整 vs caller 需求

2. **發現 X 不通用時，task prompt 必須明示**：
   - 如果 X 可以擴充 → require caller 加 `purpose: Literal[...]` 參數，或抽 X 內部 prompt template 為 caller-overridable
   - 如果 X 是 SEED / WIP → 在 task prompt 標 caveat「reuse 時意識 SEED 限制 + 在輸出標明降級狀態」
   - 如果 X 完全不能 reuse → require caller 寫 thin wrapper 自己 prompt

3. **review 階段的 catch**：dispatch rule-coverage / domain-expert review agent（不只 doc-level review），讓他們從「caller 視角」看 reuse 是否真的工作 — doc-level 看 reference 對齊度，domain 看實際語義 fit

4. **常見高風險 reuse 場景**：
   - 任何 LLM-based module（prompt 寫死 caller context）
   - 任何 SEED / Phase 1 prefix / TODO 標記的 module
   - 任何 const list-based module（PATTERNS / VOCAB / KEYWORDS）
   - 任何 default kwargs 寫死特定 caller value 的 function

教訓的反向：寫 module 時為了未來 reuse，**避免在 prompt / const 內寫死 caller context**；如果一定要綁，加 `purpose` / `context` 參數讓 caller 可改，或抽出 prompt template 成 caller-overridable parameter。
