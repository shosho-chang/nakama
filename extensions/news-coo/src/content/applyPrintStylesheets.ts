// ADR-025 Stage 0 spike — switch print stylesheets to media="all" before
// extraction, so any DOM nodes hidden by `@media print { display: none }`
// rules get removed from layout, which Defuddle then treats as non-content.

export interface PrintSwitchReport {
  stylesheetsTouched: number;
  inlinePrintBlocks: number;
  warnings: string[];
}

export function applyPrintStylesheets(doc: Document): PrintSwitchReport {
  const report: PrintSwitchReport = {
    stylesheetsTouched: 0,
    inlinePrintBlocks: 0,
    warnings: [],
  };

  // <link rel="stylesheet" media="print"> — broaden media so the rules apply.
  for (const link of Array.from(
    doc.querySelectorAll<HTMLLinkElement>('link[rel="stylesheet"][media]'),
  )) {
    const media = (link.getAttribute("media") ?? "").toLowerCase();
    if (media === "print" || media.includes("print")) {
      link.setAttribute("media", "all");
      report.stylesheetsTouched++;
    }
  }

  // Inline <style media="print"> — same treatment.
  for (const style of Array.from(
    doc.querySelectorAll<HTMLStyleElement>('style[media]'),
  )) {
    const media = (style.getAttribute("media") ?? "").toLowerCase();
    if (media === "print" || media.includes("print")) {
      style.setAttribute("media", "all");
      report.stylesheetsTouched++;
    }
  }

  // Inline @media print { ... } blocks inside <style> tags need rewriting
  // because we cannot re-evaluate CSSOM mid-pass. We rewrite the @media rule
  // to drop the print gate so the inner rules become unconditional.
  for (const style of Array.from(doc.querySelectorAll<HTMLStyleElement>("style"))) {
    const cssText = style.textContent ?? "";
    if (!/@media[^{]*\bprint\b/i.test(cssText)) continue;
    try {
      const rewritten = cssText.replace(
        /@media\s+([^{]*)\bprint\b([^{]*)\{/gi,
        (_match, before: string, after: string) => {
          const cleaned = `${before}${after}`.trim().replace(/^[,\s]+|[,\s]+$/g, "");
          // If print was the only token, drop the @media wrapper by emitting
          // a no-op selector. Otherwise keep the other media tokens.
          return cleaned ? `@media ${cleaned} {` : "@media all {";
        },
      );
      if (rewritten !== cssText) {
        style.textContent = rewritten;
        report.inlinePrintBlocks++;
      }
    } catch (err) {
      report.warnings.push(`@media print rewrite failed: ${String(err)}`);
    }
  }

  return report;
}

// Helper: after applying print stylesheets, force the document to remove
// elements that the print stylesheet hides via `display: none`. Browsers
// only re-evaluate display on next layout pass; in a content-script context
// we can read computed style synchronously.
export function removeHiddenByPrint(doc: Document): number {
  if (!doc.defaultView) return 0;
  const view = doc.defaultView;
  let removed = 0;
  // Walk in reverse-document-order so child removal doesn't break NodeList
  // ordering on later iterations.
  const all = Array.from(doc.body.querySelectorAll<HTMLElement>("*"));
  for (let i = all.length - 1; i >= 0; i--) {
    const el = all[i];
    if (!el.isConnected) continue;
    let computed: CSSStyleDeclaration | null = null;
    try {
      computed = view.getComputedStyle(el);
    } catch {
      continue;
    }
    if (computed.display === "none" || computed.visibility === "hidden") {
      el.remove();
      removed++;
    }
  }
  return removed;
}
