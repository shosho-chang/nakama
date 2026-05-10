import { describe, expect, it } from "vitest";
import { NEWS_COO_VERSION } from "./types";

describe("NEWS_COO_VERSION", () => {
  it("locks frontmatter version to 1 for S1 skeleton", () => {
    expect(NEWS_COO_VERSION.version).toBe(1);
  });
});
