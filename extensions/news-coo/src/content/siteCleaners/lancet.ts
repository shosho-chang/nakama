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

    // 1. Replace every inline reference drop-block with an anchored <sup>
    //    pointing at #ref-N. Lancet's marker is wrapped inside the dropBlock;
    //    if we delete the whole dropBlock the citation number disappears too.
    //    "1,2" becomes <sup><a href="#ref-1">1</a>,<a href="#ref-2">2</a></sup>.
    const dropBlocks = Array.from(
      doc.querySelectorAll<HTMLElement>(".dropBlock.reference-citations"),
    );
    for (const block of dropBlocks) {
      const sup = block.querySelector<HTMLElement>("sup");
      const newSup = doc.createElement("sup");
      const raw = (sup?.textContent ?? "").trim();
      const nums = raw.split(/[,\s]+/).filter((p) => /^\d+$/.test(p));
      if (nums.length === 0) {
        newSup.textContent = raw;
      } else {
        nums.forEach((n, i) => {
          const a = doc.createElement("a");
          a.setAttribute("href", `#fn:${n}`);
          a.textContent = n;
          newSup.appendChild(a);
          if (i < nums.length - 1) newSup.appendChild(doc.createTextNode(","));
        });
      }
      block.replaceWith(newSup);
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
        items.forEach((item, idx) => {
          const n = idx + 1;
          const li = doc.createElement("li");
          li.id = `fn:${n}`;
          // Citation body — strip the in-list "View in article" reverse anchor.
          const cc = item.querySelector<HTMLElement>(".citation-content")
            ?? item.querySelector<HTMLElement>(".citations")
            ?? item;
          const cloned = cc.cloneNode(true) as HTMLElement;
          for (const label of Array.from(cloned.querySelectorAll(".label"))) {
            label.remove();
          }
          li.innerHTML = cloned.innerHTML;
          // External-link badges (Crossref → DOI, PubMed, Google Scholar) —
          // keep as plain anchors so Markdown emits real links instead of
          // the publisher-internal "View in article" stubs.
          const extLinks = Array.from(
            item.querySelectorAll<HTMLAnchorElement>(".external-links a"),
          );
          if (extLinks.length > 0) {
            li.appendChild(doc.createTextNode(" "));
            extLinks.forEach((a, i) => {
              const newA = doc.createElement("a");
              newA.setAttribute("href", a.getAttribute("href") ?? "");
              newA.textContent = (a.textContent ?? "").trim();
              li.appendChild(newA);
              if (i < extLinks.length - 1) li.appendChild(doc.createTextNode(" · "));
            });
          }
          ol.appendChild(li);
        });
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
