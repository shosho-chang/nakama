import type { PubMedMetadata } from "../shared/types.js";

const PUBMED_URL_RE =
  /(?:pubmed\.ncbi\.nlm\.nih\.gov|ncbi\.nlm\.nih\.gov\/pmc\/)/;

export function isPubMedUrl(url: string): boolean {
  return PUBMED_URL_RE.test(url);
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
