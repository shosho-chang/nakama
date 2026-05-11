// BMJ.com cleaner (Highwire Press platform).
//
// DOM observed 2026-05-11 on bmj.com/content/360/bmj.k322:
//
//   Body markers (NOT wrapped in <sup>):
//     <a class="xref-bibr" href="#ref-1">1</a>
//
//   Bottom list:
//     <ol class="cit-list">
//       <li>
//         <a class="rev-xref-ref" href="#xref-ref-1-1" id="ref-1">↵</a>
//         <div class="cit ref-cit ref-journal"
//              id="cit-360.feb14_8.k322.1"
//              data-doi="10.1002/ijc.29210">
//           <div class="cit-metadata">…</div>
//         </div>
//       </li>
//     </ol>
//
// DOI lives conveniently in `data-doi` on the <div class="cit"> wrapper.

import type { CleanReport, SiteCleaner } from "./types.js";

const BMJ_HOSTS = ["bmj.com", "www.bmj.com"];

export const bmjCleaner: SiteCleaner = {
  name: "bmj",

  matches(host) {
    return BMJ_HOSTS.some((h) => host === h || host.endsWith(`.${h}`));
  },

  clean(doc) {
    const report: CleanReport = {
      matched: true,
      removedNodeCount: 0,
      warnings: [],
    };

    // Map the ref-N anchor id → DOI from the bottom <ol class="cit-list">.
    const idToUrl = new Map<string, string>();
    const items = Array.from(doc.querySelectorAll<HTMLElement>("ol.cit-list > li"));
    for (const li of items) {
      const idAnchor = li.querySelector<HTMLAnchorElement>('a[id^="ref-"]');
      const id = idAnchor?.getAttribute("id");
      if (!id) continue;
      const cit = li.querySelector<HTMLElement>("div.cit[data-doi]");
      const doi = cit?.getAttribute("data-doi");
      if (doi) idToUrl.set(id, `https://doi.org/${doi}`);
    }

    if (items.length === 0) {
      report.warnings.push("no ol.cit-list > li items found");
    }

    // Rewrite body xref-bibr anchors into the paper URL.
    const refAnchors = Array.from(
      doc.querySelectorAll<HTMLAnchorElement>("a.xref-bibr"),
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
