---
name: 雙語閱讀 Pipeline 設計決策
description: Robin 雙語閱讀三場景架構決策、工具選型、各 Phase 完成狀態
type: project
originSessionId: ea82060e-3d51-44bc-a470-e61162514715
---
雙語閱讀 pipeline 設計凍結（2026-04-18）。

**Why:** 修修閱讀學術論文需要雙語版，PDF/網頁/部落格三種來源各有最佳路線。

**How to apply:** 開發時遵守此決策。PubMed flow 已完成 (2026-04-22)，其餘 Phase 照原順序推進。

---

## 三個場景

### 場景 1 & 3：網頁 / 部落格文章 — ✅ P1 done
Trafilatura → Claude Sonnet + 術語表 → 雙語 md → `/scrape-translate` → reader

### 場景 2A：學術論文 PDF（Nature / arXiv / PubMed）
- ✅ PubMed flow (PR #71)：pymupdf4llm + translator + reader（Phase 1 pragmatic）
- ⬜ P2 BabelDOC：保留版面/公式/表格的雙語 PDF（需 Immersive Translate API key）

### 場景 2B：書籍 / 掃描版 / 其他 PDF — ⬜ P2
Docling → Markdown → translator → reader

---

## 工具選型

| 任務 | 工具 | 狀態 |
|------|------|------|
| Web 抓取 | Trafilatura（主）+ Firecrawl（fallback） | ✅ done |
| PDF 一般（含 PubMed） | pymupdf4llm + pdfplumber tables | ✅ done |
| PDF 學術高保真 | BabelDOC | ⬜ P2 |
| PDF 書籍/掃描 | Docling | ⬜ P2 |
| 翻譯引擎 | Claude Sonnet + 台灣術語表 | ✅ done |
| 術語自動學習 | Glossary YAML user_terms | ✅ done |
| Annotation | 原生輕量（text range + frontmatter） | ✅ done |
| Annotation → Ingest | append 回 source page | ⬜ P3 |

**設計 deviation 記錄**：原訂 Docling 做通用 PDF，實作時沿用現有 pymupdf4llm（VPS 3.8GB RAM 撐不住 torch+transformers）。Docling 若未來發現 pymupdf4llm 品質不夠可升級。

---

## 翻譯設計

- 全文翻譯（非按需），求最高品質
- 術語表：`prompts/robin/translation_tw_glossary.yaml`
- System prompt 注入 glossary + 台灣繁體指示
- 台灣術語優先（「粒線體」not「線粒體」）

---

## 已完成模組

```
shared/
├── web_scraper.py         ✅ Trafilatura + readability + Firecrawl fallback
├── translator.py          ✅ Claude Sonnet + glossary + bilingual formatter
└── pdf_parser.py          ✅ pymupdf4llm + pdfplumber

thousand_sunny/
├── routers/robin.py       ✅ /read + /save-annotations + /mark-read
│                          ✅ /scrape-translate (web)
│                          ✅ /pubmed-to-reader (PubMed PDF)
│                          ✅ base=inbox|sources whitelist
└── templates/robin/
    └── reader.html        ✅ Markdown 雙語切換 + highlight + note

prompts/robin/
└── translation_tw_glossary.yaml  ✅
```

---

## 剩餘 Phase

| Phase | 任務 |
|-------|------|
| P2 | BabelDOC 整合（學術論文雙語 PDF，保留版面） |
| P2 | Docling 升級（書籍 / 掃描 PDF） |
| P3 | Annotation → Source page 自動 append「我的筆記」 |
