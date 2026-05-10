// Shared types between popup / options / background / content.

export interface NewsCooVersion {
  readonly version: 1;
}

export const NEWS_COO_VERSION: NewsCooVersion = { version: 1 };

export interface ImageRef {
  src: string;
  alt: string;
  title?: string;
}

export interface PubMedMetadata {
  doi?: string;
  pmid?: string;
  journal?: string;
}

export interface ExtractedPage {
  url: string;
  title: string;
  markdown: string;
  description: string;
  author: string;
  published: string;
  imageRefs: ImageRef[];
  pubmed?: PubMedMetadata;
}
