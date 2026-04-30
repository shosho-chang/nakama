---
name: Jinja inline JS 表達式：最終 filter 必須是 tojson 或 safe
description: `<script>` 內 `{{ x|tojson if x else "''" }}` 的 else 分支字面字串會被 autoescape 成 `&#39;&#39;`，產生 JS SyntaxError；改成 `{{ (x or '')|tojson }}`
type: feedback
---

`<script>` 區塊內任何 Jinja 表達式 `{{ ... }}` 都會經過 autoescape — autoescape 不認 context（不知道你在 JS 不在 HTML），對 quote / `&` / `<` / `>` 一視同仁轉成 entity。

```jinja
{# BAD — else branch returns Python literal '' which gets autoescaped #}
const filename = {{ download_filename|tojson if download_filename else "''" }};
{# renders to: const filename = &#39;&#39;;  ← SyntaxError #}

{# GOOD — single tojson call, output is JSON-safe AND marked safe #}
const filename = {{ (download_filename or '')|tojson }};
{# renders to: const filename = "";  ← valid JS #}
```

**Why:** `tojson` filter 輸出本身已是 JSON-safe 引號字串，且 Jinja 把它標 safe 後不再 autoescape。但 `if-else` 表達式的 else 分支若是純字面字串（不經 tojson / 不加 `|safe`），autoescape 仍生效。實例 PR #243 的 `kwDownloadBtn` IIFE 因此整段 SyntaxError 失敗（用戶察覺不到 — form-post fallback 還在跑），但每次 page load 留 console error，會 mask 未來 debug 訊號。詳見 PR #268 / Issue #266。

**How to apply:**

1. 任何 `<script>` 內 Jinja 表達式，最終 filter 必須是 `tojson` 或 `safe`
2. 三元改寫範式：`{{ X|tojson if X else "''" }}` → `{{ (X or '')|tojson }}`（單次 tojson 同時處理 truthy/falsy）
3. test 抓法：fetch route GET、grep `&#39;` / `&amp;` / `&lt;` 出現在 inline `<script>` 內就 fail
4. 同類陷阱：URL 構造（`{{ "/path?q=" + x }}` 也會 autoescape `+`），改用 `{{ url_for(...) }}` 或 `{{ x|urlencode }}`
