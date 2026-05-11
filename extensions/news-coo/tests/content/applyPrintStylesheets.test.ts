import { describe, expect, it } from "vitest";
import { Window } from "happy-dom";
import { applyPrintStylesheets, removeHiddenByPrint } from "../../src/content/applyPrintStylesheets.js";

function makeDoc(html: string): Document {
  const win = new Window();
  const doc = win.document as unknown as Document;
  doc.documentElement.innerHTML = html;
  return doc;
}

describe("applyPrintStylesheets", () => {
  it("switches link[media=print] to media=all", () => {
    const doc = makeDoc(`
      <head>
        <link rel="stylesheet" media="print" href="/print.css">
        <link rel="stylesheet" media="screen" href="/screen.css">
      </head>
      <body><p>x</p></body>
    `);
    const report = applyPrintStylesheets(doc);
    expect(report.stylesheetsTouched).toBe(1);
    const printLink = doc.querySelector('link[href="/print.css"]')!;
    const screenLink = doc.querySelector('link[href="/screen.css"]')!;
    expect(printLink.getAttribute("media")).toBe("all");
    expect(screenLink.getAttribute("media")).toBe("screen");
  });

  it("switches link with comma-separated media including print", () => {
    const doc = makeDoc(`
      <head>
        <link rel="stylesheet" media="print, projection" href="/x.css">
      </head>
      <body></body>
    `);
    applyPrintStylesheets(doc);
    expect(doc.querySelector("link")!.getAttribute("media")).toBe("all");
  });

  it("switches style[media=print]", () => {
    const doc = makeDoc(`
      <head><style media="print">.cite{display:none}</style></head>
      <body></body>
    `);
    const report = applyPrintStylesheets(doc);
    expect(report.stylesheetsTouched).toBe(1);
    expect(doc.querySelector("style")!.getAttribute("media")).toBe("all");
  });

  it("rewrites @media print {} blocks inside style tags", () => {
    const doc = makeDoc(`
      <head><style>
        body { color: black; }
        @media print {
          .reference-citations { display: none; }
          .share-widget { display: none; }
        }
      </style></head>
      <body></body>
    `);
    const report = applyPrintStylesheets(doc);
    expect(report.inlinePrintBlocks).toBe(1);
    const css = doc.querySelector("style")!.textContent!;
    expect(css).toMatch(/@media all\s*\{/);
    expect(css).toContain(".reference-citations");
  });

  it("preserves other media in compound @media rules", () => {
    const doc = makeDoc(`
      <head><style>
        @media print, projection { .x { display: none; } }
      </style></head>
      <body></body>
    `);
    applyPrintStylesheets(doc);
    const css = doc.querySelector("style")!.textContent!;
    expect(css).toMatch(/@media\s+projection\s*\{/);
    expect(css).not.toMatch(/\bprint\b/i);
  });

  it("returns zero touched when no print stylesheets present", () => {
    const doc = makeDoc(`<head></head><body><p>plain</p></body>`);
    const report = applyPrintStylesheets(doc);
    expect(report.stylesheetsTouched).toBe(0);
    expect(report.inlinePrintBlocks).toBe(0);
    expect(report.warnings).toEqual([]);
  });
});

describe("removeHiddenByPrint", () => {
  it("returns 0 when document has no defaultView", () => {
    // Constructed via doc.implementation.createHTMLDocument has a defaultView
    // in happy-dom — to test the no-view branch we mock it.
    const win = new Window();
    const doc = win.document as unknown as Document;
    Object.defineProperty(doc, "defaultView", { value: null, configurable: true });
    expect(removeHiddenByPrint(doc)).toBe(0);
  });
});
