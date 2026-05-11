// Per-site DOM cleaner contract — see ADR-025.
//
// Cleaners run after a `cloneNode(true)` of the target document and before
// Defuddle parses. They mutate the cloned <body> subtree only — never <head>
// (extractor still reads canonical URL, og:image, etc. from <head>) and
// never the live page DOM.

export interface CleanReport {
  /** True when matches() returned true for this host. */
  matched: boolean;
  /** Number of nodes the cleaner removed. Used for staleness detection. */
  removedNodeCount: number;
  /** Non-fatal issues — surfaced in popup later, logged for now. */
  warnings: string[];
}

export interface SiteCleaner {
  /** Stable identifier for logging + staleness telemetry. */
  name: string;

  /**
   * Decide whether this cleaner applies to the given page.
   * Receives both hostname and full URL so cleaners can match on path/DOI/etc.
   */
  matches: (host: string, url: string) => boolean;

  /**
   * Mutate the cloned document. MUST NOT touch <head>. MUST NOT throw on
   * malformed input — return warnings instead.
   */
  clean: (doc: Document, url: string) => CleanReport;
}
