// Shared "figure → Obsidian block ID" rewiring for site cleaners.
//
// Why this exists: published articles cross-reference figures inline ("see
// Fig. 3c"), and the anchor href typically points at the live page's figure
// fragment (e.g. `https://site.com/article#Fig3`). After we save the article
// to the vault, that link still leaves Obsidian — but the user wants it to
// jump to the figure inside the saved markdown instead.
//
// Strategy is the same for every site:
//   1. Walk each <figure> in the doc and derive a stable raw ID for it
//      (sites vary — Nature stores the id on an inner <b>, ScienceDirect on
//      the <figure> itself, etc.). Per-site `extractId` handles this.
//   2. Append " ^<blockId>" to the figcaption's text content. Obsidian
//      treats `^<id>` at the end of a block as the block's ID. We append it
//      as a text node so it survives Defuddle's caption rebuild (which joins
//      text nodes with spaces).
//   3. Rewrite every <a> whose href fragment matches a raw figure id to
//      `#^<blockId>`. In markdown this becomes `[3c](#^Fig3)` — a local
//      block-reference link that Obsidian resolves to the tagged figure.

export interface FigureAnchorOptions {
  // CSS selector matching figure-shaped elements in the body. Default: "figure".
  figureSelector?: string;
  // Pick a raw id from a figure element. Sites that put the id on the
  // <figure> itself can use the default. Sites that store it elsewhere
  // (Nature: <b id="FigN"> inside figcaption) supply a custom extractor.
  // Returning null skips this figure.
  extractId?: (fig: HTMLElement) => string | null;
  // CSS selector for the element inside the figure whose text gets the
  // `^<blockId>` marker appended. Default: "figcaption".
  captionSelector?: string;
  // Optional anchor predicate. By default any <a> whose href fragment
  // matches a collected figure id is rewritten; pass a predicate to scope
  // further (e.g. skip non-numeric-text anchors).
  anchorFilter?: (a: HTMLAnchorElement) => boolean;
}

// Obsidian block IDs accept [A-Za-z0-9-]; everything else collapses to a dash.
function toBlockId(raw: string): string {
  return raw.replace(/[^A-Za-z0-9-]+/g, "-").replace(/^-+|-+$/g, "");
}

// Tag each <figure>'s caption with an Obsidian block ID and return the
// raw-id → block-id map (raw id is what href fragments use; block id is
// what we point them at).
export function tagFigureBlockIds(
  doc: Document,
  opts: FigureAnchorOptions = {},
): Map<string, string> {
  const figureSelector = opts.figureSelector ?? "figure";
  const captionSelector = opts.captionSelector ?? "figcaption";
  const extractId = opts.extractId ?? ((fig) => fig.getAttribute("id"));

  const rawToBlock = new Map<string, string>();
  const figures = Array.from(doc.querySelectorAll<HTMLElement>(figureSelector));
  for (const fig of figures) {
    const rawId = extractId(fig);
    if (!rawId) continue;
    const blockId = toBlockId(rawId);
    if (!blockId) continue;
    const caption = fig.querySelector<HTMLElement>(captionSelector);
    if (!caption) continue;
    caption.appendChild(doc.createTextNode(` ^${blockId}`));
    rawToBlock.set(rawId, blockId);
  }
  return rawToBlock;
}

// Rewrite in-text anchors whose href fragment matches a collected raw id
// to point at the corresponding Obsidian block ref. Returns the rewritten
// count.
export function rewriteFigureAnchors(
  doc: Document,
  rawToBlock: Map<string, string>,
  opts: FigureAnchorOptions = {},
): number {
  if (rawToBlock.size === 0) return 0;
  let rewritten = 0;
  const anchors = Array.from(
    doc.querySelectorAll<HTMLAnchorElement>('a[href*="#"]'),
  );
  for (const a of anchors) {
    if (opts.anchorFilter && !opts.anchorFilter(a)) continue;
    const href = a.getAttribute("href") ?? "";
    const frag = href.split("#").pop() ?? "";
    if (!frag) continue;
    const blockId = rawToBlock.get(frag);
    if (!blockId) continue;
    a.setAttribute("href", `#^${blockId}`);
    // Tracking attrs aren't needed once we've redirected the link locally.
    a.removeAttribute("data-track");
    a.removeAttribute("data-track-action");
    a.removeAttribute("data-track-label");
    rewritten++;
  }
  return rewritten;
}

// Convenience: tag captions + rewrite anchors in one call.
export function wireFigureBlockIds(
  doc: Document,
  opts: FigureAnchorOptions = {},
): { taggedCount: number; rewrittenCount: number } {
  const rawToBlock = tagFigureBlockIds(doc, opts);
  const rewrittenCount = rewriteFigureAnchors(doc, rawToBlock, opts);
  return { taggedCount: rawToBlock.size, rewrittenCount };
}
