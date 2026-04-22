---
name: Brook Style Extraction 已完成
description: 2026-04-22 完成 36 篇樣本抽取，3 類風格側寫 + 交叉分析，存在 agents/brook/style-profiles/
type: project
---

# Brook Style Extraction — 2026-04-22 完成

完成內容：修修手動挑選 36 篇代表作（讀書心得 10 / 人物 13 / 科普 13），Claude 三個並行 sub-agent 各自抽取一份 style profile，最後交叉分析產出 `_extraction-notes.md`。

## 產出檔案

```
agents/brook/style-profiles/
├── book-review.md         (10 篇樣本, 22KB, 402 行)
├── people.md              (13 篇樣本, 21KB, 396 行)
├── science.md             (13 篇樣本, 25KB, 380 行)
└── _extraction-notes.md   (交叉分析 + Brook 載入守則)
```

## 關鍵發現（最 load-bearing 的 3 個）

1. **三類風格差異大到必須分開 profile** — 書評禁 emoji / 人物 🧠 招牌 / 科普頻繁；「我」vs「你」比例在三類完全相反
2. **科普文 unique**：「年份 + 期刊 + 連結 + 白話結論」四件套，每千字 2-4 個引用；中心比喻（BDNF=肥料、mTOR=總承包商）是骨架
3. **人物文是 podcast 預告片不是懶人包** — 結尾刻意留白，不做 bullet takeaway

## Brook compose 的實作含意（_extraction-notes.md 第 3、5 節）

- `compose.py` 需要 `load_style_profile(category)` helper
- Prompt 結構：identity + category guidance + few-shot A + few-shot B + user topic
- **不要三類都塞進 context**（稀釋訊號）
- Few-shot A/B/C 輪替避免過擬合
- 科普需兩階段：research → compose（非一槍出稿）
- **禁止虛構修修的個人錨點**（裸辭、環島、育兒等）— Brook 沒有這些真實經驗

## 迭代機制

- 每 10 篇 Brook 產出後做 HITL 批註 → `_iteration-log.md`
- 每季用修修新作增量更新
- 拒絕率 4 週 > 30% 觸發 re-extract

## How to apply

- Phase 1 Week 2 實作 `compose.py` 時讀這四份檔
- 電子報和 IG / YT 改寫另建 profile（Phase 2），科普 profile 不直接套電子報
- 若修修交來書評 / 人物 / 科普以外的主題（意見文、平台宣言）→ Brook 回報人工處理，不硬寫
