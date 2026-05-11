import { describe, it, expect } from "vitest";
import { extractPage } from "../../src/content/extract.js";

describe("extractPage — selection-aware path", () => {
  it("extracts markdown from a selection fragment instead of full document", () => {
    const fullDoc = new DOMParser().parseFromString(
      `<html><head><title>Full Page</title></head>
       <body>
         <article>
           <h1>Full Page Article</h1>
           <p>This is the full page body that should NOT appear.</p>
           <p id="sel">This is the selected passage.</p>
         </article>
       </body></html>`,
      "text/html",
    );

    const selectionHtml = "<p>This is the selected passage.</p>";
    const page = extractPage(fullDoc, "https://example.com/article", selectionHtml);

    expect(page.markdown).toContain("selected passage");
    // Full page content must not bleed into the selection extraction
    expect(page.markdown).not.toContain("Full Page Article");
  });

  it("falls back to document title when selection fragment has no title", () => {
    const fullDoc = new DOMParser().parseFromString(
      `<html><head><title>Original Title</title></head>
       <body><article><p>Selected text.</p></article></body></html>`,
      "text/html",
    );
    const page = extractPage(fullDoc, "https://example.com/", "<p>Selected text.</p>");
    expect(page.title).toBe("Original Title");
  });

  it("without selectionHtml extracts from full document", () => {
    const doc = new DOMParser().parseFromString(
      `<html><head><title>Full Article</title></head>
       <body><article><h1>Full Article</h1><p>Body text here.</p></article></body></html>`,
      "text/html",
    );
    const page = extractPage(doc, "https://example.com/full");
    // Defuddle promotes h1 to title; body paragraph appears in markdown.
    expect(page.markdown).toContain("Body text here.");
    expect(page.title).toBe("Full Article");
  });

  it("returns empty imageRefs when selection fragment contains no images", () => {
    const doc = new DOMParser().parseFromString(
      `<html><head><title>T</title></head>
       <body><article><img src="photo.jpg" alt="Photo"><p>Text</p></article></body></html>`,
      "text/html",
    );
    const page = extractPage(doc, "https://example.com/", "<p>Text only</p>");
    expect(page.imageRefs).toHaveLength(0);
  });
});
