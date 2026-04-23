---
name: 宣稱 ParseError 契約時要包 Pydantic ValidationError + KeyError
description: 模組若對外宣告「parse 失敗 / 缺欄位一律 raise XxxParseError」，必須同時攔 json.JSONDecodeError、pydantic.ValidationError、KeyError、TypeError — 漏一個就成契約違反
type: feedback
tags: [error-handling, pydantic, api-contract]
originSessionId: 23d6fe90-ddb9-4038-946e-a916801421f8
---
當模組對外宣告某個 `XxxParseError` 是「parse class 失敗的 single exception」時，**不能只攔 `json.JSONDecodeError`**。下面這三個會從 Pydantic / dict 取值處漏出來：

1. `pydantic.ValidationError` — LLM 回合法 JSON 但違反 schema 的 length / pattern / Literal 限制（title 太短、slug pattern 不符、primary_category 不在 Literal）
2. `KeyError` — LLM 漏某個必要 key（`metadata["focus_keyword"]` 直接炸）
3. `TypeError` — LLM 回 `"slug_candidates": "abc"`（字串而非 list），`list(str)` 做出 `["a","b","c"]` 後續 Pydantic fail；或 `None` 餵到 `int(...)` / `str(...).lower()`

**Why:** PR #78 review 抓到 score 85 blocker — `agents/brook/compose.py` 宣稱 `ComposeOutputParseError` 是 parse class exception，但 `DraftV1(...)` 構造是直接呼叫 Pydantic，LLM 回 title="太短" 時噴 raw `ValidationError` 給 caller。caller 端只 `except ComposeOutputParseError` 就會整個炸開。

**How to apply:**
- 任何「把外部輸入轉 internal schema」的函式，構造 `BaseModel(...)` 那行一律 `try/except (ValidationError, KeyError, TypeError) → raise XxxParseError(f"...: {e}") from e`
- 不要只包 `json.loads` — 合法 JSON 違反 schema 是 LLM 最常見的 drift 模式
- 測試要分三個 case：JSON 解析失敗、schema validation 失敗、缺 key；不要只測「能 parse 也能 build」的 happy path
- 案例：PR #78 第一輪 merge 前被 reviewer 擋下，補 3 個 regression test（title 太短 / 缺 focus_keyword / CJK slug）
