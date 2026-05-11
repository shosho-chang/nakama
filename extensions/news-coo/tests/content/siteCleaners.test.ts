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

  it("replaces dropBlock with sup; falls back to #fn:N when no DOI is resolvable", () => {
    const doc = makeDoc(`
      <body>
        <div role="paragraph">Hantaan virus was discovered in 1976,<span class="dropBlock reference-citations"><a class="reference-citations__ctrl"><sup>1,2</sup></a><div class="dropBlock__holder"><div>Lee, HW. Isolation of the etiologic agent...</div></div></span> and then several other hantaviruses...</div>
      </body>
    `);
    const report = lancetCleaner.clean(doc, "https://www.thelancet.com/x");
    expect(report.removedNodeCount).toBe(1);
    expect(doc.querySelectorAll(".dropBlock.reference-citations").length).toBe(0);
    // No external link table → in-doc fallback
    const supAnchors = Array.from(doc.querySelectorAll("sup a"));
    expect(supAnchors.map((a) => a.getAttribute("href"))).toEqual(["#fn:1", "#fn:2"]);
    expect(supAnchors.map((a) => a.textContent)).toEqual(["1", "2"]);
    // Reference body text gone from prose
    expect(doc.body.textContent).not.toContain("Lee, HW");
    // Surrounding prose preserved
    expect(doc.body.textContent).toContain("Hantaan virus was discovered in 1976");
    expect(doc.body.textContent).toContain("and then several other hantaviruses");
  });

  it("attaches DOI/PubMed URL directly to body sup when refs section provides external links", () => {
    const doc = makeDoc(`
      <body>
        <div role="paragraph">x<span class="dropBlock reference-citations"><a class="reference-citations__ctrl"><sup>1,3</sup></a><div class="dropBlock__holder"></div></span>y</div>
        <section id="references">
          <h2>References</h2>
          <div><div role="list">
            <div role="listitem">
              <div class="citations">
                <div class="citation-content"><div>Lee, HW</div></div>
                <div class="external-links">
                  <div class="core-xlink-crossref"><a href="https://doi.org/10.1093/ref-1">Crossref</a></div>
                </div>
              </div>
            </div>
            <div role="listitem">
              <div class="citations">
                <div class="citation-content"><div>filler</div></div>
              </div>
            </div>
            <div role="listitem">
              <div class="citations">
                <div class="citation-content"><div>Brock, J</div></div>
                <div class="external-links">
                  <div class="core-xlink-pubmed"><a href="https://pubmed.ncbi.nlm.nih.gov/3/">PubMed</a></div>
                </div>
              </div>
            </div>
          </div></div>
        </section>
      </body>
    `);
    lancetCleaner.clean(doc, "https://www.thelancet.com/x");
    const supAnchors = Array.from(doc.querySelectorAll("sup a"));
    expect(supAnchors.map((a) => a.getAttribute("href"))).toEqual([
      "https://doi.org/10.1093/ref-1",
      "https://pubmed.ncbi.nlm.nih.gov/3/",
    ]);
    expect(supAnchors.every((a) => a.getAttribute("target") === "_blank")).toBe(true);
  });

  it("prefers Crossref → PubMed → Scholar in that order", () => {
    const doc = makeDoc(`
      <body>
        <div role="paragraph">x<span class="dropBlock reference-citations"><sup>1</sup></span></div>
        <section id="references"><div><div role="list"><div role="listitem"><div class="citations">
          <div class="external-links">
            <div class="core-xlink-google-scholar"><a href="https://scholar/x">Scholar</a></div>
            <div class="core-xlink-pubmed"><a href="https://pubmed/x">PubMed</a></div>
            <div class="core-xlink-crossref"><a href="https://doi.org/x">Crossref</a></div>
          </div>
        </div></div></div></div></section>
      </body>
    `);
    lancetCleaner.clean(doc, "https://www.thelancet.com/x");
    expect(doc.querySelector("sup a")?.getAttribute("href")).toBe("https://doi.org/x");
  });

  it("falls back to plain remove when dropBlock has no <sup>", () => {
    const doc = makeDoc(`<body><p>x<div class="dropBlock reference-citations">orphan</div>y</p></body>`);
    lancetCleaner.clean(doc, "https://www.thelancet.com/x");
    expect(doc.querySelectorAll(".dropBlock.reference-citations").length).toBe(0);
    expect(doc.body.textContent).not.toContain("orphan");
  });

  it("rewrites bottom section#references ARIA list into <ol>/<li>", () => {
    const doc = makeDoc(`
      <body>
        <article>
          <p>body</p>
          <section id="references" role="list-container">
            <h2>References</h2>
            <div>
              <div role="list">
                <div role="listitem">
                  <div class="citations">
                    <div class="citation-content">
                      <div class="label"><a>1.</a></div>
                      <div>Lee, HW</div>
                      <div><strong>Isolation of the etiologic agent</strong></div>
                    </div>
                  </div>
                </div>
                <div role="listitem">
                  <div class="citations">
                    <div class="citation-content">
                      <div class="label"><a>2.</a></div>
                      <div>Vaheri, A</div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </section>
        </article>
      </body>
    `);
    lancetCleaner.clean(doc, "https://www.thelancet.com/x");
    const section = doc.querySelector("section#references")!;
    expect(section.getAttribute("role")).toBeNull();
    expect(section.querySelector("h2")?.textContent).toBe("References");
    const lis = section.querySelectorAll("ol > li");
    expect(lis.length).toBe(2);
    expect(lis[0].id).toBe("ref-1");
    expect(lis[1].id).toBe("ref-2");
    expect(lis[0].textContent).toContain("Lee, HW");
    expect(lis[0].textContent).toContain("Isolation of the etiologic agent");
    // The "View in article" reverse link should be gone
    expect(lis[0].textContent).not.toContain("1.");
    expect(lis[1].textContent).toContain("Vaheri, A");
    // ARIA structure replaced
    expect(section.querySelectorAll('[role="list"]').length).toBe(0);
    expect(section.querySelectorAll('[role="listitem"]').length).toBe(0);
  });

  it("preserves external links (Crossref/PubMed/Scholar) as real anchors in each <li>", () => {
    const doc = makeDoc(`
      <body><article><section id="references">
        <h2>References</h2>
        <div><div role="list">
          <div role="listitem">
            <div class="citations">
              <div class="citation-content">
                <div class="label"><a>1.</a></div>
                <div>Lee, HW</div>
              </div>
              <div class="external-links">
                <div class="core-xlink-crossref"><a href="https://doi.org/10.1093/x">Crossref</a></div>
                <div class="core-xlink-pubmed"><a href="https://pubmed.ncbi.nlm.nih.gov/24670/">PubMed</a></div>
              </div>
            </div>
          </div>
        </div></div>
      </section></article></body>
    `);
    lancetCleaner.clean(doc, "https://www.thelancet.com/x");
    const li = doc.querySelector("section#references ol > li")!;
    const anchors = Array.from(li.querySelectorAll("a"));
    const hrefs = anchors.map((a) => a.getAttribute("href"));
    expect(hrefs).toContain("https://doi.org/10.1093/x");
    expect(hrefs).toContain("https://pubmed.ncbi.nlm.nih.gov/24670/");
    const texts = anchors.map((a) => a.textContent);
    expect(texts).toContain("Crossref");
    expect(texts).toContain("PubMed");
  });

  it("warns (without throwing) when section#references is missing", () => {
    const doc = makeDoc("<body><article>no refs section</article></body>");
    const report = lancetCleaner.clean(doc, "https://www.thelancet.com/x");
    expect(report.warnings).toContain("no section#references found");
  });

  it("returns 0 removed (and triggers stale signal upstream) when page has none", () => {
    const doc = makeDoc("<body><p>plain page</p></body>");
    const report = lancetCleaner.clean(doc, "https://www.thelancet.com/x");
    expect(report.removedNodeCount).toBe(0);
  });
});
