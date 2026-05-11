globalThis.fetch;
extensions\news-coo\node_modules\defuddle\dist\extractors\_base.d.ts:14:    abstract canExtract(): boolean;
extensions\news-coo\node_modules\defuddle\dist\extractors\_base.d.ts:15:    abstract extract(): ExtractorResult;
extensions\news-coo\node_modules\defuddle\dist\extractors\_base.d.ts:16:    /**
extensions\news-coo\node_modules\defuddle\dist\extractors\_base.d.ts:17:     * Generate a title from the post 
description text, falling back to
extensions\news-coo\node_modules\defuddle\dist\extractors\_base.d.ts:18:     * "Post by {author}" if the description 
is empty.
extensions\news-coo\node_modules\defuddle\dist\extractors\_base.d.ts:19:     */
extensions\news-coo\node_modules\defuddle\dist\extractors\_base.d.ts:20:    protected postTitle(author: string, site: 
string): string;
extensions\news-coo\node_modules\defuddle\dist\extractors\_base.d.ts:21:    canExtractAsync(): boolean;
extensions\news-coo\node_modules\defuddle\dist\extractors\_base.d.ts:22:    /**
extensions\news-coo\node_modules\defuddle\dist\extractors\_base.d.ts:23:     * When true, parseAsync() will prefer 
extractAsync() over extract(),
extensions\news-coo\node_modules\defuddle\dist\extractors\_base.d.ts:24:     * even if sync extraction produces 
content. Use this when the async
extensions\news-coo\node_modules\defuddle\dist\extractors\_base.d.ts:25:     * path provides strictly better results 
(e.g. YouTube transcripts).
extensions\news-coo\node_modules\defuddle\dist\extractors\_base.d.ts:26:     */
extensions\news-coo\node_modules\defuddle\dist\extractors\_base.d.ts:27:    prefersAsync(): boolean;
extensions\news-coo\node_modules\defuddle\dist\extractors\_base.d.ts:28:    extractAsync(): Promise<ExtractorResult>;
extensions\news-coo\node_modules\defuddle\dist\extractors\_base.d.ts:29:}


Cannot set property. Property setting is supported only on core types in this language mode.
At line:1 char:1
+ [Console]::OutputEncoding=[System.Text.Encoding]::UTF8;
+ ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    + CategoryInfo          : InvalidOperation: (:) [], RuntimeException
    + FullyQualifiedErrorId : PropertySetterNotSupportedInConstrainedLanguage
 

web search: 
web search: site:developer.chrome.com/docs/extensions Chrome extensions reader mode API readerMode
exec
"C:\\WINDOWS\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" -NoProfile -Command "Test-Path .\\docs\\research\\2026-05-11-codex-adr025-site-cleaners-audit.md" in E:\nakama-p0
 succeeded in 499ms:
False
Cannot set property. Property setting is supported only on core types in this language mode.
At line:1 char:1
+ [Console]::OutputEncoding=[System.Text.Encoding]::UTF8;
+ ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    + CategoryInfo          : InvalidOperation: (:) [], RuntimeException
    + FullyQualifiedErrorId : PropertySetterNotSupportedInConstrainedLanguage
 

  ✓ Read ADR and News Coo content extractor flow
  ✓ Check call sites, PRD, Robin, and Defuddle hooks
  ✓ Verify numerical claims against captured output
  → Draft audit and attempt save
codex
I have enough evidence to call the ADR direction viable but not signable as written. I’m going to try to create the requested audit file now; if the read-only sandbox blocks the write, I’ll still print the exact audit text here.
2026-05-11T05:10:48.284175Z ERROR codex_core::tools::router: error=patch rejected: writing is blocked by read-only sandbox; rejected by user approval settings
Write attempt to docs/research/2026-05-11-codex-adr025-site-cleaners-audit.md was rejected by the read-only sandbox, so the audit is provided in full in the final response.
  ✓ Read ADR and News Coo content extractor flow
  ✓ Check call sites, PRD, Robin, and Defuddle hooks
  ✓ Verify numerical claims against captured output
  ✓ Draft audit and attempt save
codex
I could not save the file: the write attempt to `docs/research/2026-05-11-codex-adr025-site-cleaners-audit.md` was rejected by the read-only sandbox. Full audit follows.

# ADR-025 Site Cleaners Audit

## 1. Code Grounding

The insertion point exists, but the ADR describes it too loosely. The current chokepoint is `extractPage(doc, url, selectionHtml?)` in `extensions/news-coo/src/content/extract.ts:31`. That function chooses `targetDoc = doc` by default, creates a synthetic document only for selection clipping at `extensions/news-coo/src/content/extract.ts:36-41`, then immediately calls `new Defuddle(targetDoc, { url, separateMarkdown: true }).parse()` at `extensions/news-coo/src/content/extract.ts:43`. It collects images from Defuddle's returned HTML at `extensions/news-coo/src/content/extract.ts:44` and maps Defuddle metadata into `ExtractedPage` at `extensions/news-coo/src/content/extract.ts:45-57`.

So registry dispatch would land after `targetDoc` is chosen and before line 43. It should not be in popup/background code. It should be a content-extractor concern:

```ts
targetDoc = buildTargetDocument(...);
const cleanReport = runSiteCleaners(targetDoc, url);
const result = new Defuddle(targetDoc, { url, separateMarkdown: true }).parse();
```

The missing design detail is document ownership. For full-page clipping, `targetDoc` is currently the live page document (`extensions/news-coo/src/content/extract.ts:36`). A cleaner that mutates `document` directly would remove visible DOM nodes from the user's active tab. Defuddle itself already mutates the original document in some cases: it normalizes attributes and resolves noscript images before cloning (`extensions/news-coo/node_modules/defuddle/dist/defuddle.js:53-55`), later strips unsafe elements from `this.doc` (`extensions/news-coo/node_modules/defuddle/dist/defuddle.js:181-201`), and only clones for the main extraction pass at `extensions/news-coo/node_modules/defuddle/dist/defuddle.js:706`. Adding an aggressive Lancet cleaner on top of that makes live-page mutation more visible and should be avoided by cloning before cleaner dispatch.

For selection clipping, the synthetic target document has only the selected HTML fragment (`extensions/news-coo/src/content/extract.ts:37-41`). That means cleaner dispatch must accept the URL/hostname explicitly; it cannot infer host from `targetDoc.location` reliably. It also means tests need to cover both full-document and selection-fragment extraction.

## 2. Drift Detection

The ADR's "default behaviour when no cleaner matches: pass DOM through unchanged (current behaviour)" is only partly true. It is true if "unchanged" means "the new cleaner registry does nothing before Defuddle." It is false if read literally: current extraction does not preserve the DOM untouched. The full-page path passes the live document into Defuddle (`extensions/news-coo/src/content/extract.ts:36,43`), and Defuddle mutates its input as noted above. The cleaner registry should define its own no-op guarantee: no matching cleaner means no additional pre-Defuddle mutations.

There is one practical extraction chokepoint. The content script listens for `MSG_EXTRACT` at `extensions/news-coo/src/content/contentScript.ts:13-17`, serializes the active selection at `extensions/news-coo/src/content/contentScript.ts:18-28`, calls `extractPage(document, location.href, selectionHtml)` at `extensions/news-coo/src/content/contentScript.ts:30`, then adds PubMed metadata if applicable at `extensions/news-coo/src/content/contentScript.ts:31-32`.

Popup and quick clip do not need their own cleaner invocation. Popup calls `deps.sendExtract(tabId)` at `extensions/news-coo/src/popup/popup.ts:104-107`; the real dependency is `chrome.tabs.sendMessage(tabId, { type: MSG_EXTRACT })` at `extensions/news-coo/src/popup/popup.ts:232-233`. Quick clip's default sender is the same message at `extensions/news-coo/src/background/quickClip.ts:21-22`, and quick clip consumes the returned page at `extensions/news-coo/src/background/quickClip.ts:52-75`. Context menu and keyboard commands only call `quickClip(tab.id)` (`extensions/news-coo/src/background/serviceWorker.ts:21-33`). Therefore the cleaner pass belongs in `extractPage`; putting it anywhere else risks duplicated behavior or missed surfaces.

## 3. Numerical / Factual Claims Push-Back

The "256 such injections" claim checks out for the exact metric stated in the prompt. I counted lines matching `^\d+\.$` in `E:/Shosho LifeOS/hantavirus-in-humans-a-review-of-clinical-aspects-and-management.md`; the count is 256. The captured file also reports `word_count: 12689` at `E:/Shosho LifeOS/hantavirus-in-humans-a-review-of-clinical-aspects-and-management.md:14`, so the ADR's "12.7k-word article" claim is grounded.

That said, `^\d+\.$` is a proxy, not proof of "256 reference bodies." The MHTML snapshot had 95 lines matching the literal string `dropBlock reference-citations` and 107 lines matching `reference-citations__ctrl`. Because MHTML is quoted-printable and line-wrapped, those are not reliable node counts either. The right fixture test should parse the HTML/MHTML into a DOM and count actual `.dropBlock.reference-citations` nodes, then compare Markdown before/after.

The "3-5x cost" for Option B is not verifiable from the ADR or repo. It may be directionally plausible, but it is presented as an estimate without basis: no per-site implementation sketch, no fixture count, no previous cleaner timing, no test delta. If it remains in the ADR, label it explicitly as an uncalibrated estimate or replace it with a smaller claim: "higher and more variable cost because marker/list mapping differs by publisher."

The "long tail concentrates on ~10 hosts" is plausible but under-supported. Robin already encodes 12 academic publisher domains (`thelancet.com`, `bmj.com`, `nature.com`, `cell.com`, `nejm.org`, `science.org`, `plos.org`, `link.springer.com`, `onlinelibrary.wiley.com`, `jamanetwork.com`, `academic.oup.com`, `sciencedirect.com`) at `agents/robin/url_dispatcher.py:81-96`, and its helper treats subdomains as matches at `agents/robin/url_dispatcher.py:122-124`. The ADR's list overlaps that set but omits several high-volume publishers/platforms. The concentration claim should be backed by local vault host frequency or clipped-source history, not intuition.

## 4. Assumption Push-Back

First, "Defuddle is not citation-aware" is overstated. Defuddle's README says it provides consistent output for footnotes (`extensions/news-coo/node_modules/defuddle/README.md:15`), documents a `standardize` option for footnotes (`extensions/news-coo/node_modules/defuddle/README.md:183`), and explicitly says inline references and footnotes are converted to a standard format (`extensions/news-coo/node_modules/defuddle/README.md:211-213`). Its source also runs `adoptExternalFootnotes` and `standardizeFootnotes` during parsing (`extensions/news-coo/node_modules/defuddle/dist/defuddle.js:772-780`). The more accurate claim is: Defuddle's existing footnote/citation handling does not recognize Lancet's inline hover/click reference drop-blocks.

Second, "graceful degradation" is not guaranteed by the current News Coo code. If a cleaner throws inside `extractPage`, the content script's broad `try/catch` will return `{ ok: false, error }` at `extensions/news-coo/src/content/contentScript.ts:35-36`; popup/quick mode then show extraction failure. A broken cleaner is graceful only if the registry catches per-cleaner exceptions and continues to Defuddle, ideally returning warnings/removed counts.

Third, "0 risk to non-matching sites" is too narrow a risk statement. It ignores matching-site blast radius. A host-wide Lancet cleaner can delete the only available reference detail if the page does not expose a separate bibliography in the selected main content. Removing the drop-blocks solves reading flow but may degrade evidence quality by leaving citation markers without bibliography. The ADR's Phase 2 footnote deferral needs to acknowledge this data loss explicitly.

Fourth, the cleaner contract is underspecified. It needs to say whether cleaners mutate a clone or the live document, whether they run on selection fragments, whether they receive URL/hostname separately, whether exceptions are swallowed, whether stats are returned, and how stale selectors are detected. The ADR asks this as an open question, but it is not optional for signoff.

Fifth, "one selector list" is too optimistic. The initial Lancet cleaner may be one selector, but reliable behavior needs guards: host predicate, selector specificity, fixture assertions that article text remains intact, negative assertions around author/institution popups, and a stale-cleaner signal when zero nodes are removed on a known matching host.

## 5. Alternatives Claude Didn't Seriously Consider

Defuddle has relevant knobs and internals. Public options include `removeExactSelectors`, `removePartialSelectors`, `standardize`, and `contentSelector` (`extensions/news-coo/node_modules/defuddle/dist/types.d.ts:65-110`; README summary at `extensions/news-coo/node_modules/defuddle/README.md:177-184`). Those do not directly provide "remove this custom selector before extraction," but the ADR should still explain why local pre-cleaning is chosen over a Defuddle upstream issue/patch. Defuddle also has an internal extractor registry with `register()` (`extensions/news-coo/node_modules/defuddle/dist/extractor-registry.js:170`) and domain matching (`extensions/news-coo/node_modules/defuddle/dist/extractor-registry.js:182-190`), but package exports expose only `.`, `./full`, and `./node` (`extensions/news-coo/node_modules/defuddle/package.json:24-38`), and `defuddle/full` only exports the wrapper class plus Markdown helper (`extensions/news-coo/node_modules/defuddle/dist/index.full.d.ts:1-12`). Deep-importing internals would be brittle; upstreaming may be cleaner if this affects Obsidian Web Clipper too.

Post-Defuddle Markdown cleanup is also an unexamined middle ground. It is dangerous if done by broad regex because real numbered lists and references can match `^\d+\.$`, but a conservative cleanup keyed to repeated Crossref/PubMed/Google Scholar blocks might be useful as a diagnostic or fallback. The ADR jumps from DOM cleaning to full footnote rewriting without evaluating this smaller post-processing option.

Browser reader mode is not a serious Chrome MV3 alternative today unless a specific API is identified. The current official Chrome extension API reference lists extension APIs broadly, and `declarativeContent` can match URLs/selectors without reading page content, but it is not a reader-mode extraction API. I did not find a public Chrome extension reader-mode API in the official reference search. 

Robin is related, but this should not be dumped onto Robin by default. The PRD says News Coo's job is to write a clean original/evidence track into `Inbox/kb/` (`docs/prds/2026-05-10-news-coo-inbox-importer.md:31-32,48`) and explicitly says News Coo owns extraction while Robin owns translation/bilingual output (`docs/prds/2026-05-10-news-coo-inbox-importer.md:38,57,80,90-91,294-296`). If the saved original is structurally polluted, Robin translating it later preserves the pollution. However, Robin already has academic URL routing and publisher-domain detection for URL-based ingest (`agents/robin/url_dispatcher.py:25-33,81-96`). The ADR should draw the boundary: News Coo cleaner fixes browser-captured Markdown; Robin academic ingest may be a separate path for DOI/PubMed/OA retrieval, not a cleanup bandage for broken client extraction.

## 6. Verdict

Adopt with mods. The core idea fits the current architecture because `extractPage` is a real chokepoint and all UX surfaces flow through it. I would not sign the ADR as-is because it handwaves document mutation, overclaims Defuddle's lack of citation awareness, and presents estimates/long-tail claims without evidence.

Top 3 required changes before signoff:

1. Specify the implementation contract: clone full-page documents before cleaning; run cleaners only inside `extractPage` after selection target creation and before Defuddle; pass `(doc, url)`; catch cleaner exceptions per cleaner; return `{ matched, removedNodeCount, warnings }` or equivalent.
2. Tighten the Lancet proof: parse the captured snapshot into a fixture, count actual `.dropBlock.reference-citations` nodes, assert the Markdown no longer contains inline Crossref/PubMed/Google Scholar reference fragments, and assert title/summary/headings/images still survive. Include tests for no-match no-op, stale selector, and throwing cleaner.
3. Rewrite the claims/alternatives section: say Defuddle is citation-aware but misses Lancet drop-blocks; mark "3-5x" and "~10 hosts" as estimates unless backed by data; explicitly address bibliography loss, upstream Defuddle, and Robin boundary.
tokens used
466,422
I could not save the file: the write attempt to `docs/research/2026-05-11-codex-adr025-site-cleaners-audit.md` was rejected by the read-only sandbox. Full audit follows.

# ADR-025 Site Cleaners Audit

## 1. Code Grounding

The insertion point exists, but the ADR describes it too loosely. The current chokepoint is `extractPage(doc, url, selectionHtml?)` in `extensions/news-coo/src/content/extract.ts:31`. That function chooses `targetDoc = doc` by default, creates a synthetic document only for selection clipping at `extensions/news-coo/src/content/extract.ts:36-41`, then immediately calls `new Defuddle(targetDoc, { url, separateMarkdown: true }).parse()` at `extensions/news-coo/src/content/extract.ts:43`. It collects images from Defuddle's returned HTML at `extensions/news-coo/src/content/extract.ts:44` and maps Defuddle metadata into `ExtractedPage` at `extensions/news-coo/src/content/extract.ts:45-57`.

So registry dispatch would land after `targetDoc` is chosen and before line 43. It should not be in popup/background code. It should be a content-extractor concern:

```ts
targetDoc = buildTargetDocument(...);
const cleanReport = runSiteCleaners(targetDoc, url);
const result = new Defuddle(targetDoc, { url, separateMarkdown: true }).parse();
```

The missing design detail is document ownership. For full-page clipping, `targetDoc` is currently the live page document (`extensions/news-coo/src/content/extract.ts:36`). A cleaner that mutates `document` directly would remove visible DOM nodes from the user's active tab. Defuddle itself already mutates the original document in some cases: it normalizes attributes and resolves noscript images before cloning (`extensions/news-coo/node_modules/defuddle/dist/defuddle.js:53-55`), later strips unsafe elements from `this.doc` (`extensions/news-coo/node_modules/defuddle/dist/defuddle.js:181-201`), and only clones for the main extraction pass at `extensions/news-coo/node_modules/defuddle/dist/defuddle.js:706`. Adding an aggressive Lancet cleaner on top of that makes live-page mutation more visible and should be avoided by cloning before cleaner dispatch.

For selection clipping, the synthetic target document has only the selected HTML fragment (`extensions/news-coo/src/content/extract.ts:37-41`). That means cleaner dispatch must accept the URL/hostname explicitly; it cannot infer host from `targetDoc.location` reliably. It also means tests need to cover both full-document and selection-fragment extraction.

## 2. Drift Detection

The ADR's "default behaviour when no cleaner matches: pass DOM through unchanged (current behaviour)" is only partly true. It is true if "unchanged" means "the new cleaner registry does nothing before Defuddle." It is false if read literally: current extraction does not preserve the DOM untouched. The full-page path passes the live document into Defuddle (`extensions/news-coo/src/content/extract.ts:36,43`), and Defuddle mutates its input as noted above. The cleaner registry should define its own no-op guarantee: no matching cleaner means no additional pre-Defuddle mutations.

There is one practical extraction chokepoint. The content script listens for `MSG_EXTRACT` at `extensions/news-coo/src/content/contentScript.ts:13-17`, serializes the active selection at `extensions/news-coo/src/content/contentScript.ts:18-28`, calls `extractPage(document, location.href, selectionHtml)` at `extensions/news-coo/src/content/contentScript.ts:30`, then adds PubMed metadata if applicable at `extensions/news-coo/src/content/contentScript.ts:31-32`.

Popup and quick clip do not need their own cleaner invocation. Popup calls `deps.sendExtract(tabId)` at `extensions/news-coo/src/popup/popup.ts:104-107`; the real dependency is `chrome.tabs.sendMessage(tabId, { type: MSG_EXTRACT })` at `extensions/news-coo/src/popup/popup.ts:232-233`. Quick clip's default sender is the same message at `extensions/news-coo/src/background/quickClip.ts:21-22`, and quick clip consumes the returned page at `extensions/news-coo/src/background/quickClip.ts:52-75`. Context menu and keyboard commands only call `quickClip(tab.id)` (`extensions/news-coo/src/background/serviceWorker.ts:21-33`). Therefore the cleaner pass belongs in `extractPage`; putting it anywhere else risks duplicated behavior or missed surfaces.

## 3. Numerical / Factual Claims Push-Back

The "256 such injections" claim checks out for the exact metric stated in the prompt. I counted lines matching `^\d+\.$` in `E:/Shosho LifeOS/hantavirus-in-humans-a-review-of-clinical-aspects-and-management.md`; the count is 256. The captured file also reports `word_count: 12689` at `E:/Shosho LifeOS/hantavirus-in-humans-a-review-of-clinical-aspects-and-management.md:14`, so the ADR's "12.7k-word article" claim is grounded.

That said, `^\d+\.$` is a proxy, not proof of "256 reference bodies." The MHTML snapshot had 95 lines matching the literal string `dropBlock reference-citations` and 107 lines matching `reference-citations__ctrl`. Because MHTML is quoted-printable and line-wrapped, those are not reliable node counts either. The right fixture test should parse the HTML/MHTML into a DOM and count actual `.dropBlock.reference-citations` nodes, then compare Markdown before/after.

The "3-5x cost" for Option B is not verifiable from the ADR or repo. It may be directionally plausible, but it is presented as an estimate without basis: no per-site implementation sketch, no fixture count, no previous cleaner timing, no test delta. If it remains in the ADR, label it explicitly as an uncalibrated estimate or replace it with a smaller claim: "higher and more variable cost because marker/list mapping differs by publisher."

The "long tail concentrates on ~10 hosts" is plausible but under-supported. Robin already encodes 12 academic publisher domains (`thelancet.com`, `bmj.com`, `nature.com`, `cell.com`, `nejm.org`, `science.org`, `plos.org`, `link.springer.com`, `onlinelibrary.wiley.com`, `jamanetwork.com`, `academic.oup.com`, `sciencedirect.com`) at `agents/robin/url_dispatcher.py:81-96`, and its helper treats subdomains as matches at `agents/robin/url_dispatcher.py:122-124`. The ADR's list overlaps that set but omits several high-volume publishers/platforms. The concentration claim should be backed by local vault host frequency or clipped-source history, not intuition.

## 4. Assumption Push-Back

First, "Defuddle is not citation-aware" is overstated. Defuddle's README says it provides consistent output for footnotes (`extensions/news-coo/node_modules/defuddle/README.md:15`), documents a `standardize` option for footnotes (`extensions/news-coo/node_modules/defuddle/README.md:183`), and explicitly says inline references and footnotes are converted to a standard format (`extensions/news-coo/node_modules/defuddle/README.md:211-213`). Its source also runs `adoptExternalFootnotes` and `standardizeFootnotes` during parsing (`extensions/news-coo/node_modules/defuddle/dist/defuddle.js:772-780`). The more accurate claim is: Defuddle's existing footnote/citation handling does not recognize Lancet's inline hover/click reference drop-blocks.

Second, "graceful degradation" is not guaranteed by the current News Coo code. If a cleaner throws inside `extractPage`, the content script's broad `try/catch` will return `{ ok: false, error }` at `extensions/news-coo/src/content/contentScript.ts:35-36`; popup/quick mode then show extraction failure. A broken cleaner is graceful only if the registry catches per-cleaner exceptions and continues to Defuddle, ideally returning warnings/removed counts.

Third, "0 risk to non-matching sites" is too narrow a risk statement. It ignores matching-site blast radius. A host-wide Lancet cleaner can delete the only available reference detail if the page does not expose a separate bibliography in the selected main content. Removing the drop-blocks solves reading flow but may degrade evidence quality by leaving citation markers without bibliography. The ADR's Phase 2 footnote deferral needs to acknowledge this data loss explicitly.

Fourth, the cleaner contract is underspecified. It needs to say whether cleaners mutate a clone or the live document, whether they run on selection fragments, whether they receive URL/hostname separately, whether exceptions are swallowed, whether stats are returned, and how stale selectors are detected. The ADR asks this as an open question, but it is not optional for signoff.

Fifth, "one selector list" is too optimistic. The initial Lancet cleaner may be one selector, but reliable behavior needs guards: host predicate, selector specificity, fixture assertions that article text remains intact, negative assertions around author/institution popups, and a stale-cleaner signal when zero nodes are removed on a known matching host.

## 5. Alternatives Claude Didn't Seriously Consider

Defuddle has relevant knobs and internals. Public options include `removeExactSelectors`, `removePartialSelectors`, `standardize`, and `contentSelector` (`extensions/news-coo/node_modules/defuddle/dist/types.d.ts:65-110`; README summary at `extensions/news-coo/node_modules/defuddle/README.md:177-184`). Those do not directly provide "remove this custom selector before extraction," but the ADR should still explain why local pre-cleaning is chosen over a Defuddle upstream issue/patch. Defuddle also has an internal extractor registry with `register()` (`extensions/news-coo/node_modules/defuddle/dist/extractor-registry.js:170`) and domain matching (`extensions/news-coo/node_modules/defuddle/dist/extractor-registry.js:182-190`), but package exports expose only `.`, `./full`, and `./node` (`extensions/news-coo/node_modules/defuddle/package.json:24-38`), and `defuddle/full` only exports the wrapper class plus Markdown helper (`extensions/news-coo/node_modules/defuddle/dist/index.full.d.ts:1-12`). Deep-importing internals would be brittle; upstreaming may be cleaner if this affects Obsidian Web Clipper too.

Post-Defuddle Markdown cleanup is also an unexamined middle ground. It is dangerous if done by broad regex because real numbered lists and references can match `^\d+\.$`, but a conservative cleanup keyed to repeated Crossref/PubMed/Google Scholar blocks might be useful as a diagnostic or fallback. The ADR jumps from DOM cleaning to full footnote rewriting without evaluating this smaller post-processing option.

Browser reader mode is not a serious Chrome MV3 alternative today unless a specific API is identified. The current official Chrome extension API reference lists extension APIs broadly, and `declarativeContent` can match URLs/selectors without reading page content, but it is not a reader-mode extraction API. I did not find a public Chrome extension reader-mode API in the official reference search. 

Robin is related, but this should not be dumped onto Robin by default. The PRD says News Coo's job is to write a clean original/evidence track into `Inbox/kb/` (`docs/prds/2026-05-10-news-coo-inbox-importer.md:31-32,48`) and explicitly says News Coo owns extraction while Robin owns translation/bilingual output (`docs/prds/2026-05-10-news-coo-inbox-importer.md:38,57,80,90-91,294-296`). If the saved original is structurally polluted, Robin translating it later preserves the pollution. However, Robin already has academic URL routing and publisher-domain detection for URL-based ingest (`agents/robin/url_dispatcher.py:25-33,81-96`). The ADR should draw the boundary: News Coo cleaner fixes browser-captured Markdown; Robin academic ingest may be a separate path for DOI/PubMed/OA retrieval, not a cleanup bandage for broken client extraction.

## 6. Verdict

Adopt with mods. The core idea fits the current architecture because `extractPage` is a real chokepoint and all UX surfaces flow through it. I would not sign the ADR as-is because it handwaves document mutation, overclaims Defuddle's lack of citation awareness, and presents estimates/long-tail claims without evidence.

Top 3 required changes before signoff:

1. Specify the implementation contract: clone full-page documents before cleaning; run cleaners only inside `extractPage` after selection target creation and before Defuddle; pass `(doc, url)`; catch cleaner exceptions per cleaner; return `{ matched, removedNodeCount, warnings }` or equivalent.
2. Tighten the Lancet proof: parse the captured snapshot into a fixture, count actual `.dropBlock.reference-citations` nodes, assert the Markdown no longer contains inline Crossref/PubMed/Google Scholar reference fragments, and assert title/summary/headings/images still survive. Include tests for no-match no-op, stale selector, and throwing cleaner.
3. Rewrite the claims/alternatives section: say Defuddle is citation-aware but misses Lancet drop-blocks; mark "3-5x" and "~10 hosts" as estimates unless backed by data; explicitly address bibliography loss, upstream Defuddle, and Robin boundary.
