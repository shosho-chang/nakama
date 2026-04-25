# Vault Ingest Flow — IA Diagram (v1, mermaid baseline)

**Status**: data-faithful baseline。下一步 Claude Design handoff 美學迭代後，把成品圖嵌回本檔（mermaid 留作 source of truth）。

**對齊**：[project_vault_ingest_flow_drift_2026_04_25.md](../../memory/claude/project_vault_ingest_flow_drift_2026_04_25.md) 的 5 條流程 + 3 條 schema drift + 桌機 / VPS 分工（[feedback_compute_tier_split.md](../../memory/claude/feedback_compute_tier_split.md)）。

---

## 1. 全景流程

```mermaid
flowchart TD
    %% External sources
    PUBMED[("PubMed RSS<br/>(20+ feeds)")]
    WEB[("任意網頁 URL")]
    CLIPPER[("Obsidian Web Clipper<br/>(Chrome / Safari)")]
    PAPER_PDF[("論文 / 書 / 文章<br/>PDF / md / docx")]

    %% Ingest entry points
    subgraph VPS[VPS 2vCPU 4GB · Asia/Taipei]
        DIGEST_CRON[Robin daily digest<br/>cron 05:30]
        SCRAPE[POST /scrape-translate<br/>Trafilatura + Sonnet]
        PUBMED_READER[GET /pubmed-to-reader<br/>parse_pdf + Sonnet]
        ROBIN_START[POST /robin/start<br/>ingest pipeline]
        AGENT_LOOP[Slack agent loop<br/>Nami / Brook / etc.]
    end

    subgraph DESKTOP[桌機 RTX 5070 Ti · 64GB RAM]
        DOCLING[(Docling parser<br/>❌ 未開發)]
        EPUB[(EPUB / Word parser<br/>❌ 未開發)]
        TEXTBOOK[(整本書 ingest<br/>❌ 未開發)]
    end

    %% Vault landing zones
    INBOX[Inbox/kb/*.md<br/>frontmatter drift A]
    RAW_ART[KB/Raw/Articles/]
    RAW_BOOK[KB/Raw/Books/]
    RAW_PAPER[KB/Raw/Papers/]
    SRC[KB/Wiki/Sources/]
    SRC_PUBMED[KB/Wiki/Sources/<br/>pubmed-{pmid}.md<br/>schema: paper_digest]
    SRC_BILING[KB/Wiki/Sources/<br/>pubmed-{pmid}-bilingual.md]
    DIGESTS[KB/Wiki/Digests/<br/>PubMed/YYYY-MM-DD.md]
    CONCEPTS[KB/Wiki/Concepts/]
    ENTITIES[KB/Wiki/Entities/]
    ATT_PUBMED[KB/Attachments/<br/>pubmed/{pmid}.pdf or .md]

    %% Flows
    PUBMED --> DIGEST_CRON
    DIGEST_CRON -->|abstract only| SRC_PUBMED
    DIGEST_CRON -->|index| DIGESTS
    DIGEST_CRON -->|OA PDF/HTML| ATT_PUBMED

    WEB --> SCRAPE
    SCRAPE -->|bilingual md| INBOX

    ATT_PUBMED -->|user click| PUBMED_READER
    PUBMED_READER -->|bilingual md| SRC_BILING

    CLIPPER -->|drop md| INBOX
    INBOX -->|user pick| ROBIN_START
    ROBIN_START -->|copy| RAW_ART
    ROBIN_START -->|copy| RAW_BOOK
    ROBIN_START -->|copy| RAW_PAPER
    ROBIN_START -->|chunk + LLM| SRC
    ROBIN_START -->|extract| CONCEPTS
    ROBIN_START -->|extract| ENTITIES

    PAPER_PDF -.-> DOCLING
    PAPER_PDF -.-> EPUB
    PAPER_PDF -.-> TEXTBOOK
    DOCLING -.-> SRC
    EPUB -.-> SRC
    TEXTBOOK -.-> SRC

    %% Read paths
    SRC --> AGENT_LOOP
    SRC_PUBMED --> AGENT_LOOP
    CONCEPTS --> AGENT_LOOP
    ENTITIES --> AGENT_LOOP

    classDef gap fill:#f9d6d6,stroke:#c44,stroke-dasharray: 4 4,color:#333
    classDef vpsNode fill:#e6f2ff,stroke:#3a7
    classDef deskNode fill:#fff2e6,stroke:#a73
    classDef vault fill:#f0f0f0,stroke:#888

    class DOCLING,EPUB,TEXTBOOK gap
    class DIGEST_CRON,SCRAPE,PUBMED_READER,ROBIN_START,AGENT_LOOP vpsNode
    class INBOX,RAW_ART,RAW_BOOK,RAW_PAPER,SRC,SRC_PUBMED,SRC_BILING,DIGESTS,CONCEPTS,ENTITIES,ATT_PUBMED vault
```

---

## 2. 5 條既有流程 / 對齊度

| # | 流程 | 觸發 | 寫入 | 翻譯？ | 對齊度 | 主要 gap |
|---|------|------|------|-------|--------|---------|
| 1 | Robin Daily Digest | cron 05:30 | `KB/Wiki/Sources/pubmed-{pmid}.md` + `KB/Wiki/Digests/PubMed/{date}.md` + OA 全文到 `KB/Attachments/pubmed/` | ❌ 只 metadata | 80% | 非 OA paper 沒全文路徑（付費期刊抓不到） |
| 2 | Reader 翻譯 | user 點 daily digest 連結 → `/pubmed-to-reader` | `KB/Wiki/Sources/pubmed-{pmid}-bilingual.md` | ✅ Sonnet on-demand | 40% | 修修以為 Reader 內有「按下去翻譯」按鈕；實作是 ingest 時預翻譯 + Reader toggle |
| 3 | scrape-translate | user POST URL | `Inbox/kb/{slug}.md`（雙語 frontmatter） | ✅ Sonnet | 70% | 缺 Chrome plugin 一鍵剪 |
| 4 | Robin /start ingest | user POST filename | `KB/Wiki/Sources/{slug}.md` + `Concepts/` + `Entities/` + `KB/Raw/<type>/` | ❌ 不翻譯 | 90% | Inbox/kb 不會自動進來，要 user 手動觸發 |
| 5 | EPUB / 整本書 ingest | — | — | — | 0% | **完全沒實作**（[project_textbook_ingest_design_gap.md](../../memory/claude/project_textbook_ingest_design_gap.md)） |

---

## 3. Schema drift（frontmatter 三種形狀）

```mermaid
flowchart LR
    subgraph A[Drift A: Inbox/kb 雜項]
        A1[Web Clipper 寫]
        A2[scrape-translate 寫]
        A3["frontmatter 形狀:<br/>title / source / source_type / content_nature / bilingual<br/>(scrape-translate)"]
        A4["或:<br/>title / source / author / tags=[clippings]<br/>(Web Clipper, 不規範)"]
    end

    subgraph B[Drift B: KB/Wiki/Sources/pubmed-*.md]
        B1[Robin daily digest 寫]
        B2["frontmatter 形狀（PR #148 後）:<br/>pmid / doi / journal / quartile / sjr / scores /<br/>source_type=paper / content_nature=research /<br/>lang=en / type=paper_digest"]
    end

    subgraph C[Drift C: KB/Wiki/Sources/*-bilingual.md]
        C1[pubmed-to-reader 寫]
        C2["frontmatter 形狀:<br/>title / pmid / source / source_type=paper /<br/>content_nature=research / bilingual=true /<br/>source_kind / derived_from"]
    end

    LIFEOS["LifeOS CLAUDE.md §4<br/>『Source Summary』schema<br/>(canonical)"]

    A -.x.- LIFEOS
    B -.x.- LIFEOS
    C -.x.- LIFEOS

    classDef drift fill:#fff2e6,stroke:#a73
    class A,B,C drift
```

**對齊狀態**：A / B / C 三種 schema 跟 LifeOS canonical schema 都對不齊；要做就是三條 ingest 共用一份 frontmatter 標準（待設計）。

---

## 4. 桌機 / VPS 分工

| 流程 | 落點 | Why |
|------|------|-----|
| Robin daily digest（cron） | VPS | abstract-only，輕量 LLM call，要 24/7 always-on |
| scrape-translate | VPS | Trafilatura 輕量、Sonnet via API，無本地 GPU 需求 |
| pubmed-to-reader | VPS | parse_pdf 輕量、Sonnet via API |
| Robin /start ingest（OA paper / 短文章） | VPS | 單檔 chunking 對 4GB RAM OK |
| EPUB / Word parser | 桌機 | 中文書帶圖、整檔長 |
| 整本教科書 ingest | 桌機 | 1500 頁 × chunking + embedding 桌機 RAM 才裝得下 |
| Docling 高保真 PDF | 桌機 | torch + transformers > 4GB RAM |
| 本地 LLM batch（Qwen 3.6 etc.）| 桌機 | GPU 對 batch 比 API 省 |
| Bridge UI / Slack agent loop | VPS | 純 API，永遠在線 |

**vault 是同步介面**：桌機寫 → Obsidian Sync → VPS 讀，不需桌機在線。

---

## 5. 缺口清單（按優先序）

| 缺口 | 大小 | 落點 | 規劃狀態 |
|------|------|------|---------|
| Daily digest 補 `content_nature` / `lang` / `source_type` | 小 | VPS | ✅ PR #148 收 |
| frontmatter schema 統一（三條 drift） | 中 | VPS + 桌機 | 待設計 |
| Reader 內按需翻譯按鈕 vs 預翻譯設計分歧 | 中 | VPS | 待設計討論 |
| Translator A/B 試 Opus 4.7（cost vs Sonnet quality）| 小 | VPS | 一篇就能驗 |
| Annotation → Source page append「我的筆記」 | 中 | VPS | [project_bilingual_reader_design.md](../../memory/claude/project_bilingual_reader_design.md) P3 |
| Chrome Extension 一鍵剪 → thousand_sunny | 中-大 | VPS（API 都有，缺 plugin shell）| ⬜ |
| EPUB parser（中文帶圖）| 中 | 桌機 | ⬜ 完全缺 |
| Word (.docx) parser | 小-中 | 桌機 | ⬜ 完全缺 |
| 整本書 / 教科書 ingest workflow | 大 | 桌機 | [project_textbook_ingest_design_gap.md](../../memory/claude/project_textbook_ingest_design_gap.md) |
| Inbox/kb 自動 → Robin pipeline | 小 | VPS | Web Clipper 進來後不自動處理，要修修手動點 /start |

---

## 6. 後續

1. 修修對 mermaid 內容驗資訊（路徑 / 觸發 / 寫入點正確？）
2. 用 Claude Design 把這份美學迭代成 IA poster（dark mode、token-driven、可貼進 Bridge dashboard 或 vault dashboard）
3. 設計新 ingest 功能前 grep 本檔，確認沒踩既有 drift / 落點正確

