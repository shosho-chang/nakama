import { describe, expect, it } from "vitest";
import { slugify, slugifyUnique } from "../../src/vault/slug.js";

describe("slugify", () => {
  it("lowercases ASCII title", () => {
    expect(slugify("Hello World")).toBe("hello-world");
  });

  it("replaces non-word chars with hyphens", () => {
    expect(slugify("Sleep: The Ultimate Guide!")).toBe(
      "sleep-the-ultimate-guide",
    );
  });

  it("collapses multiple hyphens", () => {
    expect(slugify("A  &  B")).toBe("a-b");
  });

  it("trims leading and trailing hyphens", () => {
    expect(slugify("  leading and trailing  ")).toBe("leading-and-trailing");
  });

  it("preserves CJK characters", () => {
    expect(slugify("睡眠の重要性")).toBe("睡眠の重要性");
  });

  it("preserves Korean characters", () => {
    expect(slugify("수면의 중요성")).toBe("수면의-중요성");
  });

  it("truncates to 80 characters", () => {
    const long = "a".repeat(100);
    expect(slugify(long).length).toBeLessThanOrEqual(80);
  });

  it("falls back to 'untitled' for empty input", () => {
    expect(slugify("")).toBe("untitled");
    expect(slugify("---")).toBe("untitled");
  });
});

describe("slugifyUnique", () => {
  it("returns base slug when not taken", async () => {
    const slug = await slugifyUnique("Test Article", async () => false);
    expect(slug).toBe("test-article");
  });

  it("appends -2 when base slug is taken", async () => {
    const taken = new Set(["test-article"]);
    const slug = await slugifyUnique("Test Article", async (s) =>
      taken.has(s),
    );
    expect(slug).toBe("test-article-2");
  });

  it("increments suffix until free", async () => {
    const taken = new Set(["test-article", "test-article-2", "test-article-3"]);
    const slug = await slugifyUnique("Test Article", async (s) =>
      taken.has(s),
    );
    expect(slug).toBe("test-article-4");
  });
});
