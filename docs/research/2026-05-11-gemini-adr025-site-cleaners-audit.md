### 1. Code grounding

The ADR correctly identifies the location for the new registry (`extensions/news-coo/src/content/siteCleaners/`) and its primary insertion point within the content script's lifecycle: before the main extraction library, Defuddle, is invoked. However, the current `extractor.ts` flow is more nuanced than the ADR's `runs cleaners → defuddle → return` summary implies. A review of `E:/nakama-p0/extensions/news-coo/src/content/extractor.ts` reveals the following pipeline:

1.  **`prepareDocument(document)`:** A preliminary DOM mutation pass that already exists. It normalizes image `src` attributes to be absolute, removes `noscript` tags, and unwraps certain container elements. **The proposed cleaner registry should run inside this function, after the existing normalizations but before the final `document.body.innerHTML` is returned for extraction.** The ADR omits this existing preparation step, making it seem like a completely new concept.
2.  **`extractMainContent(html, url, options)`:** This function receives the prepared HTML string. It instantiates Defuddle (`new Defuddler(doc)`), runs `.extract()`, and then performs significant post-processing on the Defuddle output (`{title, content, author, ...}`).
3.  **Metadata Enrichment:** After extraction, there's logic to pull `og:image`, `canonical` link, and other metadata from the *original, unclean* document's `<head>`. This is a critical detail. If a cleaner aggressively removes parts of the DOM, it must be careful not to touch the `<head>` element, as downstream metadata extraction depends on it.

The proposed `clean(document)` mutator contract fits well, but the ADR needs to be more precise about its integration point. It should explicitly state that the registry is called within `prepareDocument` and that cleaners **must not** modify the document's `<head>`.

### 2. Drift detection

The slice plan in the ADR is directionally correct but misses key implementation details that will cause drift from the estimate.

*   **Slice 1 (Infrastructure):**
    *   **Missing:** The `index.ts` in `siteCleaners/` needs more than just dispatch logic. It needs a clear interface definition (`SiteCleaner { predicate: (host: string) => boolean; clean: (doc: Document) => void; }`) and a mechanism to import and register all cleaner modules. A simple array `const cleaners = [lancetCleaner, nejmCleaner, ...]` is likely sufficient.
    *   **Missing:** The ADR implies `extractor.ts` calls the registry. The actual change is within `prepareDocument.ts` (or a function it calls). The call site needs to be updated.
    *   **Missing:** The WebExtension manifest (`manifest.json`) and build configuration (`webpack.config.js` or similar) will need to be updated to ensure the new `siteCleaners/*.ts` files are correctly bundled into the content script. This is a non-trivial step that is completely omitted.

*   **Slice 2 (Lancet Cleaner):**
    *   **Correct:** The DoD ("Hantavirus article captured → 0 inline reference fragments") is good.
    *   **Missing:** The test strategy requires a new fixture. The process for adding and maintaining this fixture (e.g., sanitizing it to remove personal data, ensuring it's stored efficiently) isn't specified. It also doesn't mention testing the *negative case*—a non-Lancet page that should *not* be modified.

*   **Slice 3 (Documentation):**
    *   **Correct:** The plan to create `docs/news-coo/site-cleaners.md` is sound.
    *   **Missing:** The ADR's "how to add a cleaner in 15 min" promise is optimistic. The documentation must also include how to update the build system, how to add a test fixture, and the "rules of engagement" (e.g., "do not touch `<head>`," "prefer specific selectors over broad ones").

The current plan under-scopes the work by omitting build system changes and the full testing and documentation requirements, leading to likely schedule drift.

### 3. Multilingual / multimodal lens

The ADR's perspective is overwhelmingly Anglophone and Western-centric. The chosen examples (Lancet, NEJM, Nature) reinforce this bias. This is a major blind spot for a tool intended for global use.

*   **CJK Publisher Blind Spot:** Major academic portals in Chinese, Japanese, and Korean (e.g., **CNKI (知网)**, **J-STAGE**, **KISS**) use different DOM structures and citation patterns.
    *   **CNKI** often uses `<iframe>` elements to embed article content, and citation markers might be linked to JavaScript functions (`onclick="showRef(..)"`) rather than CSS-driven hover blocks. A DOM-only cleaner that just removes elements might fail entirely; it might need to disable or rewrite inline scripts.
    *   **J-STAGE** articles frequently mix English and Japanese. The selectors will need to be robust to this. Furthermore, citation styles might not use superscript numbers but rather bracketed text like `[文献1]`. The underlying assumption of `<sup>` tags is flawed.
    *   The ADR's `host.endsWith(...)` predicate is too simple. A single publisher like ScienceDirect has regional domains (`sciencedirect.com`, `elsevier.com/zh-cn`). The predicate logic will need to be more sophisticated, potentially using regex or a list of domains per cleaner.

*   **Right-to-Left (RTL) Languages:** For academic journals in Hebrew or Arabic, content flow and DOM structure can be inverted. While CSS handles most visual rendering, selectors relying on element order (`:first-child`, `:nth-of-type`) can become brittle. Cleaners must use class- or attribute-based selectors that are independent of document flow. The current proposal doesn't account for this.

*   **Multimodal Failures:** The ADR focuses on HTML citations. A significant portion of non-Western academic content, especially older archives, is delivered as **scanned PDFs rendered in a web view**. In these cases, the "text" is an image, and citations might be image fragments. The proposed cleaner has no way to handle this. It also fails to consider that some "citation widgets" are rendered on a `<canvas>` element, which is opaque to DOM manipulation. While out of scope for a simple DOM cleaner, acknowledging these modalities is crucial for understanding the solution's limitations.

*   **Character Encoding:** While modern browsers handle UTF-8 well, a cleaner that mutates the DOM must ensure it doesn't corrupt character encoding, especially when dealing with complex CJK or RTL scripts. This is a low-level risk but one that exists.

The proposed solution, while functional for *The Lancet*, is based on a narrow set of assumptions that will not scale globally without significant adaptation.

### 4. Assumption push-back

1.  **Assumption:** "The long tail of academic publishers actually concentrate on ~10 hosts."
    *   **Challenge:** This is a dangerous assumption based on a Western-centric view of top-tier journals. While the *impact factor* may concentrate, the sheer *volume* of research published is distributed across thousands of university presses, regional consortia (SciELO in Latin America), and national platforms (CNKI in China). The "long tail" is likely fatter and longer than anticipated. The maintenance burden could easily exceed the "30+ cleaners" figure dismissed in the open questions.

2.  **Assumption:** "A broken cleaner can return a no-op and the page still extracts... Failure mode is graceful."
    *   **Challenge:** This definition of "graceful" is too narrow. If a publisher redesigns their site, a cleaner's selectors will fail to match. The system will revert to the broken, unreadable output. From the user's perspective, the feature *regressed* without warning. This is not a graceful failure; it's a silent, frustrating one. A truly graceful failure requires detection and user notification (as hinted at in "Open Question 4"). The ADR should treat this as a requirement, not a question.

3.  **Assumption:** "Defuddle is a main-content extractor, not citation-aware."
    *   **Challenge:** This is true, but it frames the problem as Defuddle's deficiency and the solution as a patch *around* it. The more fundamental question is whether a generic content extractor is the right tool for a specialized domain (academic papers). The decision to patch Defuddle instead of replacing it (Option C) is presented as a pragmatic choice to save test-fixture work. But this choice creates a new, ongoing maintenance burden (the cleaner registry). This ADR is essentially committing to technical debt to avoid a one-time migration cost. Is the trade-off worth it in the long run? The ADR doesn't fully grapple with the total cost of ownership of the cleaner registry.

4.  **Assumption:** "Subsequent sites added one PR at a time as we encounter them."
    *   **Challenge:** This reactive "step-on-it / fix-it" model optimizes for developer convenience, not user experience. A user capturing from a new but popular academic site (e.g., IEEE Xplore) will have a broken experience and must file a bug report, wait for a developer to prioritize it, and then wait for a new extension release. A more proactive approach, perhaps analyzing user-submitted failed captures to identify the most common broken sites, would be more user-centric.

### 5. Alternatives Claude didn't consider

The ADR considers replacing Defuddle or enhancing the current proposal. These are good, but several other angles were missed.

1.  **Leverage Native Browser APIs (Reader Mode):** Instead of reimplementing a reader view, why not invoke the browser's own? Both Chrome (`dom.distiller.ReaderArticleFinder`) and Firefox have internal, non-standardized APIs for their reader modes. While using them is a hack that could break, it's worth investigating if a message-passing approach from the content script to the background script could trigger a "read-only" version of the page's content, which could then be extracted. Mozilla's Readability.js is the open-source version of this, but the browser's *native* implementation is often more robust and up-to-date.

2.  **Contribute Upstream to Defuddle:** The issue is that Defuddle doesn't know how to handle academic citation patterns. Instead of building a bespoke, downstream patch registry, a more sustainable solution would be to add a "pluggable rules" or "heuristic set" system to Defuddle itself. We could contribute a ruleset specifically for academic sites (e.g., `defuddle.extract({rules: 'academic'})`). This improves the core tool for everyone (including Obsidian users) and centralizes the maintenance effort.

3.  **Use CSS `print` Stylesheets as a Heuristic:** Many academic sites have a `media="print"` stylesheet that dramatically simplifies the page for printing. This is a powerful signal for identifying core content and removing interactive cruft like citation pop-ups. Before running Defuddle, the content script could programmatically apply the print styles to the DOM (`@media print`), which might remove the problematic elements automatically. This is a generic heuristic that could fix many sites at once without per-site cleaners.

4.  **Structured Data Parsing (JSON-LD, Microdata):** Many modern publishers embed structured data (`<script type="application/ld+json">`, Microdata, RDFa) describing the article, author, and even citations. This is a goldmine of clean, semantic information. The extractor could prioritize this structured data. If it finds a `ScholarlyArticle` object with a `citation` property, it could use that directly, sidestepping DOM parsing for that information entirely. This is more robust than selector-based scraping.

### 6. Verdict

**Adopt with major modifications.**

The core problem is real and the proposed solution of a pre-extraction cleaning pass is a valid, incremental approach. However, the ADR's current form is too narrowly focused on a single English-language publisher and underestimates both the technical implementation details and the long-term maintenance implications of its "long tail" strategy.

**Top 3 required changes:**

1.  **Rearchitect for Proactive Heuristics First, Reactive Cleaners Second:** Before building the per-site cleaner registry, implement a more generic, powerful pre-processing step. **The top priority should be to investigate using the site's `print` stylesheet (Alternative #3)**. This single change has the potential to fix a whole class of sites without requiring a registry. The per-site registry should be the fallback for when this heuristic fails, not the primary solution.
2.  **Mandate a "Staleness Detection" Mechanism:** The "graceful failure" assumption must be rejected. Each cleaner module **must** also export a `verify(doc)` function that returns `true` if it successfully removed nodes. The central dispatcher will check this. If a cleaner matches a host but `verify` returns `false`, it means a site redesign has broken the selectors. This event must be logged to a monitoring service (Sentry, etc.) and a warning should be surfaced to the user ("This page structure has changed; capture quality may be reduced."). This transforms silent failures into actionable data.
3.  **Broaden the International Scope from Day One:** The initial infrastructure must be designed for multilingual realities.
    *   The `predicate` must support a list of hosts or a regex to handle regional domains (`['thelancet.com', 'sciencedirect.com', 'elsevier.com/zh-cn']`).
    *   The first non-Lancet cleaner added should be for a major CJK publisher (e.g., CNKI or J-STAGE) to prove the architecture isn't Anglocentric and to force the development of more robust patterns (e.g., handling `onclick` handlers, non-`<sup>` markers). This prevents the architecture from calcifying around simple Western DOM patterns.