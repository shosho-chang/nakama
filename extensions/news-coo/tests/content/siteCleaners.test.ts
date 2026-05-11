import { describe, expect, it, vi } from "vitest";
import { Window } from "happy-dom";
import { dispatchCleaners } from "../../src/content/siteCleaners/index.js";
import { lancetCleaner } from "../../src/content/siteCleaners/lancet.js";
import type { CleanReport, SiteCleaner } from "../../src/content/siteCleaners/types.js";

function makeDoc(html: string): Document {
  const win = new Window();
  const doc = win.document as unknown as Document;
  doc.documentElement.innerHTML = html;
  return doc;
}

describe("dispatchCleaners", () => {
  it("returns empty summary when skip=true (selection-clip path)", () => {
    const doc = makeDoc("<body><p>x</p></body>");
    const summary = dispatchCleaners(doc, "https://www.thelancet.com/x", { skip: true });
    expect(summary.ranCleaners).toEqual([]);
    expect(summary.totalRemoved).toBe(0);
  });

  it("runs only cleaners whose matches() returns true", () => {
    const a: SiteCleaner = {
      name: "a",
      matches: (h) => h === "a.test",
      clean: () => ({ matched: true, removedNodeCount: 1, warnings: [] }),
    };
    const b: SiteCleaner = {
      name: "b",
      matches: (h) => h === "b.test",
      clean: () => ({ matched: true, removedNodeCount: 5, warnings: [] }),
    };
    const doc = makeDoc("<body></body>");
    const summary = dispatchCleaners(doc, "https://b.test/article", {
      cleaners: [a, b],
    });
    expect(summary.ranCleaners).toEqual(["b"]);
    expect(summary.totalRemoved).toBe(5);
  });

  it("isolates a throwing cleaner — others still run", () => {
    const warn = vi.fn();
    const thrower: SiteCleaner = {
      name: "thrower",
      matches: () => true,
      clean: () => { throw new Error("boom"); },
    };
    const ok: SiteCleaner = {
      name: "ok",
      matches: () => true,
      clean: () => ({ matched: true, removedNodeCount: 2, warnings: [] }),
    };
    const doc = makeDoc("<body></body>");
    const summary = dispatchCleaners(doc, "https://x.test/", {
      cleaners: [thrower, ok],
      warn,
    });
    expect(summary.ranCleaners).toEqual(["thrower", "ok"]);
    expect(summary.totalRemoved).toBe(2);
    expect(warn).toHaveBeenCalledWith("cleaner_threw", expect.objectContaining({ cleaner: "thrower" }));
  });

  it("emits cleaner_stale when matched but removed 0 nodes", () => {
    const warn = vi.fn();
    const stale: SiteCleaner = {
      name: "stale",
      matches: () => true,
      clean: () => ({ matched: true, removedNodeCount: 0, warnings: [] }),
    };
    dispatchCleaners(makeDoc("<body></body>"), "https://x.test/", { cleaners: [stale], warn });
    expect(warn).toHaveBeenCalledWith("cleaner_stale", expect.objectContaining({ cleaner: "stale", host: "x.test" }));
  });

  it("does not emit cleaner_stale when removedNodeCount > 0", () => {
    const warn = vi.fn();
    const c: SiteCleaner = {
      name: "c",
      matches: () => true,
      clean: () => ({ matched: true, removedNodeCount: 1, warnings: [] }),
    };
    dispatchCleaners(makeDoc("<body></body>"), "https://x.test/", { cleaners: [c], warn });
    expect(warn).not.toHaveBeenCalled();
  });

  it("warns on invalid URL and returns empty summary", () => {
    const warn = vi.fn();
    const summary = dispatchCleaners(makeDoc("<body></body>"), "not a url", { warn });
    expect(summary.ranCleaners).toEqual([]);
    expect(warn).toHaveBeenCalledWith("invalid_url", expect.objectContaining({ url: "not a url" }));
  });
});

describe("lancetCleaner", () => {
  it("matches thelancet.com and subdomains", () => {
    expect(lancetCleaner.matches("www.thelancet.com", "https://www.thelancet.com/x")).toBe(true);
    expect(lancetCleaner.matches("thelancet.com", "https://thelancet.com/x")).toBe(true);
    expect(lancetCleaner.matches("oa.thelancet.com", "https://oa.thelancet.com/x")).toBe(true);
  });

  it("does not match unrelated hosts", () => {
    expect(lancetCleaner.matches("nytimes.com", "https://nytimes.com/")).toBe(false);
    expect(lancetCleaner.matches("nature.com", "https://nature.com/")).toBe(false);
  });

  it("removes .dropBlock.reference-citations and .reference-citations__ctrl", () => {
    const doc = makeDoc(`
      <body>
        <p>Hantaan virus was discovered in 1976,
          <sup>1</sup>
          <div class="dropBlock reference-citations">
            <div class="reference-citations__ctrl">close</div>
            <div>Lee, HW. Isolation of the etiologic agent...</div>
          </div>
          and then several other hantaviruses...
        </p>
      </body>
    `);
    const before = doc.querySelectorAll(".dropBlock.reference-citations").length;
    expect(before).toBe(1);
    const report = lancetCleaner.clean(doc, "https://www.thelancet.com/x");
    expect(report.matched).toBe(true);
    expect(report.removedNodeCount).toBeGreaterThanOrEqual(1);
    expect(doc.querySelectorAll(".dropBlock.reference-citations").length).toBe(0);
    expect(doc.querySelectorAll(".reference-citations__ctrl").length).toBe(0);
    // Surrounding prose preserved
    expect(doc.body.textContent).toContain("Hantaan virus was discovered in 1976");
    expect(doc.body.textContent).toContain("and then several other hantaviruses");
  });

  it("returns 0 removed (and triggers stale signal upstream) when page has none", () => {
    const doc = makeDoc("<body><p>plain page</p></body>");
    const report = lancetCleaner.clean(doc, "https://www.thelancet.com/x");
    expect(report.removedNodeCount).toBe(0);
  });
});
