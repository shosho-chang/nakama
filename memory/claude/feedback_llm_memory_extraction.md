---
name: LLM 抽取記憶的兩個設計陷阱
description: 用 LLM 做記憶抽取時，subject 去重與 content merge 都要把「既有記憶」注入 prompt，否則會發明重複或覆蓋資訊
type: feedback
originSessionId: 6dc774b2-8ee7-4655-b691-eebe19832245
---
當用 LLM 做自動記憶抽取（例：Haiku 從對話抽 preference/fact/decision），直接問「請抽取記憶」會踩兩個坑：

## 坑 1：Subject 語意重複

LLM 每次都重新發明 subject，同概念變成多條。
- 例：「學習目標」「學習計畫」「教育計畫」其實都是博士班

**Why**：LLM 不知道 DB 裡已有哪些 subject。
**How to apply**：抽取前把既有 subject 列表注入 prompt，要求「語意相近直接重用」。

## 坑 2：Content 覆蓋導致資訊遺失

用 `(subject)` 做 dedup key 時，upsert 會把舊 content 整個替換掉新的。
- 例：「專業領域 = 健康長壽」被「正在研究蛋白質」覆蓋 → 健康長壽資訊消失

**Why**：LLM 抽取時只看得到 subject 名稱，看不到舊 content，所以只寫新事實。
**How to apply**：
- 注入 `(subject, content)` pair（不只 subject）
- Prompt 明示「重用 subject 時 content 必須是 merged 版本，包含舊資訊」
- 例外：使用者明確取代（「我改去讀哈佛了」）才覆蓋

## 驗證通過（2026-04-19）

Nami 的 `專業領域` 成功 merge：健康長壽 + 蛋白質 + 睡眠週期，7 條總數不增。
實作見 `shared/memory_extractor.py` + `shared/agent_memory.py` (commit 62007b8)。
