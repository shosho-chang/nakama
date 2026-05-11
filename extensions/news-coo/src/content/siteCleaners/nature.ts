// Nature.com family cleaner.
//
// DOM observed 2026-05-11 on /articles/s43587-024-00692-2 (Nature Aging):
//
//   Body markers:
//     <sup><a href="/articles/.../#ref-CR1"
//             data-test="citation-ref"
//             data-track-action="reference anchor">1</a></sup>
//
//   Bottom list:
//     <li class="c-article-references__item" data-counter="1.">
//       <p class="c-article-references__text" id="ref-CR1">…citation…</p>
//       <p class="c-article-references__links">
//         <a data-track-action="article reference"
//            href="https://doi.org/10.1038/...">Article</a>
//         …
//       </p>
//     </li>
//
// Nature already ships semantic HTML; Defuddle handles the bibliography fine.
// All we do here is rewrite each in-text <sup> to point at the paper's DOI
// (when one is in the matching <li>'s links), so a single click opens the paper.

import type { CleanReport, SiteCleaner } from "./types.js";

const NATURE_HOSTS = ["nature.com", "www.nature.com"];

function pickPrimaryLink(item: Element): string | null {
  // Priority: Article (DOI), then PubMed/PMC, then any external https link.
  const article = item.querySelector<HTMLAnchorElement>(
    'a[data-track-action="article reference"][href^="https://doi.org"]',
  );
  if (article?.getAttribute("href")) return article.getAttribute("href");
  const pubmed = item.querySelector<HTMLAnchorElement>(
    'a[data-track-action="pubmed reference"][href^="http"]',
  );
  if (pubmed?.getAttribute("href")) return pubmed.getAttribute("href");
  const any = item.querySelector<HTMLAnchorElement>(
    '.c-article-references__links a[href^="http"]',
  );
  return any?.getAttribute("href") ?? null;
}

export const natureCleaner: SiteCleaner = {
  name: "nature",

  matches(host) {
    return NATURE_HOSTS.some((h) => host === h || host.endsWith(`.${h}`));
  },

  clean(doc) {
    const report: CleanReport = {
      matched: true,
      removedNodeCount: 0,
      warnings: [],
    };

    // Build fragment-id → DOI map from the bottom list.
    const fragToUrl = new Map<string, string>();
    const items = Array.from(
      doc.querySelectorAll<HTMLElement>("li.c-article-references__item"),
    );
    for (const item of items) {
      const textP = item.querySelector<HTMLElement>(
        "p.c-article-references__text[id]",
      );
      const id = textP?.getAttribute("id");
      if (!id) continue;
      const url = pickPrimaryLink(item);
      if (url) fragToUrl.set(id, url);
    }

    if (items.length === 0) {
      report.warnings.push("no li.c-article-references__item found");
    }

    // Rewrite each in-text citation anchor to the paper URL.
    const refAnchors = Array.from(
      doc.querySelectorAll<HTMLAnchorElement>(
        'a[data-test="citation-ref"], a[data-track-action="reference anchor"]',
      ),
    );
    for (const a of refAnchors) {
      const href = a.getAttribute("href") ?? "";
      const frag = href.split("#").pop() ?? "";
      const url = fragToUrl.get(frag);
      if (!url) continue;
      a.setAttribute("href", url);
      a.setAttribute("target", "_blank");
      a.setAttribute("rel", "noopener");
      report.removedNodeCount++; // re-using as "rewritten count"
    }

    return report;
  },
};
