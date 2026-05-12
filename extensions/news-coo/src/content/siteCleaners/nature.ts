// Nature.com family cleaner.
//
// DOM observed 2026-05-11 on /articles/s43587-024-00692-2 (Nature Aging):
//
//   Body markers:
//     <sup><a href="/articles/.../#ref-CR1"
//             data-test="citation-ref"
//             data-track-action="reference anchor"
//             id="ref-link-section-...">1</a></sup>
//
//   Bottom list (inside <ol class="c-article-references">):
//     <li class="c-article-references__item" data-counter="1.">
//       <p class="c-article-references__text" id="ref-CR1">…citation…</p>
//       <p class="c-article-references__links">
//         <a data-track-action="article reference"
//            href="https://doi.org/10.1038/...">Article</a>
//         …
//       </p>
//     </li>
//
//   Figure pill button (inside <figure>):
//     <div class="c-article-section__figure-link">
//       <a class="c-article__pill-button"
//          href="/articles/.../figures/1">Full size image</a>
//     </div>
//
// We rewrite each in-text <sup> to point at the paper's DOI (when one is in the
// matching <li>'s links) and then neutralise the structural attributes Defuddle's
// footnote standardiser uses (`a[id^="ref-link"]`, `ol[class*="article-references"]`,
// `<li data-counter>`) so it doesn't replace our DOI hrefs with `#fn:N` internal
// anchors. Figure pill buttons are removed so the only image link in the markdown
// is the inline embed itself (which the vault image fetcher rewrites to a local
// `attachments/<slug>/img-N.<ext>` path — clicking opens the local file in Obsidian
// instead of nature.com).

import type { CleanReport, SiteCleaner } from "./types.js";
import { wireFigureBlockIds } from "./figureAnchors.js";

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

    // Rewrite each in-text citation anchor to the paper URL, then strip the
    // attributes Defuddle's footnote standardiser keys off (`a[id^="ref-link"]`,
    // `data-test="citation-ref"`). Without this, Defuddle replaces our DOI
    // hrefs with internal `#fn:N` anchors.
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
      a.removeAttribute("id");
      a.removeAttribute("data-test");
      a.removeAttribute("data-track-action");
      a.removeAttribute("data-track");
      a.removeAttribute("data-track-label");
      report.removedNodeCount++; // re-using as "rewritten count"
    }

    // Neutralise the bottom list so Defuddle doesn't treat it as a footnote
    // list (`ol[class*="article-references"]` + `<li data-counter>` would
    // trigger standardisation that rebuilds in-text refs into `#fn:N`).
    for (const item of items) {
      item.removeAttribute("data-counter");
      item.classList.remove("c-article-references__item");
      const textP = item.querySelector<HTMLElement>(
        "p.c-article-references__text[id]",
      );
      textP?.removeAttribute("id");
    }
    const refsLists = new Set<HTMLElement>();
    for (const item of items) {
      const parent = item.parentElement;
      if (parent && parent.tagName === "OL") refsLists.add(parent);
    }
    for (const ol of refsLists) {
      // Drop any class containing "article-references" (matches Defuddle's
      // `ol[class*="article-references"]` selector).
      for (const cls of Array.from(ol.classList)) {
        if (cls.includes("article-references")) ol.classList.remove(cls);
      }
    }

    // Drop figure pill buttons ("Full size image" links to nature.com). The
    // inline image embed survives — its src is rewritten to the local vault
    // path by the image fetcher, so clicking opens the local file.
    const pillButtons = Array.from(
      doc.querySelectorAll<HTMLElement>(".c-article-section__figure-link"),
    );
    for (const pill of pillButtons) {
      pill.remove();
      report.removedNodeCount++;
    }

    // Wire in-text figure references ("Fig. 3c" inline anchors) to local
    // Obsidian block IDs so clicking navigates within the saved markdown
    // instead of opening nature.com. Nature stores the figure id on an
    // inner <b id="FigN"> inside the <figcaption> rather than on <figure>
    // itself, so we hand the helper a custom extractor.
    wireFigureBlockIds(doc, {
      extractId: (fig) => {
        const figcaption = fig.querySelector<HTMLElement>("figcaption");
        const label = figcaption?.querySelector<HTMLElement>('[id^="Fig"]');
        const id = label?.getAttribute("id") ?? "";
        return /^Fig\d+$/.test(id) ? id : null;
      },
    });

    // Promote thumbnail figure URLs to full resolution. Nature serves
    // `media.springernature.com/lw685/.../FigN_HTML.png` (685px wide) inline,
    // with the full-resolution image at `/full/.../FigN_HTML.png`. We rewrite
    // both src and srcset so the image fetcher downloads the full-size asset.
    const figureImgs = Array.from(
      doc.querySelectorAll<HTMLElement>(
        "figure img, picture img, picture source",
      ),
    );
    for (const node of figureImgs) {
      for (const attr of ["src", "srcset"] as const) {
        const v = node.getAttribute(attr);
        if (!v) continue;
        if (!/media\.springernature\.com\//.test(v)) continue;
        const promoted = v.replace(
          /media\.springernature\.com\/lw\d+\//g,
          "media.springernature.com/full/",
        );
        if (promoted !== v) node.setAttribute(attr, promoted);
      }
    }

    return report;
  },
};
