// Lancet family cleaner — preserves citation markers and the bottom
// References list while stripping inline reference drop-block bodies that
// Defuddle otherwise inlines into prose (ADR-025 root cause).
//
// DOM structure observed 2026-05-11 on PIIS1473-3099(23)00128-7:
//
//   <div role="paragraph">
//     Hantaan virus (HTNV) was discovered in 1976,
//     <span class="dropBlock reference-citations">
//       <a class="reference-citations__ctrl"><sup>1,2</sup></a>
//       <div class="dropBlock__holder">…full ref bodies…</div>
//     </span>
//     and then several other hantaviruses…
//   </div>
//
//   <section id="references">
//     <h2>References</h2>
//     <div><div role="list">
//       <div role="listitem"><div id="bib1" class="citations">…</div></div>
//       …
//     </div></div>
//   </section>
//
// We replace each <span class="dropBlock"> with just the <sup> marker, then
// rewrite the ARIA-role list at the bottom into <ol>/<li> so Defuddle picks
// it up as the canonical references section.

import type { CleanReport, SiteCleaner } from "./types.js";

const LANCET_HOSTS = [
  "thelancet.com",
  "www.thelancet.com",
];

export const lancetCleaner: SiteCleaner = {
  name: "lancet",

  matches(host) {
    return LANCET_HOSTS.some((h) => host === h || host.endsWith(`.${h}`));
  },

  clean(doc) {
    const report: CleanReport = {
      matched: true,
      removedNodeCount: 0,
      warnings: [],
    };

    // 1. Replace every inline reference drop-block with just its <sup> marker.
    //    Lancet's marker is wrapped inside the dropBlock; if we delete the
    //    whole dropBlock the citation number disappears too.
    const dropBlocks = Array.from(
      doc.querySelectorAll<HTMLElement>(".dropBlock.reference-citations"),
    );
    for (const block of dropBlocks) {
      const sup = block.querySelector<HTMLElement>("sup");
      if (sup) {
        // Preserve the citation number (e.g. "1,2") in plain superscript.
        const replacement = doc.createElement("sup");
        replacement.textContent = sup.textContent ?? "";
        block.replaceWith(replacement);
      } else {
        block.remove();
      }
      report.removedNodeCount++;
    }

    // 2. Rewrite the bottom References section into semantic <ol>/<li>.
    //    Defuddle skips ARIA-role-only lists; converting to real list tags
    //    makes the canonical reference list survive into the markdown.
    const refsSection = doc.querySelector<HTMLElement>("section#references");
    if (refsSection) {
      const items = Array.from(
        refsSection.querySelectorAll<HTMLElement>('[role="listitem"]'),
      );
      if (items.length > 0) {
        const ol = doc.createElement("ol");
        for (const item of items) {
          const li = doc.createElement("li");
          // Pull the canonical citation block; fall back to entire item text.
          const citation = item.querySelector<HTMLElement>(".citation-content")
            ?? item.querySelector<HTMLElement>(".citations")
            ?? item;
          li.innerHTML = citation.innerHTML;
          ol.appendChild(li);
        }
        // Keep the heading; replace the ARIA-list wrapper with the real <ol>.
        const h2 = refsSection.querySelector<HTMLElement>("h2") ?? doc.createElement("h2");
        if (!h2.textContent) h2.textContent = "References";
        refsSection.innerHTML = "";
        refsSection.appendChild(h2);
        refsSection.appendChild(ol);
        // Remove the role attribute so downstream extractors don't mis-classify.
        refsSection.removeAttribute("role");
      } else {
        report.warnings.push("references section present but no role=listitem children");
      }
    } else {
      report.warnings.push("no section#references found");
    }

    return report;
  },
};
