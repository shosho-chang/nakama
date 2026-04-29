---
name: Jinja macro + caller block 適合「同 partial 多 dialect」refactor
description: chassis-nav 14 templates 有 3 種 dialect（簡潔 / a11y / +version+meta），用 macro + caller block 一檔覆蓋；include 沒 slot 機制做不到
type: feedback
created: 2026-04-29
---

Refactor multi-dialect inlined HTML 抽 partial 時，**macro + caller** 比 include 強。

**Why:** PR #260 Slice 0 抽 chassis-nav，14 templates 分 3 dialect：
- A：`<header class="chassis">` 簡潔（8 個）
- B：franky.html `role="banner"` + `aria-label` + `aria-hidden`（1 個）
- C：`<div class="chassis">` + `chassis-version` + page-specific `chassis-meta` slot（5 個）

C 的 chassis-meta 內容 page-specific（drafts/cost 顯示 `CPT SHOSHO`、index 多顯示 `TPE clock`），如果用 `{% include %}` 沒 slot 機制做不到。Jinja macro + `{% call %}` block 才是 canonical 做法。

**How to apply:**
- 抽 partial 之前先 sample N 個既有 inline 看格式差異（grep 不只 `<header.*chassis`，要包含 `<div class="chassis"` + `class='chassis'` 等變體）
- 純參數差用 `{% include %}` 或 `{{ macro(args) }}`
- 有 page-specific HTML slot → macro 接受 caller，consumer 用 `{% call macro(...) %}body{% endcall %}`
- 用 `{% if caller is defined %}` 偵測是否在 call block 內（include-style 直呼也安全 fallback）
- 範本：`thousand_sunny/templates/bridge/_chassis_nav.html`（chassis-nav）和 `_breadcrumb.html`（純參數版本）
