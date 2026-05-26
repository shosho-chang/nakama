---
name: feedback-hitl-gate-serves-subjective-taste
description: HITL gate 同時解「LLM 客觀正確」+「修修主觀品味」兩種職責；LLM 變強只能解前者，gate 不會消失
metadata:
  type: feedback
---

提議 / 評估 HITL gate（promotion review、approval queue、audit review session、Entity 統一審批…）時，要記得 gate 同時做兩件性質**完全不同**的事：

- **A. 客觀正確性判斷** — LLM 抽出的 claim 真的是 source 講的嗎？disambig 對嗎？這類問題 LLM 變強會大幅改善（grounding / citation / fast-track threshold 上調自動吸收）
- **B. 主觀品味判斷** — 這個 entity / concept / draft 值得進**修修的 KB / 文章 / 發布**嗎？這跟修修當下寫什麼、未來想做什麼題目、KB 簡潔性偏好綁在一起 — **LLM 再聰明都不能替修修決定**

## Why

2026-05-26 promotion polymorphism grill，修修問「Person disambig 不是顯而易見嗎？以後 LLM 變聰明可以直接接管 gate 嗎？」

- Person disambig 我場景 2 確實 oversell — 簡單拼字 Huberman vs Hubberman 現代 LLM 早就行
- 但 gate 不只解 disambig（A 類），還解「這個人值得進我 KB 嗎」「這個 entity 是高頻 recurring 還是一次性引用」（B 類）
- B 類**無解** — KB 是修修的個人資產，他想保留 agency
- 呼應 [[user_vault_access_pattern]] 「vault 簡潔性是 first-class concern」— 只有修修自己驗收得出來

## How to apply

- user 問「LLM 變強會不會取代 gate」時，先 disambig：你問的是 A 還是 B？
- 設計 HITL 時，明確標示哪些 invariant 是 A（confidence-based fast-track 可吸收）vs B（永遠進 queue）
- A 類用 confidence threshold（如 ADR-034 `confidence > 0.9` auto-approve）absorb LLM 進步 — 不需 redesign 架構
- 不要因為「LLM 變強就不用 gate」勸阻加 gate — gate 不會消失，只會越來越安靜
- 反向：不要把純 B 類決策（純品味）放進 LLM-only auto path — 例如 Brook compose Line 2 atomic content（ADR-024 §Decision 明令）
- 對應 ADR-024 § "The LLM is the primary recommender; 修修 is the checkpoint / brake" 的精神 — 框架就是這樣設計的
