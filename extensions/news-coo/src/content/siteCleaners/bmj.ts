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
import { wireFigureBlockIds } from "./figureAnchors.js";

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

    // Promote the article body so Defuddle's main-content heuristic picks it.
    // BMJ wraps full text in `<div class="fulltext-view">`, which Defuddle's
    // density-based scorer overlooks in favour of the dense abstract block —
    // so the resulting markdown is just the abstract + author affiliations.
    // Wrap (or convert) it into a semantic <article> element.
    const fulltext = doc.querySelector<HTMLElement>(".fulltext-view");
    if (fulltext && fulltext.tagName !== "ARTICLE") {
      const article = doc.createElement("article");
      article.className = "fulltext-view";
      while (fulltext.firstChild) article.appendChild(fulltext.firstChild);
      fulltext.replaceWith(article);
      report.removedNodeCount++;
    }

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

    // Wire in-text figure references to local Obsidian block IDs. BMJ
    // Highwire uses `<div class="fig" id="F1">` rather than semantic <figure>
    // and `<div class="caption">` for the caption, so we override both.
    wireFigureBlockIds(doc, {
      figureSelector: "figure, div.fig",
      captionSelector: "figcaption, .caption, .fig-caption",
    });

    // Inject `<meta name="author">` from dc.Creator (BMJ family ships one
    // tag per author). Falls back to `.contributor-list .name` if missing.
    const head = doc.querySelector("head");
    if (head && !head.querySelector('meta[name="author"]')?.getAttribute("content")) {
      const dcCreators = Array.from(
        doc.querySelectorAll<HTMLMetaElement>(
          'meta[name="dc.Creator"], meta[name="DC.Creator"], meta[name="citation_author"]',
        ),
      ).map((m) => m.content?.trim()).filter((s): s is string => !!s);
      let authorString = dcCreators.join(", ");
      if (!authorString) {
        const contribItems = Array.from(
          doc.querySelectorAll<HTMLElement>(".contributor-list > li, .contributors li"),
        );
        const names: string[] = [];
        for (const li of contribItems) {
          const given = li.querySelector(".name-given, [class*='given-name']")?.textContent?.trim() ?? "";
          const surname = li.querySelector(".name-surname, [class*='family-name'], [class*='surname']")?.textContent?.trim() ?? "";
          const full = `${given} ${surname}`.trim();
          if (full) names.push(full);
        }
        authorString = names.join(", ");
      }
      if (authorString) {
        const existing = head.querySelector('meta[name="author"]');
        if (existing) existing.remove();
        const meta = doc.createElement("meta");
        meta.setAttribute("name", "author");
        meta.setAttribute("content", authorString);
        head.appendChild(meta);
        report.removedNodeCount++;
      }
    }

    return report;
  },
};
