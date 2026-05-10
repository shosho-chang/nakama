import { readFileSync } from "fs";
import { join } from "path";
import { describe, expect, it } from "vitest";
import {
  extractPubMedMetadata,
  isPubMedUrl,
} from "../../src/content/pubmedDetector.js";

const FIXTURES = join(__dirname, "../fixtures");

function loadFixture(name: string): Document {
  const html = readFileSync(join(FIXTURES, name), "utf-8");
  return new DOMParser().parseFromString(html, "text/html");
}

describe("isPubMedUrl", () => {
  it("matches pubmed.ncbi.nlm.nih.gov", () => {
    expect(
      isPubMedUrl("https://pubmed.ncbi.nlm.nih.gov/38123456/"),
    ).toBe(true);
  });

  it("matches ncbi.nlm.nih.gov/pmc/", () => {
    expect(
      isPubMedUrl("https://ncbi.nlm.nih.gov/pmc/articles/PMC1234567/"),
    ).toBe(true);
  });

  it("does not match non-PubMed NCBI URLs", () => {
    expect(isPubMedUrl("https://ncbi.nlm.nih.gov/gene/")).toBe(false);
  });

  it("does not match unrelated sites", () => {
    expect(isPubMedUrl("https://example.com/paper")).toBe(false);
  });
});

describe("extractPubMedMetadata", () => {
  it("extracts doi, pmid, and journal from citation meta tags", () => {
    const doc = loadFixture("sample-pubmed.html");
    const meta = extractPubMedMetadata(doc);

    expect(meta.doi).toBe("10.1234/sleep.2024.001");
    expect(meta.pmid).toBe("38123456");
    expect(meta.journal).toBe("Journal of Sleep Research");
  });

  it("returns empty object when no PubMed meta tags are present", () => {
    const doc = loadFixture("sample-blog.html");
    const meta = extractPubMedMetadata(doc);

    expect(meta.doi).toBeUndefined();
    expect(meta.pmid).toBeUndefined();
    expect(meta.journal).toBeUndefined();
  });

  it("returns only fields that are present", () => {
    const doc = new DOMParser().parseFromString(
      `<html><head>
        <meta name="citation_doi" content="10.9999/test">
      </head><body></body></html>`,
      "text/html",
    );
    const meta = extractPubMedMetadata(doc);

    expect(meta.doi).toBe("10.9999/test");
    expect(meta.pmid).toBeUndefined();
    expect(meta.journal).toBeUndefined();
  });
});
