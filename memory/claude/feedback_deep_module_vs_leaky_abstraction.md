---
name: deep module vs leaky abstraction
description: 看到「small interface + large impl」前先分清是 Ousterhout 深模組（好事）還是 leaky abstraction（cost 不透明）；前者保留架構、後者修 docstring，都不是 shallow facade
type: feedback
originSessionId: 04f37d13-15b9-43e9-a664-4fb9a48bffa8
---
「small interface + large impl」是 Ousterhout〈A Philosophy of Software Design〉推崇的**深模組（deep module）**範式 — 是好事不是 bug。被誤當「shallow interface 隱藏 deep impl」拆掉，等於把 dispatcher 推上 caller、isinstance ladder 換個位置 — **zero-net cohesion shift**。

**Why**：審 PR / 跑 architecture audit 時看到一個短介面背後藏複雜實作，反射可能是「太 shallow / 隱藏 cost / 應該拆 → 4 個 public function」，但 Ousterhout 原則正是讚這種形狀。只有當 cost 真的對 caller 重要（LLM call、外部 I/O、DB transaction）**卻沒在 docstring 揭露**時，才是 leaky abstraction — 修法是 docstring 揭露 cost 給 caller batch-budget，**不是重構成 shallow facade**。

**How to apply**：審查「短介面 + 深實作」時兩問：
1. cost 對 caller 重要嗎？（LLM call / 網路 / DB transaction / 大檔 I/O）
   - 不重要 → 純 deep module，沒事
   - 重要 → 可能是 leaky abstraction（go to step 2）
2. docstring 有揭露 cost 嗎？
   - 有 → deep module + transparent cost = 完美
   - 沒有 → leaky abstraction，docstring 加 cost note 即可，不重構

**反例特徵**（這些是 deep module 不是 bug）：
- 4-action dispatcher 內部分派（caller 一次傳 whole bag、不必知道 action 分支邏輯）
- 統一寫入入口蓋多步驟（load → migrate → backup → write）
- 抽象工廠 / repository 模式蓋 ORM 細節
- ADR 凍結的「按 X 分派」介面

**真 bug 特徵**（這些才是 shallow facade 該重構）：
- 介面跟實作 1:1 mapping（無抽象價值，只是 typing layer）
- 介面 surface 比實作 surface 大（args 多但內部判斷少）
- caller 必須知道內部分支才能正確呼叫（dispatcher 沒做事）

**教訓來源**：2026-04-28 audit ③ kb_writer dispatcher（PR #220）— audit skill 把「SHALLOW interface 隱藏 deep impl」當 bug，但檢查 ADR-011 §3.5 凍結 dispatcher + caller 乾淨度後確認是 deep module；真 friction 只是 update_merge LLM cost 不透明，docstring 1 行修。
