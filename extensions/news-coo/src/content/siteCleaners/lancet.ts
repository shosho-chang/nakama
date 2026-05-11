// Lancet family cleaner — strips inline reference drop-blocks that Defuddle
// otherwise inlines into prose (ADR-025 root cause).
//
// Verified 2026-05-11 against Hantavirus article (PIIS1473-3099(23)00128-7):
// 145 .dropBlock.reference-citations nodes between citation markers and the
// rest of the paragraph text. Lancet-family domains share the ScienceDirect
// reading shell and use the same DOM pattern.

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

    // The drop-block sits adjacent to each <sup> citation marker. Removing
    // it leaves the <sup>N</sup> in place (preserved as plain "N" by
    // Defuddle's footnote handling) and drops the in-prose reference body.
    const dropBlocks = doc.querySelectorAll<HTMLElement>(
      ".dropBlock.reference-citations, .reference-citations__ctrl",
    );
    for (const el of Array.from(dropBlocks)) {
      el.remove();
      report.removedNodeCount++;
    }

    return report;
  },
};
