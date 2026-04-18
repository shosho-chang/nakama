---
name: 雙語閱讀 Pipeline 設計決策
description: Robin 雙語閱讀三場景的架構決策、工具選型、開發順序
type: project
created: 2026-04-18
updated: 2026-04-18
originSessionId: 9e5f9200-248f-40e6-96be-0a814625432a
---
雙語閱讀 pipeline 設計凍結（2026-04-18）。

**Why:** 修修閱讀學術論文需要雙語版，PDF/網頁/部落格三種來源各有最佳路線。

**How to apply:** 開發時遵守此決策，不要重新討論已確認的選型。

---

## 三個場景

### 場景 1 & 3：網頁 / 部落格文章
Trafilatura（主）→ 段落切割 → Claude Sonnet + 術語表翻譯 → 雙語 HTML → Robin Reader → Annotation → Ingest

### 場景 2A：學術論文 PDF（Nature / arXiv / pubmed）
BabelDOC → 雙語 PDF（保留版面、公式、表格）→ Robin Reader PDF 模式 → Annotation → Ingest

### 場景 2B：書籍 / 掃描版 / 其他 PDF
Docling → Markdown → Claude Sonnet + 術語表翻譯 → 雙語 Markdown → Robin Reader → Annotation → Ingest

**判斷邏輯**：依 source_type 欄位（`paper` → BabelDOC；其他 → Docling）

---

## 工具選型（已確認）

| 任務 | 工具 | 理由 |
|------|------|------|
| Web 抓取 | Trafilatura（主）+ Firecrawl（fallback） | 免費本地優先 |
| PDF 學術論文 | BabelDOC | 保留版面、公式、雙語 PDF |
| PDF 其他 | Docling | 語義結構保留、Markdown 輸出 |
| 翻譯引擎 | Claude Sonnet | 台灣繁體術語準確、學術細膩度 |
| 術語一致性 | Glossary YAML | 使用者可自行維護，Robin 主動學習 |
| Annotation | 原生輕量（SQLite + JS） | 無外部依賴，Ingest 時一次匯入 |

---

## 翻譯設計細節

- **全文翻譯**（不是按需），求最高品質
- 術語表路徑：`prompts/robin/translation_tw_glossary.yaml`
- 術語表可自動學習：使用者修正後 Robin 寫回 glossary
- System prompt：注入 glossary + 台灣繁體指示
- 台灣術語優先（「粒線體」not「線粒體」）

---

## 新增模組

```
shared/
├── web_scraper.py         # Trafilatura + readability + Playwright
├── translator.py          # Claude Sonnet + glossary + bilingual formatter
└── pdf_parser.py          # 升級 → Docling + BabelDOC

agents/robin/
├── reader_annotation.py   # Annotation CRUD（SQLite）
└── ingest.py              # 擴充 → 讀取 annotations 加入 KB

thousand_sunny/
├── routers/robin.py       # 雙語 reader 模式 + annotation API
└── templates/
    ├── reader_bilingual.html  # 雙語 CSS + JS
    └── reader_pdf.html        # PDF viewer + annotation

prompts/robin/
└── translation_tw_glossary.yaml  # 台灣術語表
```

---

## 開發順序（已確認）

| Phase | 任務 | 估計 |
|-------|------|------|
| P0 | `shared/translator.py` + glossary YAML | 2hr |
| P0 | `shared/web_scraper.py`（Trafilatura） | 2hr |
| P1 | Robin Reader 雙語模式 HTML/CSS/JS | 3-4hr |
| P1 | Annotation 輕量實作（前後端） | 3-4hr |
| P2 | BabelDOC 整合（PDF 雙語） | 2hr |
| P2 | Docling 升級 + 場景切換邏輯 | 3hr |
| P3 | Annotation → Ingest 整合 | 2hr |
