---
name: 設計 doc 凍結後發現現成輕量方案要 flag
description: 實作時若發現既有模組已能解決且更輕量，停下來明說 deviation 理由再動手
type: feedback
originSessionId: ea82060e-3d51-44bc-a470-e61162514715
---
若設計 doc 凍結了某個技術選型（例如 Docling），實作前先 grep 既有 codebase 看有無相似功能的 lighter 版本。發現了就停下來：
1. 在對話中 flag 這個 deviation
2. 說明為什麼 lighter 方案足夠（VPS 資源、現有整合、測試覆蓋）
3. 給 user 決定（或在 auto mode 下自己決定但在 PR description 明說）

**Why:** 2026-04-22 做 PubMed 雙語閱讀時，原設計 doc 指定 Docling（2GB+ torch/transformers），實作時發現 `shared/pdf_parser.py` 已用 pymupdf4llm 產 markdown 且 VPS 只有 3.8GB RAM 會 OOM。PR #71 選 pymupdf4llm，Docling 留為未來品質升級選項。

**How to apply:**
- 準備動手前 grep 既有 shared/ 與類似用途模組
- 發現現成方案就停下來比較（資源、品質、維護成本）
- Auto mode 下仍需在 PR description 開獨立段落解釋 deviation，讓 user 閱 PR 時能 sanity check
- 不是所有 deviation 都要問 user — 純技術實作可自決，架構/UX 差異仍得按 `feedback_ask_on_architecture.md` 先問
