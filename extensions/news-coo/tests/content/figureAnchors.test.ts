import { describe, expect, it } from "vitest";
import { Window } from "happy-dom";
import {
  tagFigureBlockIds,
  rewriteFigureAnchors,
  wireFigureBlockIds,
} from "../../src/content/siteCleaners/figureAnchors.js";

function makeDoc(html: string): Document {
  const win = new Window();
  const doc = win.document as unknown as Document;
  doc.documentElement.innerHTML = html;
  return doc;
}

describe("tagFigureBlockIds", () => {
  it("appends ^id to figcaption text and returns raw→block map (default extractor)", () => {
    const doc = makeDoc(`
      <body>
        <figure id="fig1">
          <figcaption>caption A</figcaption>
        </figure>
        <figure id="fig2">
          <figcaption>caption B</figcaption>
        </figure>
      </body>
    `);
    const map = tagFigureBlockIds(doc);
    expect(map.get("fig1")).toBe("fig1");
    expect(map.get("fig2")).toBe("fig2");
    expect(doc.querySelectorAll("figcaption")[0].textContent).toContain("^fig1");
    expect(doc.querySelectorAll("figcaption")[1].textContent).toContain("^fig2");
  });

  it("uses a custom extractId when figure id is on an inner element", () => {
    const doc = makeDoc(`
      <body>
        <figure>
          <figcaption><b id="Fig3">Fig. 3:</b> caption</figcaption>
        </figure>
      </body>
    `);
    const map = tagFigureBlockIds(doc, {
      extractId: (fig) =>
        fig.querySelector<HTMLElement>("figcaption [id]")?.getAttribute("id") ?? null,
    });
    expect(map.get("Fig3")).toBe("Fig3");
    expect(doc.querySelector("figcaption")!.textContent).toContain("^Fig3");
  });

  it("sanitises raw ids with disallowed chars into valid Obsidian block IDs", () => {
    const doc = makeDoc(`
      <body>
        <figure id="fig:10.1038/x.1">
          <figcaption>caption</figcaption>
        </figure>
      </body>
    `);
    const map = tagFigureBlockIds(doc);
    const blockId = map.get("fig:10.1038/x.1");
    expect(blockId).toBe("fig-10-1038-x-1");
    expect(doc.querySelector("figcaption")!.textContent).toContain(
      "^fig-10-1038-x-1",
    );
  });

  it("skips figures without an extractable id or without a caption", () => {
    const doc = makeDoc(`
      <body>
        <figure><figcaption>no id</figcaption></figure>
        <figure id="fig5"><!-- no caption --></figure>
      </body>
    `);
    const map = tagFigureBlockIds(doc);
    expect(map.size).toBe(0);
  });

  it("honours custom figureSelector / captionSelector (BMJ-style)", () => {
    const doc = makeDoc(`
      <body>
        <div class="fig" id="F1">
          <div class="caption">A figure</div>
        </div>
      </body>
    `);
    const map = tagFigureBlockIds(doc, {
      figureSelector: "div.fig",
      captionSelector: ".caption",
    });
    expect(map.get("F1")).toBe("F1");
    expect(doc.querySelector(".caption")!.textContent).toContain("^F1");
  });
});

describe("rewriteFigureAnchors", () => {
  it("rewrites <a href> whose fragment matches a collected raw id", () => {
    const doc = makeDoc(`
      <body>
        <p>see <a href="https://example.com/x#fig1">Fig. 1</a></p>
        <p>and <a href="/x#fig2">Fig. 2</a></p>
      </body>
    `);
    const rewritten = rewriteFigureAnchors(
      doc,
      new Map([
        ["fig1", "fig1"],
        ["fig2", "fig2"],
      ]),
    );
    expect(rewritten).toBe(2);
    const hrefs = Array.from(doc.querySelectorAll("a")).map((a) =>
      a.getAttribute("href"),
    );
    expect(hrefs).toEqual(["#^fig1", "#^fig2"]);
  });

  it("leaves anchors with unrelated fragments alone", () => {
    const doc = makeDoc(`
      <body>
        <a href="#section-1">jump</a>
        <a href="https://example.com/x#fig9">fig 9 not collected</a>
      </body>
    `);
    const rewritten = rewriteFigureAnchors(doc, new Map([["fig1", "fig1"]]));
    expect(rewritten).toBe(0);
  });

  it("uses block id (not raw id) in the rewritten href when sanitisation changed it", () => {
    const doc = makeDoc(`
      <body>
        <a href="#fig:10/x">see fig</a>
      </body>
    `);
    const rewritten = rewriteFigureAnchors(
      doc,
      new Map([["fig:10/x", "fig-10-x"]]),
    );
    expect(rewritten).toBe(1);
    expect(doc.querySelector("a")!.getAttribute("href")).toBe("#^fig-10-x");
  });

  it("strips tracking attrs on rewritten anchors", () => {
    const doc = makeDoc(`
      <body>
        <a href="#fig1" data-track="click" data-track-action="figure anchor">see</a>
      </body>
    `);
    rewriteFigureAnchors(doc, new Map([["fig1", "fig1"]]));
    const a = doc.querySelector("a")!;
    expect(a.getAttribute("data-track")).toBeNull();
    expect(a.getAttribute("data-track-action")).toBeNull();
  });
});

describe("wireFigureBlockIds (tag + rewrite)", () => {
  it("end-to-end: collects ids and rewrites matching anchors", () => {
    const doc = makeDoc(`
      <body>
        <p>see <a href="https://example.com/x#fig1">Fig. 1</a></p>
        <figure id="fig1"><figcaption>Caption.</figcaption></figure>
      </body>
    `);
    const result = wireFigureBlockIds(doc);
    expect(result.taggedCount).toBe(1);
    expect(result.rewrittenCount).toBe(1);
    expect(doc.querySelector("a")!.getAttribute("href")).toBe("#^fig1");
    expect(doc.querySelector("figcaption")!.textContent).toContain("^fig1");
  });
});
