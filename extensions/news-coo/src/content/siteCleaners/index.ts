// Cleaner registry + dispatcher (ADR-025 Stage 1).
//
// Contract invariants enforced here:
// - Per-cleaner exception isolation: a throwing cleaner does not block others.
// - Staleness signal: cleaner matched its host but removed 0 nodes → warn.
// - Selection-clip path bypass: caller decides via `runCleaners`'s selection arg.

import type { CleanReport, SiteCleaner } from "./types.js";
import { atyponCleaner } from "./atypon.js";
import { natureCleaner } from "./nature.js";
import { jamaCleaner } from "./jama.js";
import { bmjCleaner } from "./bmj.js";

const CLEANERS: SiteCleaner[] = [
  atyponCleaner,  // Lancet, NEJM, and other Atypon-platform journals
  natureCleaner,  // Nature.com family
  jamaCleaner,    // JAMA Network
  bmjCleaner,     // BMJ.com (Highwire Press)
];

export interface DispatchSummary {
  ranCleaners: string[];
  reports: Array<CleanReport & { name: string }>;
  totalRemoved: number;
}

export interface DispatchOptions {
  /** When true, skip the registry entirely (selection-clip path). */
  skip?: boolean;
  /**
   * Optional logger for staleness + warning events. Default is `console.warn`
   * so messages reach the content-script console without extra wiring.
   */
  warn?: (event: string, details: Record<string, unknown>) => void;
  /** Allow tests to inject a custom cleaner list. */
  cleaners?: SiteCleaner[];
}

export function dispatchCleaners(
  doc: Document,
  url: string,
  opts: DispatchOptions = {},
): DispatchSummary {
  const summary: DispatchSummary = {
    ranCleaners: [],
    reports: [],
    totalRemoved: 0,
  };

  if (opts.skip) return summary;

  const warn = opts.warn ?? ((event, details) => {
    // eslint-disable-next-line no-console
    console.warn(`[news-coo:cleaner] ${event}`, details);
  });

  let host = "";
  try {
    host = new URL(url).hostname.toLowerCase();
  } catch {
    warn("invalid_url", { url });
    return summary;
  }

  const cleaners = opts.cleaners ?? CLEANERS;
  for (const cleaner of cleaners) {
    if (!cleaner.matches(host, url)) continue;

    let report: CleanReport;
    try {
      report = cleaner.clean(doc, url);
    } catch (err) {
      warn("cleaner_threw", { cleaner: cleaner.name, host, error: String(err) });
      summary.ranCleaners.push(cleaner.name);
      summary.reports.push({
        name: cleaner.name,
        matched: true,
        removedNodeCount: 0,
        warnings: [`threw: ${String(err)}`],
      });
      continue;
    }

    summary.ranCleaners.push(cleaner.name);
    summary.reports.push({ name: cleaner.name, ...report });
    summary.totalRemoved += report.removedNodeCount;

    if (report.matched && report.removedNodeCount === 0) {
      warn("cleaner_stale", { cleaner: cleaner.name, host });
    }
  }

  return summary;
}
