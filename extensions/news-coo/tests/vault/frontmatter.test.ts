import { describe, expect, it } from "vitest";
import { buildFrontmatter } from "../../src/vault/frontmatter.js";
import type { ExtractedPage } from "../../src/shared/types.js";

const basePage: ExtractedPage = {
  url: "https://example.com/article",
  title: "Sleep and Longevity",
  markdown: "# Sleep and Longevity\n\nContent here.",
  description: "A guide to healthy sleep.",
  author: "Jane Doe",
  published: "2026-01-15",
  imageRefs: [],
};

describe("buildFrontmatter", () => {
  it("includes all required fields", () => {
    const fm = buildFrontmatter(basePage, {
      capturedAt: "2026-05-10T14:32:18+08:00",
    });

    expect(fm).toContain("title:");
    expect(fm).toContain("source_url: https://example.com/article");
    expect(fm).toContain("canonical_url: https://example.com/article");
    expect(fm).toContain("captured_at: 2026-05-10T14:32:18+08:00");
    expect(fm).toContain("source_type: web_document");
    expect(fm).toContain("stage: 1");
    expect(fm).toContain("extraction_method: defuddle");
    expect(fm).toContain("news_coo_version: 1");
  });

  it("wraps with YAML delimiters", () => {
    const fm = buildFrontmatter(basePage);
    expect(fm.startsWith("---\n")).toBe(true);
    expect(fm.trimEnd().endsWith("---")).toBe(true);
  });

  it("includes optional Defuddle fields when present", () => {
    const page: ExtractedPage = {
      ...basePage,
      site: "ExampleBlog",
      wordCount: 1842,
      favicon: "https://example.com/favicon.ico",
    };
    const fm = buildFrontmatter(page);
    expect(fm).toContain("site_name: ExampleBlog");
    expect(fm).toContain("word_count: 1842");
    expect(fm).toContain("favicon: https://example.com/favicon.ico");
  });

  it("omits optional fields when absent", () => {
    const fm = buildFrontmatter(basePage);
    expect(fm).not.toContain("site_name:");
    expect(fm).not.toContain("word_count:");
  });

  it("includes PubMed fields when page has pubmed metadata", () => {
    const page: ExtractedPage = {
      ...basePage,
      pubmed: {
        doi: "10.1234/sleep.2024.001",
        pmid: "38123456",
        journal: "Journal of Sleep Research",
      },
    };
    const fm = buildFrontmatter(page);
    expect(fm).toContain("doi: 10.1234/sleep.2024.001");
    expect(fm).toContain("pmid: 38123456");
    expect(fm).toContain("journal:");
  });

  it("writes highlights when provided", () => {
    const fm = buildFrontmatter(basePage, {
      highlights: [{ text: "Key passage", offset: 512 }],
    });
    expect(fm).toContain("highlights:");
    expect(fm).toContain('"Key passage"');
    expect(fm).toContain("offset: 512");
  });

  it("writes empty highlights array when none", () => {
    const fm = buildFrontmatter(basePage);
    expect(fm).toContain("highlights: []");
  });

  it("escapes quotes in title", () => {
    const page: ExtractedPage = { ...basePage, title: 'It\'s a "test"' };
    const fm = buildFrontmatter(page);
    expect(fm).toContain('\\"test\\"');
  });

  it("defaults lang to en when language not set", () => {
    const fm = buildFrontmatter(basePage);
    expect(fm).toContain("lang: en");
  });

  it("uses page language when set", () => {
    const page: ExtractedPage = { ...basePage, language: "zh" };
    const fm = buildFrontmatter(page);
    expect(fm).toContain("lang: zh");
  });
});
