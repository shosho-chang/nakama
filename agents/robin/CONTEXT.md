# Robin — Knowledge Base ingest + KB read

Robin 吸收 source（article / paper / book / podcast）→ 抽 concept / entity → 寫 wiki page。

## Language

### Reader-side bilingual (PR #354)

**Bilingual sibling**:
Reader「翻譯」按鈕產出 `Inbox/kb/{slug}-bilingual.md`，含中英對照段（英文段落後接 `>` blockquote 中文譯文）。**原 `{slug}.md` 不變**，bilingual 是 sidecar，frontmatter 帶 `derived_from`。修修 annotate 落 bilingual 上。翻譯走自家 translator（`shared/translator.py` Claude Sonnet + 台灣繁中 glossary）。
_Avoid_: 翻譯檔, translated md（用 bilingual sibling）

**Bilingual reader**:
`reader.html` 雙語 toggle — 一份 MD 含中英對照段，UI 切換顯示譯文（`譯文 ✓` / `譯文 ✗`）。**不是兩個檔**。
_Avoid_: 雙語 reader pair（用 bilingual reader）

## Inbox sibling collapse

當 `{stem}.md` + `{stem}-bilingual.md` 同時存在時，inbox 列表只顯示 bilingual 那一筆（user-facing 的閱讀 / annotate target），原 `{stem}.md` hide 起來但保留作再翻譯來源。實作在 `thousand_sunny.routers.robin._get_inbox_files`。
