---
name: CSS `display:` 寫法會 shadow `[hidden]` 預設 display:none
description: 任何 selector 寫 `display: flex/grid/block` 都要同時補 `selector[hidden] { display: none }`，否則 `<div hidden>` 失效
type: feedback
originSessionId: ae6ace4c-6a20-4ab2-bc01-ec47c0980825
---
**規則：CSS 對某 selector 寫 `display:` 時必補 `selector[hidden] { display: none; }`，否則 HTML5 `[hidden]` attribute 預設 `display: none` 會被靜默蓋掉。**

**Why:** PR #253 抓到的 SEO 中控台 audit progress page bug — `.error-box { display: flex }` 蓋掉 user-agent stylesheet 的 `[hidden] { display: none }`（same specificity 0,1,0，later declaration wins），導致 `<div class="error-box" id="error-box" hidden>` **從打開頁面那一刻就永遠顯示「audit failed」字樣**，跟 audit 真實狀態無關。修修第一篇 end-to-end QA 走進來看到假警報，diagnose 30 分鐘才抓到根因不是後端 race 而是 CSS。修法 1 行：`.error-box[hidden] { display: none; }` 重新主張 `[hidden]` 語意。

**How to apply:**

寫任何 JS-toggled hidden component（`errBox.hidden = false` 模式）時 — Bridge mutation 對話框、表單錯誤訊息、loading state 等 — 在 CSS 寫 `display:` 後必同時加 `[hidden]` override：

```css
.error-box {
  display: flex;          /* component layout when visible */
  flex-direction: column;
  ...
}
.error-box[hidden] { display: none; }   /* restore HTML5 hidden */
```

也適用於 `dialog`, `details`, `template`, `[aria-hidden]` 風格選擇器。Bridge UI native `<dialog>` modal 模式（PR #140）因為 `dialog:not([open]) { display: none }` 是 user-agent 預設且 specificity 較高，沒這坑；但其他自家 component 都要自覺補。

**Diagnose 訊號**：用戶反映「UI 顯示失敗訊息但後端 log 顯示成功」→ 先 grep 字面字串 source（`grep "audit failed" templates/`）找到 hidden element → 看 CSS 是否寫了顯式 `display:`。比 deep dive 後端 race condition 快 100 倍。

對齊：[reference_bridge_ui_mutation_pattern.md](reference_bridge_ui_mutation_pattern.md) — Bridge 寫新 hidden-toggled component 時引用本條。
