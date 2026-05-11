// ScienceDirect (Elsevier) cleaner.
//
// DOM observed 2026-05-11 on /science/article/pii/S0092867426004605:
//
//   Body markers:
//     <a class="anchor" href="#bib1" name="bbib1"
//        data-xocs-content-type="reference" data-xocs-content-id="bib1">
//       <span class="anchor-text-container">
//         <span class="anchor-text"><sup>1</sup></span>
//       </span>
//     </a>
//   Defuddle's footnote heuristic doesn't recognise this nested wrapping —
//   the link contents get stripped, leaving punctuation orphans like
//   "<sup>,</sup><sup>,</sup>". So we replace the whole anchor with a flat
//   `<sup><a href="DOI">N</a></sup>` Defuddle leaves alone.
//
//   Bottom list:
//     <ol class="references">
//       <li>
//         <span class="label"><a id="ref-id-bib1" href="#bbib1">1</a></span>
//         <span class="reference" id="sref1">
//           <div class="contribution">…authors / title…</div>
//           <div class="host">Cell, 188 (2025), …,
//             <a href="https://doi.org/10.1016/j.cell.2025.03.011">DOI</a>
//             <a href="https://www.scopus.com/...">Scopus</a>
//             <a href="https://scholar.google.com/...">Scholar</a>
//           </div>
//         </span>
//       </li>
//     </ol>
//   The original `<ol>` is opaque to Defuddle (it confuses the in-li anchors
//   with body refs and dumps them into footnote definitions). We rebuild it
//   as a clean numbered list keyed `id="ref-N"` with citation text + a single
//   "[paper]" anchor.
//
//   Authors:
//     <div id="author-group">
//       <button data-xocs-content-type="author">…First Last…superscripts…</button>
//     </div>
//   Defuddle reads the author from `<meta name="author">`, which ScienceDirect
//   doesn't emit. We extract clean "First Last" pairs from #author-group and
//   inject a `<meta name="author">` into <head> for Defuddle to pick up.

import type { CleanReport, SiteCleaner } from "./types.js";

const SD_HOSTS = ["sciencedirect.com", "www.sciencedirect.com"];

function extractAuthorString(doc: Document): string | null {
  const ag = doc.querySelector<HTMLElement>("#author-group");
  if (!ag) return null;
  const buttons = Array.from(
    ag.querySelectorAll<HTMLElement>('button[data-xocs-content-type="author"]'),
  );
  const names: string[] = [];
  for (const btn of buttons) {
    const given = btn.querySelector(".given-name")?.textContent?.trim() ?? "";
    const surname = btn.querySelector(".surname")?.textContent?.trim() ?? "";
    const full = `${given} ${surname}`.trim();
    if (full) names.push(full);
  }
  return names.length > 0 ? names.join(", ") : null;
}

function pickReferenceUrl(li: HTMLElement): string | null {
  // Priority: doi.org > scopus > scholar > pii article path.
  const doi = li.querySelector<HTMLAnchorElement>('a[href^="https://doi.org/"]');
  if (doi) return doi.getAttribute("href");
  const scopus = li.querySelector<HTMLAnchorElement>('a[href*="scopus.com"]');
  if (scopus) return scopus.getAttribute("href");
  const scholar = li.querySelector<HTMLAnchorElement>('a[href*="scholar.google"]');
  if (scholar) return scholar.getAttribute("href");
  const pii = li.querySelector<HTMLAnchorElement>(
    'a[href^="/science/article/pii/"]:not([href*="pdfft"])',
  );
  const piiHref = pii?.getAttribute("href");
  if (piiHref) return `https://www.sciencedirect.com${piiHref}`;
  return null;
}

function citationTextFromLi(li: HTMLElement): string {
  // Pull text from the .reference span without the extra anchor links;
  // strip the leading label anchor's content too.
  const refSpan = li.querySelector<HTMLElement>("span.reference");
  if (!refSpan) return (li.textContent ?? "").trim();
  // Clone and remove anchors + scripts so plain text remains.
  const clone = refSpan.cloneNode(true) as HTMLElement;
  for (const a of Array.from(clone.querySelectorAll("a"))) a.remove();
  return (clone.textContent ?? "").replace(/\s+/g, " ").trim();
}

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

    // 1. Build bibN → URL map from ol.references > li.
    const idToUrl = new Map<string, string>();
    const idToText = new Map<string, string>();
    const refsList = doc.querySelector<HTMLElement>("ol.references");
    const items = refsList
      ? Array.from(refsList.querySelectorAll<HTMLElement>(":scope > li"))
      : [];
    for (const li of items) {
      const labelAnchor = li.querySelector<HTMLAnchorElement>(
        'span.label a[id^="ref-id-bib"]',
      );
      const labelId = labelAnchor?.getAttribute("id") ?? "";
      const bibId = labelId.replace(/^ref-id-/, "");
      if (!bibId) continue;
      const url = pickReferenceUrl(li);
      if (url) idToUrl.set(bibId, url);
      idToText.set(bibId, citationTextFromLi(li));
    }

    if (items.length === 0) {
      report.warnings.push("no ol.references > li items found");
    }

    // 2. Replace each body reference anchor with a flat `<sup><a></sup>` so
    //    Defuddle leaves it as a normal external link.
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
      // Recover the visible number from the original sup; fall back to bibN→N.
      const sup = a.querySelector("sup");
      const numberText =
        sup?.textContent?.trim() || frag.replace(/^bib/, "");
      const newSup = doc.createElement("sup");
      const newAnchor = doc.createElement("a");
      newAnchor.setAttribute("href", url);
      newAnchor.setAttribute("target", "_blank");
      newAnchor.setAttribute("rel", "noopener");
      newAnchor.textContent = numberText;
      newSup.appendChild(newAnchor);
      a.replaceWith(newSup);
      report.removedNodeCount++;
    }

    // 3. Rebuild ol.references as a clean <ol id="references"> Defuddle parses.
    if (refsList && items.length > 0) {
      const newOl = doc.createElement("ol");
      newOl.setAttribute("id", "references");
      for (const li of items) {
        const labelAnchor = li.querySelector<HTMLAnchorElement>(
          'span.label a[id^="ref-id-bib"]',
        );
        const labelId = labelAnchor?.getAttribute("id") ?? "";
        const bibId = labelId.replace(/^ref-id-/, "");
        if (!bibId) continue;
        const newLi = doc.createElement("li");
        newLi.setAttribute("id", `ref-${bibId.replace(/^bib/, "")}`);
        const text = idToText.get(bibId) ?? "";
        const url = idToUrl.get(bibId);
        newLi.textContent = text + (text && url ? " " : "");
        if (url) {
          const a = doc.createElement("a");
          a.setAttribute("href", url);
          a.setAttribute("target", "_blank");
          a.setAttribute("rel", "noopener");
          a.textContent = "[paper]";
          newLi.appendChild(a);
        }
        newOl.appendChild(newLi);
        report.removedNodeCount++;
      }
      refsList.replaceWith(newOl);
    }

    // 4. Inject `<meta name="author">` so Defuddle's metadata extractor
    //    doesn't fall back to the abstract heading.
    const authorString = extractAuthorString(doc);
    if (authorString) {
      const head = doc.querySelector("head");
      if (head) {
        const existing = head.querySelector('meta[name="author"]');
        if (existing) existing.remove();
        const meta = doc.createElement("meta");
        meta.setAttribute("name", "author");
        meta.setAttribute("content", authorString);
        head.appendChild(meta);
        report.removedNodeCount++;
      }
    } else {
      report.warnings.push("no #author-group → meta[name=author] not injected");
    }

    return report;
  },
};
