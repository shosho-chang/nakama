---
name: 驗收 LLM artifact 要分清驗收對象（artifact 精確度 vs source material 品質）
description: 設計 acceptance check 前先問「真正要 catch 的問題是什麼」；如果驗收對象就是 ground-truth 本身（如 EPUB binary 圖檔），人眼打分變沒意義
type: feedback
originSessionId: f576b513-b012-4b87-a689-d42f09413728
---
**規則**：設計 LLM artifact 的 acceptance check 時，明確標註「驗收對象 = X，對照標的 = Y」。驗收對象通常是 LLM 輸出（description / summary / annotation）的精確度，不是輸入素材本身。混淆會寫出 misframed 評分項。

**Why:** PR C ch1 v2 acceptance checklist V1 原寫「人眼看 6 張 figure 打分（🟢/🟡/🔴）」決定 PR D Vision LLM 模型選型。修修點出問題：「圖看起來都很清楚啊，是不是直接從教科書抓下來的？如果是那為什麼要評分？」— 圖檔是 `parse_book.py` 從 EPUB zip 直接抽 binary 寫到 attachments，pixel-for-pixel 教科書原圖，當然清楚。要評的是 frontmatter `llm_description` 文字 vs 圖的精確度，不是 figure 本身視覺品質。

修修一個正確的問題就把整個 V1 design 拆穿 — 這是 framing 的鍋，不是修修偷懶。

**How to apply:**

- **寫 acceptance item 前先 ask 三題**：
  1. 這個 check 真正要 catch 的問題是什麼？（hint：通常是 LLM 輸出的精確度 / 完整度 / 有沒有 hallucinate）
  2. 驗收對象是哪個 file / field？對照標的（ground truth）是什麼？
  3. 對照方式是什麼？（人眼？grep？跨模型 head-to-head？）
- **驗收對象是 ground-truth source material 本身（如 EPUB binary 圖檔、原 paper PDF）→ 不該寫人眼判斷項**。素材是 verbatim 從來源抽出，沒有「品質變差」的可能；要評的應該是 LLM 對素材的理解 / 描述 / 摘要。
- **正確 framing 範例（PR C 改寫 V1 的做法）**：派 Sonnet 4.6 sub-agent 對同 13 張 figure 重跑 Vision describe，跟既有 Opus description 做 controlled head-to-head — 對照組明確、評分對象明確（description 文字精確度）、不需要 user manual。
- **適用範圍**：所有寫 acceptance checklist / driver script self-check / PR test plan 的場景；尤其是 LLM-generated artifact（concept page / chapter source / image description / SEO audit report）的人眼驗收。
