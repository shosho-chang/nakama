import { describe, expect, it, vi } from "vitest";
import { Window } from "happy-dom";
import { dispatchCleaners } from "../../src/content/siteCleaners/index.js";
import { atyponCleaner } from "../../src/content/siteCleaners/atypon.js";
import { natureCleaner } from "../../src/content/siteCleaners/nature.js";
import { jamaCleaner } from "../../src/content/siteCleaners/jama.js";
import { bmjCleaner } from "../../src/content/siteCleaners/bmj.js";
import { sciencedirectCleaner } from "../../src/content/siteCleaners/sciencedirect.js";
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

describe("atyponCleaner", () => {
  it("matches thelancet.com and nejm.org families", () => {
    expect(atyponCleaner.matches("www.thelancet.com", "https://www.thelancet.com/x")).toBe(true);
    expect(atyponCleaner.matches("thelancet.com", "https://thelancet.com/x")).toBe(true);
    expect(atyponCleaner.matches("oa.thelancet.com", "https://oa.thelancet.com/x")).toBe(true);
    expect(atyponCleaner.matches("www.nejm.org", "https://www.nejm.org/x")).toBe(true);
    expect(atyponCleaner.matches("nejm.org", "https://nejm.org/x")).toBe(true);
  });

  it("does not match unrelated hosts", () => {
    expect(atyponCleaner.matches("nytimes.com", "https://nytimes.com/")).toBe(false);
    expect(atyponCleaner.matches("nature.com", "https://nature.com/")).toBe(false);
  });

  it("replaces dropBlock with sup; falls back to #fn:N when no DOI is resolvable", () => {
    const doc = makeDoc(`
      <body>
        <div role="paragraph">Hantaan virus was discovered in 1976,<span class="dropBlock reference-citations"><a class="reference-citations__ctrl"><sup>1,2</sup></a><div class="dropBlock__holder"><div>Lee, HW. Isolation of the etiologic agent...</div></div></span> and then several other hantaviruses...</div>
      </body>
    `);
    const report = atyponCleaner.clean(doc, "https://www.thelancet.com/x");
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
    atyponCleaner.clean(doc, "https://www.thelancet.com/x");
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
    atyponCleaner.clean(doc, "https://www.thelancet.com/x");
    expect(doc.querySelector("sup a")?.getAttribute("href")).toBe("https://doi.org/x");
  });

  it("falls back to plain remove when dropBlock has no <sup>", () => {
    const doc = makeDoc(`<body><p>x<div class="dropBlock reference-citations">orphan</div>y</p></body>`);
    atyponCleaner.clean(doc, "https://www.thelancet.com/x");
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
    atyponCleaner.clean(doc, "https://www.thelancet.com/x");
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
    atyponCleaner.clean(doc, "https://www.thelancet.com/x");
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
    const report = atyponCleaner.clean(doc, "https://www.thelancet.com/x");
    expect(report.warnings.some((w) => w.includes("no references section found"))).toBe(true);
  });

  it("returns 0 removed (and triggers stale signal upstream) when page has none", () => {
    const doc = makeDoc("<body><p>plain page</p></body>");
    const report = atyponCleaner.clean(doc, "https://www.thelancet.com/x");
    expect(report.removedNodeCount).toBe(0);
  });

  it("rewrites NEJM-style sup>a[data-xml-rid] hrefs to resolved DOI URLs", () => {
    // NEJM body markers are plain <sup><a> (no dropBlock), and the bottom
    // section uses .core-reference-list#bibliography with the same
    // role=list/listitem + .external-links shape as Lancet.
    const doc = makeDoc(`
      <body>
        <p>Hibernating myocardium<sup><a href="#core-collateral-r1" data-xml-rid="r1">1</a></sup>.</p>
        <section class="core-reference-list" id="bibliography">
          <h2>References</h2>
          <div role="list">
            <div role="listitem">
              <div class="citations">
                <div class="citation-content">Rahimtoola SH. The hibernating myocardium.</div>
                <div class="external-links">
                  <div class="core-xlink-crossref"><a href="https://doi.org/10.1016/0002-8703(89)90685-6">Crossref</a></div>
                  <div class="core-xlink-pubmed"><a href="https://pubmed.ncbi.nlm.nih.gov/2783527/">PubMed</a></div>
                </div>
              </div>
            </div>
          </div>
        </section>
      </body>
    `);
    atyponCleaner.clean(doc, "https://www.nejm.org/x");
    const supA = doc.querySelector('sup a[data-xml-rid="r1"]')!;
    expect(supA.getAttribute("href")).toBe("https://doi.org/10.1016/0002-8703(89)90685-6");
    expect(supA.getAttribute("target")).toBe("_blank");
    // Bottom list also rewritten as <ol>
    const lis = doc.querySelectorAll("section#bibliography ol > li");
    expect(lis.length).toBe(1);
    expect(lis[0].id).toBe("ref-1");
  });

  it("injects meta[name=author] from dc.Creator meta tags (NEJM)", () => {
    const doc = makeDoc(`
      <head>
        <meta name="dc.Creator" content="Kerry S. Courneya">
        <meta name="dc.Creator" content="Janette L. Vardy">
      </head>
      <body><p>x</p></body>
    `);
    atyponCleaner.clean(doc, "https://www.nejm.org/doi/10.1056/x");
    const meta = doc.querySelector('head meta[name="author"]')!;
    expect(meta.getAttribute("content")).toBe("Kerry S. Courneya, Janette L. Vardy");
  });

  it("falls back to .contributors span[property=author] for author meta", () => {
    const doc = makeDoc(`
      <head></head>
      <body>
        <div class="contributors">
          <span property="author">
            <span property="givenName">Kerry S.</span>
            <span property="familyName">Courneya</span>
          </span>
          <span property="author">
            <span property="givenName">Janette L.</span>
            <span property="familyName">Vardy</span>
          </span>
        </div>
      </body>
    `);
    atyponCleaner.clean(doc, "https://www.nejm.org/doi/10.1056/x");
    const meta = doc.querySelector('head meta[name="author"]')!;
    expect(meta.getAttribute("content")).toBe("Kerry S. Courneya, Janette L. Vardy");
  });
});

describe("natureCleaner", () => {
  it("matches nature.com family", () => {
    expect(natureCleaner.matches("nature.com", "https://nature.com/x")).toBe(true);
    expect(natureCleaner.matches("www.nature.com", "https://www.nature.com/x")).toBe(true);
    expect(natureCleaner.matches("nytimes.com", "https://nytimes.com/x")).toBe(false);
  });

  it("rewrites in-text citation anchors to the DOI from the matching <li>", () => {
    const doc = makeDoc(`
      <body>
        <p>cells age<sup><a href="/articles/x#ref-CR1" data-test="citation-ref" data-track-action="reference anchor">1</a></sup>.</p>
        <ol>
          <li class="c-article-references__item" data-counter="1.">
            <p class="c-article-references__text" id="ref-CR1">Hou Y. Ageing as a risk factor for neurodegenerative disease.</p>
            <p class="c-article-references__links">
              <a data-track-action="article reference" href="https://doi.org/10.1038/s41582-019-0244-7">Article</a>
              <a data-track-action="pubmed reference" href="https://pubmed.ncbi.nlm.nih.gov/x">PubMed</a>
            </p>
          </li>
        </ol>
      </body>
    `);
    natureCleaner.clean(doc, "https://www.nature.com/articles/x");
    const a = doc.querySelector('a[data-test="citation-ref"]')!;
    expect(a.getAttribute("href")).toBe("https://doi.org/10.1038/s41582-019-0244-7");
    expect(a.getAttribute("target")).toBe("_blank");
  });

  it("falls back to PubMed when no Article/DOI link present", () => {
    const doc = makeDoc(`
      <body>
        <p><sup><a href="#ref-CR1" data-test="citation-ref">1</a></sup></p>
        <ol>
          <li class="c-article-references__item">
            <p class="c-article-references__text" id="ref-CR1">x</p>
            <p class="c-article-references__links">
              <a data-track-action="pubmed reference" href="https://pubmed.ncbi.nlm.nih.gov/123/">PubMed</a>
            </p>
          </li>
        </ol>
      </body>
    `);
    natureCleaner.clean(doc, "https://www.nature.com/x");
    expect(doc.querySelector('a[data-test="citation-ref"]')!.getAttribute("href")).toBe(
      "https://pubmed.ncbi.nlm.nih.gov/123/",
    );
  });
});

describe("jamaCleaner", () => {
  it("matches jamanetwork.com", () => {
    expect(jamaCleaner.matches("jamanetwork.com", "https://jamanetwork.com/x")).toBe(true);
    expect(jamaCleaner.matches("www.jamanetwork.com", "https://www.jamanetwork.com/x")).toBe(true);
  });

  it("rewrites body sup ref-link to https://doi.org/<doi> (skipping the jamanetwork redirector)", () => {
    const doc = makeDoc(`
      <body>
        <p>x<sup><a class="ref-link section-jump-link" href="#ajf240001r1">1</a></sup></p>
        <div class="references">
          <div class="reference">
            <a class="reference-number" id="ajf240001r1">1.</a>
            <div class="reference-content">
              Gandhi M, Goosby E. PEPFAR reauthorization. <i>JAMA</i>. 2023;330:1727.
              doi:<a href="http://jamanetwork.com/article.aspx?doi=10.1001/jama.2023.19322">10.1001/jama.2023.19322</a>
            </div>
          </div>
        </div>
      </body>
    `);
    jamaCleaner.clean(doc, "https://jamanetwork.com/x");
    expect(doc.querySelector("sup a.ref-link")!.getAttribute("href")).toBe(
      "https://doi.org/10.1001/jama.2023.19322",
    );
  });

  it("falls back to fullarticle URL when no DOI string present", () => {
    const doc = makeDoc(`
      <body>
        <p><sup><a class="ref-link section-jump-link" href="#r5">5</a></sup></p>
        <div class="references">
          <div class="reference">
            <a class="reference-number" id="r5">5.</a>
            <div class="reference-content">
              x. <a href="https://jamanetwork.com/journals/jama/fullarticle/2809749">Article</a>
            </div>
          </div>
        </div>
      </body>
    `);
    jamaCleaner.clean(doc, "https://jamanetwork.com/x");
    expect(doc.querySelector("sup a.ref-link")!.getAttribute("href")).toBe(
      "https://jamanetwork.com/journals/jama/fullarticle/2809749",
    );
  });
});

describe("bmjCleaner", () => {
  it("matches bmj.com family", () => {
    expect(bmjCleaner.matches("www.bmj.com", "https://www.bmj.com/x")).toBe(true);
    expect(bmjCleaner.matches("bmj.com", "https://bmj.com/x")).toBe(true);
  });

  it("rewrites a.xref-bibr to DOI from data-doi on the matching <li>", () => {
    const doc = makeDoc(`
      <body>
        <p>cancer rates<a class="xref-bibr" href="#ref-1">1</a>.</p>
        <ol class="cit-list">
          <li>
            <a class="rev-xref-ref" href="#xref-ref-1-1" id="ref-1">↵</a>
            <div class="cit ref-cit ref-journal" id="cit-1" data-doi="10.1002/ijc.29210">
              <div class="cit-metadata">Ferlay J. Cancer incidence.</div>
            </div>
          </li>
        </ol>
      </body>
    `);
    bmjCleaner.clean(doc, "https://www.bmj.com/content/360/bmj.k322");
    const a = doc.querySelector("a.xref-bibr")!;
    expect(a.getAttribute("href")).toBe("https://doi.org/10.1002/ijc.29210");
    expect(a.getAttribute("target")).toBe("_blank");
  });

  it("leaves anchor untouched when bottom <li> has no data-doi", () => {
    const doc = makeDoc(`
      <body>
        <p><a class="xref-bibr" href="#ref-1">1</a></p>
        <ol class="cit-list">
          <li><a class="rev-xref-ref" id="ref-1">↵</a><div class="cit">no doi here</div></li>
        </ol>
      </body>
    `);
    bmjCleaner.clean(doc, "https://www.bmj.com/x");
    expect(doc.querySelector("a.xref-bibr")!.getAttribute("href")).toBe("#ref-1");
  });
});

describe("sciencedirectCleaner", () => {
  it("matches sciencedirect.com", () => {
    expect(sciencedirectCleaner.matches("www.sciencedirect.com", "https://www.sciencedirect.com/x")).toBe(true);
    expect(sciencedirectCleaner.matches("sciencedirect.com", "https://sciencedirect.com/x")).toBe(true);
    expect(sciencedirectCleaner.matches("nature.com", "https://nature.com/x")).toBe(false);
  });

  it("replaces body anchor with flat <sup><a> pointing at DOI from ol.references", () => {
    const doc = makeDoc(`
      <head></head>
      <body>
        <p>Aging is<a class="anchor" href="#bib1" data-xocs-content-type="reference" data-xocs-content-id="bib1"><span><span><sup>1</sup></span></span></a>.</p>
        <ol class="references">
          <li>
            <span class="label"><a class="anchor" href="#bbib1" id="ref-id-bib1">1</a></span>
            <span class="reference" id="sref1">
              <div class="contribution">Kroemer et al. From geroscience.</div>
              <div class="host">Cell, 188 (2025),
                <a class="anchor" href="https://doi.org/10.1016/j.cell.2025.03.011">DOI</a>
              </div>
            </span>
          </li>
        </ol>
      </body>
    `);
    const report = sciencedirectCleaner.clean(doc, "https://www.sciencedirect.com/science/article/pii/X");
    const sup = doc.querySelector("p sup")!;
    const a = sup.querySelector("a")!;
    expect(a.getAttribute("href")).toBe("https://doi.org/10.1016/j.cell.2025.03.011");
    expect(a.getAttribute("target")).toBe("_blank");
    expect(a.textContent).toBe("1");
    expect(report.removedNodeCount).toBeGreaterThan(0);
  });

  it("falls back to pii link when no DOI is present", () => {
    const doc = makeDoc(`
      <head></head>
      <body>
        <p><a class="anchor" href="#bib2" data-xocs-content-type="reference" data-xocs-content-id="bib2"><sup>2</sup></a></p>
        <ol class="references">
          <li>
            <span class="label"><a class="anchor" href="#bbib2" id="ref-id-bib2">2</a></span>
            <span class="reference">
              <a href="/science/article/pii/S0092867423008577/pdfft?md5=x">PDF</a>
              <a href="/science/article/pii/S0092867423008577">Article</a>
            </span>
          </li>
        </ol>
      </body>
    `);
    sciencedirectCleaner.clean(doc, "https://www.sciencedirect.com/x");
    const a = doc.querySelector("p sup a")!;
    expect(a.getAttribute("href")).toBe(
      "https://www.sciencedirect.com/science/article/pii/S0092867423008577",
    );
  });

  it("rebuilds ol.references as ol#references with citation text + paper link", () => {
    const doc = makeDoc(`
      <head></head>
      <body>
        <ol class="references">
          <li>
            <span class="label"><a id="ref-id-bib1">1</a></span>
            <span class="reference">
              <div class="contribution">Kroemer et al. From geroscience.</div>
              <div class="host">Cell, 188 (2025), <a href="https://doi.org/10.1016/j.cell.2025.03.011">DOI</a></div>
            </span>
          </li>
        </ol>
      </body>
    `);
    sciencedirectCleaner.clean(doc, "https://www.sciencedirect.com/x");
    expect(doc.querySelector("ol.references")).toBeNull();
    const li = doc.querySelector("ol#references > li#ref-1")!;
    expect(li.textContent).toContain("Kroemer et al. From geroscience.");
    const paperA = li.querySelector("a")!;
    expect(paperA.getAttribute("href")).toBe("https://doi.org/10.1016/j.cell.2025.03.011");
  });

  it("injects <meta name='author'> from #author-group", () => {
    const doc = makeDoc(`
      <head></head>
      <body>
        <div id="author-group">
          <button data-xocs-content-type="author">
            <span class="given-name">Jiaming</span> <span class="surname">Li</span>
            <span class="author-ref"><sup>1</sup></span>
          </button>,
          <button data-xocs-content-type="author">
            <span class="given-name">Beier</span> <span class="surname">Jiang</span>
            <span class="author-ref"><sup>4</sup></span>
          </button>
        </div>
      </body>
    `);
    sciencedirectCleaner.clean(doc, "https://www.sciencedirect.com/x");
    const meta = doc.querySelector('head meta[name="author"]')!;
    expect(meta.getAttribute("content")).toBe("Jiaming Li, Beier Jiang");
  });

  it("warns when no references list is present", () => {
    const doc = makeDoc(`<body><p>nothing</p></body>`);
    const report = sciencedirectCleaner.clean(doc, "https://www.sciencedirect.com/x");
    expect(report.warnings.some((w) => w.includes("no ol.references"))).toBe(true);
  });
});
