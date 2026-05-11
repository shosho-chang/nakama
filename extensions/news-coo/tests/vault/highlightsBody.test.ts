import { describe, it, expect } from "vitest";
import { buildHighlightsSection } from "../../src/vault/writer.js";
import type { Highlight } from "../../src/vault/frontmatter.js";

describe("buildHighlightsSection", () => {
  it("returns empty string when highlights array is empty", () => {
    expect(buildHighlightsSection([])).toBe("");
  });

  it("includes ## Highlights heading for non-empty array", () => {
    const h: Highlight[] = [{ text: "Key insight", offset: 0 }];
    expect(buildHighlightsSection(h)).toContain("## Highlights");
  });

  it("renders each highlight as a blockquote line", () => {
    const highlights: Highlight[] = [
      { text: "First passage", offset: 10 },
      { text: "Second passage", offset: 120 },
    ];
    const section = buildHighlightsSection(highlights);
    expect(section).toContain("> First passage");
    expect(section).toContain("> Second passage");
  });

  it("produces two highlights separated by blank lines", () => {
    const highlights: Highlight[] = [
      { text: "Alpha", offset: 0 },
      { text: "Beta", offset: 50 },
    ];
    const section = buildHighlightsSection(highlights);
    // Blank line between quote blocks
    expect(section).toMatch(/> Alpha\n\n> Beta/);
  });

  it("single highlight has leading blank line before heading", () => {
    const section = buildHighlightsSection([{ text: "Solo", offset: 0 }]);
    expect(section.startsWith("\n")).toBe(true);
  });
});
