// ScienceDirect (Elsevier) cleaner.
//
// DOM observed 2026-05-11 on /science/article/pii/S0092867426004605:
//
//   Body markers:
//     <a class="anchor anchor-primary" href="#bib1" name="bbib1"
//        data-xocs-content-type="reference" data-xocs-content-id="bib1">
//       <span class="anchor-text-container">
//         <span class="anchor-text"><sup>1</sup></span>
//       </span>
//     </a>
//
//   Bottom list:
//     <ol class="references">
//       <li>
//         <span class="label">
//           <a class="anchor" href="#bbib1" id="ref-id-bib1">…1…</a>
//         </span>
//         <span class="reference" id="sref1">
//           <div class="contribution">…authors / title…</div>
//           <div class="host">Cell, 188 (2025), …,
//             <a href="https://doi.org/10.1016/j.cell.2025.03.011">…</a>
//           </div>
//         </span>
//       </li>
//     </ol>
//
//   Authors:
//     <div id="author-group">
//       <button data-xocs-content-type="author">
//         <span class="given-name">Jiaming</span> <span class="surname">Li</span>
//         <span class="author-ref"><sup>1</sup></span>…
//       </button>, <button>…</button>
//     </div>
//   Defuddle's author heuristic picks up affiliation superscripts as part of the
//   name. We collapse each author button to "FirstName LastName" so the picked
//   author field is clean.

import type { CleanReport, SiteCleaner } from "./types.js";

const SD_HOSTS = ["sciencedirect.com", "www.sciencedirect.com"];

export const sciencedirectCleaner: SiteCleaner = {
  name: "sciencedirect",

  matches(host) {
    return SD_HOSTS.some((h) => host === h || host.endsWith(`.${h}`));
  },

  clean(doc) {
    const report: CleanReport = {
      matched: true,
      removedNodeCount: 0,
      warnings: [],
    };

    // 1. Build bibN → DOI URL map from ol.references > li.
    const idToUrl = new Map<string, string>();
    const items = Array.from(doc.querySelectorAll<HTMLElement>("ol.references > li"));
    for (const li of items) {
      const labelAnchor = li.querySelector<HTMLAnchorElement>(
        'span.label a[id^="ref-id-bib"]',
      );
      const labelId = labelAnchor?.getAttribute("id") ?? "";
      // ref-id-bibN → bibN
      const bibId = labelId.replace(/^ref-id-/, "");
      if (!bibId) continue;
      const doiAnchor = li.querySelector<HTMLAnchorElement>(
        'a[href^="https://doi.org/"]',
      );
      let url = doiAnchor?.getAttribute("href") ?? "";
      if (!url) {
        // Fallback: scopus / pii internal link
        const piiAnchor = li.querySelector<HTMLAnchorElement>(
          'a[href^="/science/article/pii/"]:not([href*="pdfft"])',
        );
        const pii = piiAnchor?.getAttribute("href");
        if (pii) url = `https://www.sciencedirect.com${pii}`;
      }
      if (url) idToUrl.set(bibId, url);
    }

    if (items.length === 0) {
      report.warnings.push("no ol.references > li items found");
    }

    // 2. Rewrite body reference anchors.
    const refAnchors = Array.from(
      doc.querySelectorAll<HTMLAnchorElement>(
        'a[data-xocs-content-type="reference"]',
      ),
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

    // 3. Clean author group so Defuddle picks "First Last, First Last" instead
    //    of names polluted with affiliation superscripts.
    const authorGroup = doc.querySelector<HTMLElement>("#author-group");
    if (authorGroup) {
      const buttons = Array.from(
        authorGroup.querySelectorAll<HTMLElement>(
          'button[data-xocs-content-type="author"]',
        ),
      );
      const names: string[] = [];
      for (const btn of buttons) {
        const given = btn.querySelector(".given-name")?.textContent?.trim() ?? "";
        const surname = btn.querySelector(".surname")?.textContent?.trim() ?? "";
        const full = `${given} ${surname}`.trim();
        if (full) names.push(full);
      }
      if (names.length > 0) {
        // Replace contents with a clean comma-separated list inside a single <p>.
        while (authorGroup.firstChild) authorGroup.removeChild(authorGroup.firstChild);
        const p = doc.createElement("p");
        p.className = "author";
        p.textContent = names.join(", ");
        authorGroup.appendChild(p);
        report.removedNodeCount += buttons.length;
      } else {
        report.warnings.push("author-group present but no .given-name/.surname extracted");
      }
    }

    return report;
  },
};
