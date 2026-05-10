# News Coo

Browser extension that extracts the main content of a web page (no nav, no
sidebar, no ads), converts it to clean Markdown, downloads any embedded
images, and writes the result directly into the Nakama Obsidian vault under
`Inbox/kb/`.

It is a thin delivery extension — translation, bilingual rendering, and
Reader integration are handled downstream by the Robin agent on the Nakama
backend.

## Status

S1 skeleton. Buildable, lintable, testable. No real extraction yet.

## Stack

- Chrome MV3
- TypeScript (strict) + Rolldown
- Vitest + happy-dom
- ESLint (typescript-eslint recommended-type-checked)
- [Defuddle](https://github.com/kepano/defuddle) for extraction (npm dep)
- File System Access API for vault writes

## Local development

```bash
pnpm install
pnpm build       # → dist/
pnpm test        # vitest
pnpm check       # tsc --noEmit
pnpm lint        # eslint
```

Load `dist/` as an unpacked extension in `chrome://extensions`.

## Implementation slices

1. **S1 skeleton** ← we are here
2. S2 extraction (Defuddle wrap + content-script wiring + PubMed detector)
3. S3 FSA writer (vault picker, frontmatter, slug, dedup)
4. S4 image fetcher (mirrors `shared/image_fetcher.py` conventions)
5. S5 UX surfaces (popup preview, quick mode, context menu, kbd shortcut)
6. S6 selection-aware clipping + highlights seed
7. S7 polish (i18n, error states, CORS-fallback, test coverage)

See `docs/` and the Nakama-side decision memory
`memory/claude/project_news_coo_grill_decisions.md` for details.

## License

MIT. See `LICENSE` and `NOTICE` for attribution to upstream projects.
