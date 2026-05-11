---
type: decision
visibility: shared
agent: shared
confidence: high
created: 2026-05-09
expires: permanent
tags: [reader, toast, obsidian-clipper, kb-ingest]
name_zh: Web 閱讀匯入必須抽取主內容
name_en: Web reading import must extract main content
description_zh: Toast 是翻譯與匯入主控，但應借用 Obsidian Clipper/Defuddle 的正文抽取與格式化能力，排除側欄、導覽、廣告與頁面雜訊。
description_en: Toast is the translation and import controller, but it should borrow Obsidian Clipper/Defuddle extraction and formatting so imports exclude sidebars, navigation, ads, and page noise.
---

# Web Reading Import Main-Content Boundary

For the personal reading pipeline, Toast should remain the primary browser extension for translation and one-click import. However, import quality depends on capturing only the article/document body.

Therefore, the Toast -> Nakama import bridge must reuse or adapt Obsidian Clipper/Defuddle-style extraction and Markdown formatting for:

- identifying the main readable content;
- excluding sidebars, navigation, ads, buttons, forms, and other page chrome;
- preserving useful metadata such as title, author, site, published date, language, word count, and canonical URL when available;
- producing clean original and bilingual/display tracks for Reader and later KB integration.

Do not treat raw rendered DOM capture as sufficient for KB-bound reading imports.
