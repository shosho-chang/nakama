import type { PubMedMetadata } from "../shared/types.js";

export function isPubMedUrl(url: string): boolean {
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    return false;
  }
  if (parsed.hostname === "pubmed.ncbi.nlm.nih.gov") return true;
  if (
    (parsed.hostname === "www.ncbi.nlm.nih.gov" ||
      parsed.hostname === "ncbi.nlm.nih.gov") &&
    parsed.pathname.startsWith("/pmc/")
  )
    return true;
  return false;
}

export function extractPubMedMetadata(doc: Document): PubMedMetadata {
  const result: PubMedMetadata = {};

  const doi = doc.querySelector<HTMLMetaElement>(
    'meta[name="citation_doi"]',
  )?.content;
  if (doi) result.doi = doi;

  const pmid = doc.querySelector<HTMLMetaElement>(
    'meta[name="citation_pmid"]',
  )?.content;
  if (pmid) result.pmid = pmid;

  const journal = doc.querySelector<HTMLMetaElement>(
    'meta[name="citation_journal_title"]',
  )?.content;
  if (journal) result.journal = journal;

  return result;
}
