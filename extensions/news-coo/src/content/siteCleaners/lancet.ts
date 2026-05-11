// Lancet family cleaner — preserves citation markers, strips inline reference
// drop-block bodies, and points each in-text citation directly at the paper's
// canonical URL (DOI > PubMed > Scholar) so a single click on the superscript
// number opens the cited paper. The bottom References list still renders
// with full metadata + per-source links for browsing.
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
//       <div role="listitem"><div id="bib1" class="citations">
//         <div class="citation-content">…</div>
//         <div class="external-links">
//           <a href="https://doi.org/…">Crossref</a>
//           <a href="https://pubmed.ncbi.nlm.nih.gov/…">PubMed</a>
//           …
//         </div>
//       </div></div>
//       …
//     </div></div>
//   </section>

import type { CleanReport, SiteCleaner } from "./types.js";

const LANCET_HOSTS = [
  "thelancet.com",
  "www.thelancet.com",
];

// Order = preference. First match wins as the body sup target.
const PRIMARY_LINK_CLASSES = [
  "core-xlink-crossref",      // DOI via Crossref
  "core-xlink-pubmed",        // PubMed
  "core-xlink-google-scholar", // Google Scholar fallback
];

function pickPrimaryLink(item: Element): string | null {
  for (const cls of PRIMARY_LINK_CLASSES) {
    const a = item.querySelector<HTMLAnchorElement>(`.${cls} a[href]`);
    const href = a?.getAttribute("href");
    if (href && /^https?:/i.test(href)) return href;
  }
  // Last resort: first absolute external link in the item.
  const any = item.querySelector<HTMLAnchorElement>(
    '.external-links a[href^="http"]',
  );
  return any?.getAttribute("href") ?? null;
}

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

    // First, build a number → primary URL map from the bottom References list.
    // Done up front so the body-marker rewrite can attach DOI hrefs in one pass.
    const numberToUrl = new Map<number, string>();
    const refsSection = doc.querySelector<HTMLElement>("section#references");
    const items = refsSection
      ? Array.from(refsSection.querySelectorAll<HTMLElement>('[role="listitem"]'))
      : [];
    items.forEach((item, idx) => {
      const url = pickPrimaryLink(item);
      if (url) numberToUrl.set(idx + 1, url);
    });

    // 1. Replace every inline reference drop-block with anchored <sup>.
    //    Anchor href = paper URL (DOI/PubMed/Scholar) when we resolved one,
    //    falling back to in-document #fn:N otherwise. Lancet's marker is
    //    wrapped inside the dropBlock; deleting the whole dropBlock would
    //    remove the citation number too.
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
          const url = numberToUrl.get(parseInt(n, 10));
          a.setAttribute("href", url ?? `#fn:${n}`);
          if (url) {
            a.setAttribute("target", "_blank");
            a.setAttribute("rel", "noopener");
          }
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
    //    keeps the canonical reference list in the markdown for browsing.
    if (refsSection) {
      if (items.length > 0) {
        const ol = doc.createElement("ol");
        items.forEach((item, idx) => {
          const n = idx + 1;
          const li = doc.createElement("li");
          li.id = `ref-${n}`;
          // Citation body — strip the in-list "View in article" reverse anchor.
          const cc = item.querySelector<HTMLElement>(".citation-content")
            ?? item.querySelector<HTMLElement>(".citations")
            ?? item;
          const cloned = cc.cloneNode(true) as HTMLElement;
          for (const label of Array.from(cloned.querySelectorAll(".label"))) {
            label.remove();
          }
          li.innerHTML = cloned.innerHTML;
          // External-link badges (Crossref → DOI, PubMed, Google Scholar)
          // appended as plain anchors so Markdown emits real outbound links.
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
