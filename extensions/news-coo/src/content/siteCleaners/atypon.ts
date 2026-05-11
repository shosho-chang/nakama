// Atypon-published journals (Lancet, NEJM, and other ScienceDirect/Atypon
// platforms that share the same `core-reference-list` / `section#references`
// + `<div role="list">` bibliography shell).
//
// Two body-marker variants we have to handle:
//
//   1. Lancet — citation marker wrapped inside an inline drop-block:
//      <span class="dropBlock reference-citations">
//        <a class="reference-citations__ctrl"><sup>1,2</sup></a>
//        <div class="dropBlock__holder">…full ref bodies…</div>
//      </span>
//      Removing the drop-block naively would also remove the <sup> marker.
//
//   2. NEJM — plain <sup><a data-xml-rid="rN" href="#core-collateral-rN">N</a></sup>
//      No drop-block; just rewrite the href to the paper URL.
//
// Bottom References list shape is identical for both: <section> with
// <div role="list"> / <div role="listitem"> wrapping <div class="citations">
// blocks. We rewrite into <ol>/<li> so Defuddle picks it up, and lift the
// primary external link (Crossref/DOI > PubMed > Scholar) onto each in-text
// <sup> so a single click jumps to the paper.

import type { CleanReport, SiteCleaner } from "./types.js";

const ATYPON_HOSTS = [
  "thelancet.com",
  "www.thelancet.com",
  "nejm.org",
  "www.nejm.org",
];

const REFS_SECTION_SELECTORS = [
  "section#references",                      // Lancet
  "section.core-reference-list",             // NEJM (id="bibliography")
  "section#bibliography",                    // NEJM alt
];

const PRIMARY_LINK_CLASSES = [
  "core-xlink-crossref",       // DOI via Crossref
  "core-xlink-pubmed",         // PubMed
  "core-xlink-google-scholar", // Google Scholar fallback
];

function pickPrimaryLink(item: Element): string | null {
  for (const cls of PRIMARY_LINK_CLASSES) {
    const a = item.querySelector<HTMLAnchorElement>(`.${cls} a[href]`);
    const href = a?.getAttribute("href");
    if (href && /^https?:/i.test(href)) return href;
  }
  const any = item.querySelector<HTMLAnchorElement>(
    '.external-links a[href^="http"]',
  );
  return any?.getAttribute("href") ?? null;
}

function findRefsSection(doc: Document): HTMLElement | null {
  for (const sel of REFS_SECTION_SELECTORS) {
    const el = doc.querySelector<HTMLElement>(sel);
    if (el) return el;
  }
  return null;
}

export const atyponCleaner: SiteCleaner = {
  name: "atypon",

  matches(host) {
    return ATYPON_HOSTS.some((h) => host === h || host.endsWith(`.${h}`));
  },

  clean(doc) {
    const report: CleanReport = {
      matched: true,
      removedNodeCount: 0,
      warnings: [],
    };

    // Build position → URL map from the bottom References list before any
    // mutations so body-marker rewrites in step 2 can attach DOI hrefs.
    const numberToUrl = new Map<number, string>();
    const refsSection = findRefsSection(doc);
    const items = refsSection
      ? Array.from(refsSection.querySelectorAll<HTMLElement>('[role="listitem"]'))
      : [];
    items.forEach((item, idx) => {
      const url = pickPrimaryLink(item);
      if (url) numberToUrl.set(idx + 1, url);
    });

    // 1. Lancet drop-blocks → keep <sup>, drop the holder.
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

    // 2. NEJM-style plain <sup><a data-xml-rid="rN">N</a></sup>: rewrite the
    //    fragment-only href to the resolved paper URL when we have one.
    const xmlRefAnchors = Array.from(
      doc.querySelectorAll<HTMLAnchorElement>("sup a[data-xml-rid]"),
    );
    for (const a of xmlRefAnchors) {
      const text = (a.textContent ?? "").trim();
      const n = parseInt(text, 10);
      if (!Number.isFinite(n)) continue;
      const url = numberToUrl.get(n);
      if (!url) continue;
      a.setAttribute("href", url);
      a.setAttribute("target", "_blank");
      a.setAttribute("rel", "noopener");
    }

    // 3. Rewrite bottom References section into semantic <ol>/<li>.
    if (refsSection) {
      if (items.length > 0) {
        const ol = doc.createElement("ol");
        items.forEach((item, idx) => {
          const n = idx + 1;
          const li = doc.createElement("li");
          li.id = `ref-${n}`;
          const cc = item.querySelector<HTMLElement>(".citation-content")
            ?? item.querySelector<HTMLElement>(".citations")
            ?? item;
          const cloned = cc.cloneNode(true) as HTMLElement;
          for (const label of Array.from(cloned.querySelectorAll(".label"))) {
            label.remove();
          }
          // NEJM also injects "Go to Citation" reverse anchors; strip them.
          for (const back of Array.from(cloned.querySelectorAll(".to-citation, .to-citation__wrapper"))) {
            back.remove();
          }
          li.innerHTML = cloned.innerHTML;
          const extLinks = Array.from(
            item.querySelectorAll<HTMLAnchorElement>(".external-links a"),
          ).filter((a) => {
            // Drop "Go to Citation" — they're internal anchors, not paper links.
            const cls = a.parentElement?.className?.toString() ?? "";
            return !/to-citation/.test(cls);
          });
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
      report.warnings.push("no references section found (tried " + REFS_SECTION_SELECTORS.join(", ") + ")");
    }

    // 4. Inject `<meta name="author">` so Defuddle's metadata extractor picks
    //    up the right author. NEJM exposes authors via `meta[name="dc.Creator"]`
    //    (one per author); fall back to the .contributors author block.
    const head = doc.querySelector("head");
    if (head && !head.querySelector('meta[name="author"]')?.getAttribute("content")) {
      const dcCreators = Array.from(
        doc.querySelectorAll<HTMLMetaElement>(
          'meta[name="dc.Creator"], meta[name="DC.Creator"]',
        ),
      ).map((m) => m.content?.trim()).filter((s): s is string => !!s);
      let authorString = dcCreators.join(", ");
      if (!authorString) {
        const contribAuthors = Array.from(
          doc.querySelectorAll<HTMLElement>(
            '.contributors span[property="author"], .core-authors span[property="author"]',
          ),
        );
        const names: string[] = [];
        for (const span of contribAuthors) {
          const given = span.querySelector('[property="givenName"]')?.textContent?.trim() ?? "";
          const surname = span.querySelector('[property="familyName"]')?.textContent?.trim() ?? "";
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
