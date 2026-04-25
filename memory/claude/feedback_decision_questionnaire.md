---
name: 需要意見的決策走獨立 questionnaire 檔，不混在對話裡
description: 設計提案有未決選項時，獨立開 docs/plans/*-decisions-{date}.md 做 checkbox + comment 區塊，修修在 PR file diff / 本機編輯回覆，不要把選項列在 chat 訊息或 PR description 裡
type: feedback
originSessionId: d5dcc78c-17a9-4f10-ad41-93e6142c64b2
---
**規則：架構/設計提案有 ≥ 2 個未決選項要給修修拍板時，獨立開一個 questionnaire 檔，checkbox + comment 區塊。不要把選項全列在 chat 訊息或 PR description 裡。**

**Why:** 修修明確指令（2026-04-25 PR #150 textbook ingest 5 題提案後）：「以後要我給意見的話，能不能 create 一個新的 document，讓我可以去勾選項或是加 comment？混在對話裡面，有點不好閱讀跟註解。」

混在 chat 不好回覆 — 修修要勾選一題還得另外打字、還要說「我選 A、B 不要、C 改成 X」，這是介面摩擦。獨立檔案讓他能 inline edit、留 comment 在對應段落、版控上看 diff。

**How to apply:**

### 觸發條件

提案 doc 寫到一半發現有 ≥ 2 個未決選項要修修拍板時，**多開一個 sibling 檔**：
- `docs/plans/{topic}-design-{date}.md` — 提案本體（5W1H、tradeoff、我的傾向）
- `docs/plans/{topic}-decisions-{date}.md` — questionnaire（每題 checkbox + comment 區）

### Questionnaire 檔範本結構

```markdown
# {Topic} — Decision Questionnaire

**用法**：每題勾選一個選項（用 `[x]` 取代 `[ ]`），需要 nuance 寫在 Comments / overrides 區。

對應提案：[link]
背景：[memory link]

---

## Q1 — {問題簡述}：{選項 A 還是 B?}

**Tradeoff**：
- **A**（建議）：{一句話 why}
- **B**：{一句話 why}

**選一個**：

- [ ] **A — {label}**（我的建議；理由）
- [ ] **B — {label}**
- [ ] **C — {hybrid / 拒答 / 其他}**

**Comments / overrides**：

> _（修修在這裡寫補充 / 例外 / 反對理由）_

---

（重複 Q2、Q3...）

---

## 補充自由提問區

> _（修修自由 input — 想到我沒列到的題）_

---

## 拍板後我做什麼

{1-5 步收尾流程，例如：合併 questionnaire → 提案 doc 更新成 Decided → 升級 ADR → 開工}
```

### 不要做的事

- ❌ 把選項列在 chat 訊息裡讓修修打字回覆
- ❌ 把選項塞在 PR description 裡（PR description 不適合 checkbox edit + comment 互動）
- ❌ 提案本體 doc 內用 `⚠️ 修修決策點` 標一行就期待修修在 PR review comment 回 — 那只是標記未決，不是 review 介面
- ❌ 用 GitHub PR review comment 取代（comment thread 跟整體 doc 結構脫鉤，後續對齊困難）

### 例外

- 1 題未決 → 直接 chat 問 OK，不用獨立檔
- 純 yes/no（沒選項 tradeoff）→ chat 問 OK
- 緊急 / 對話 in-flight 的 micro-decision → chat 問 OK
