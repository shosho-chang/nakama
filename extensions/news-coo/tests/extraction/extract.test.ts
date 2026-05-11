import { readFileSync } from "fs";
import { join } from "path";
import { describe, expect, it } from "vitest";
import { extractPage } from "../../src/content/extract.js";

const FIXTURES = join(__dirname, "../fixtures");

function loadFixture(name: string): Document {
  const html = readFileSync(join(FIXTURES, name), "utf-8");
  return new DOMParser().parseFromString(html, "text/html");
}

describe("extractPage", () => {
  it("returns ExtractedPage shape from blog fixture", () => {
    const doc = loadFixture("sample-blog.html");
    const page = extractPage(doc, "https://example.com/blog/sleep");

    expect(page.url).toBe("https://example.com/blog/sleep");
    expect(typeof page.title).toBe("string");
    expect(typeof page.markdown).toBe("string");
    expect(page.markdown.length).toBeGreaterThan(0);
    expect(Array.isArray(page.imageRefs)).toBe(true);
    expect(page.pubmed).toBeUndefined();
  });

  it("extracts image refs with src, alt, and title", () => {
    const doc = loadFixture("sample-blog.html");
    const page = extractPage(doc, "https://example.com/blog/sleep");

    const imgRef = page.imageRefs.find((r) =>
      r.src.includes("sleep-cycle.png"),
    );
    expect(imgRef).toBeDefined();
    expect(imgRef?.alt).toBe("Sleep cycle diagram");
    expect(imgRef?.title).toBe("Sleep Cycle");
  });

  it("returns empty imageRefs when no images in content", () => {
    const doc = new DOMParser().parseFromString(
      `<html><body><article><h1>No Images</h1><p>Text only content here.</p></article></body></html>`,
      "text/html",
    );
    const page = extractPage(doc, "https://example.com/no-images");
    expect(page.imageRefs).toHaveLength(0);
  });

  it("normalizes relative image URLs against the page URL", () => {
    const doc = new DOMParser().parseFromString(
      `<html><body><article><h1>Relative</h1><p>Content.</p><img src="/img/photo.jpg" alt="Photo"></article></body></html>`,
      "text/html",
    );
    const page = extractPage(doc, "https://example.com/article");
    const imgRef = page.imageRefs.find((r) => r.src.includes("photo.jpg"));
    expect(imgRef?.src).toBe("https://example.com/img/photo.jpg");
  });
});
