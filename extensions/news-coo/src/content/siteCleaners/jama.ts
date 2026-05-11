// JAMA Network family cleaner.
//
// DOM observed 2026-05-11 on jamanetwork.com/journals/jama-health-forum/...
//
//   Body markers:
//     <sup><a class="ref-link section-jump-link"
//             href="#ajf240001r1">1</a></sup>
//
//   Bottom list:
//     <div class="references">
//       <div class="reference">
//         <a class="reference-number" id="ajf240001r1">1.</a>
//         <div class="reference-content">
//           Author. Title. <i>Journal</i>. 2023;330:1727.
//           doi:<a href="http://jamanetwork.com/article.aspx?doi=10.1001/jama.2023.19322">10.1001/...</a>
//           <br>
//           <a href="https://jamanetwork.com/journals/jama/fullarticle/2809749">Article</a>
//         </div>
//       </div>
//     </div>
//
// JAMA wraps DOI URLs through their own `jamanetwork.com/article.aspx?doi=`
// redirector. We extract the literal DOI from the link text (which is the
// raw DOI string) and rebuild as a plain `https://doi.org/...` URL so the
// reader doesn't have to bounce through JAMA first.

import type { CleanReport, SiteCleaner } from "./types.js";

const JAMA_HOSTS = ["jamanetwork.com", "www.jamanetwork.com"];

const DOI_RE = /^10\.[\d.]+\/\S+$/;

function extractDoi(refContent: HTMLElement): string | null {
  // 1. Inline `doi:<a>10.1001/...</a>` pattern.
  for (const a of Array.from(refContent.querySelectorAll<HTMLAnchorElement>("a"))) {
    const text = (a.textContent ?? "").trim();
    if (DOI_RE.test(text)) return `https://doi.org/${text}`;
  }
  // 2. Fall back to the first jamanetwork article-link (still useful).
  for (const a of Array.from(refContent.querySelectorAll<HTMLAnchorElement>("a[href]"))) {
    const href = a.getAttribute("href") ?? "";
    if (/^https?:\/\/(?:www\.)?jamanetwork\.com\/journals\/.+\/fullarticle\//.test(href)) {
      return href;
    }
  }
  return null;
}

export const jamaCleaner: SiteCleaner = {
  name: "jama",

  matches(host) {
    return JAMA_HOSTS.some((h) => host === h || host.endsWith(`.${h}`));
  },

  clean(doc) {
    const report: CleanReport = {
      matched: true,
      removedNodeCount: 0,
      warnings: [],
    };

    // Map ref id → paper URL from the bottom list.
    const idToUrl = new Map<string, string>();
    const refs = Array.from(
      doc.querySelectorAll<HTMLElement>("div.references > div.reference"),
    );
    for (const ref of refs) {
      const numAnchor = ref.querySelector<HTMLAnchorElement>("a.reference-number");
      const id = numAnchor?.getAttribute("id");
      const content = ref.querySelector<HTMLElement>("div.reference-content");
      if (!id || !content) continue;
      const url = extractDoi(content);
      if (url) idToUrl.set(id, url);
    }

    if (refs.length === 0) {
      report.warnings.push("no div.reference items found");
    }

    // Rewrite body sup anchors that point at #refId into the paper URL.
    const refAnchors = Array.from(
      doc.querySelectorAll<HTMLAnchorElement>('sup a.ref-link, a.ref-link.section-jump-link'),
    );
    for (const a of refAnchors) {
      const href = a.getAttribute("href") ?? "";
      const frag = href.split("#").pop() ?? "";
      const url = idToUrl.get(frag);
      if (!url) continue;
      a.setAttribute("href", url);
      a.setAttribute("target", "_blank");
      a.setAttribute("rel", "noopener");
      report.removedNodeCount++;
    }

    return report;
  },
};
