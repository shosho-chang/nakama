// Frontmatter assembler per PRD §5.2.

import type { ExtractedPage } from "../shared/types.js";

export interface Highlight {
  text: string;
  offset: number;
}

export interface FrontmatterOptions {
  capturedAt?: string;
  selectionOnly?: boolean;
  highlights?: Highlight[];
  imagesPartial?: boolean;
  imagesCount?: number;
}

function yamlStr(v: string): string {
  const escaped = v
    .replace(/\\/g, "\\\\")
    .replace(/"/g, '\\"')
    .replace(/\r?\n/g, "\\n");
  return `"${escaped}"`;
}

function field(key: string, value: string): string {
  // Values that are safe as bare YAML scalars
  if (/^[\w./:@+-]+$/.test(value)) return `${key}: ${value}`;
  return `${key}: ${yamlStr(value)}`;
}

export function buildFrontmatter(
  page: ExtractedPage,
  opts: FrontmatterOptions = {},
): string {
  const capturedAt = opts.capturedAt ?? new Date().toISOString();
  const lines: string[] = ["---"];

  // Required
  lines.push(field("title", page.title || "Untitled"));
  lines.push(field("source_url", page.url));
  lines.push(field("canonical_url", page.url));
  lines.push(field("captured_at", capturedAt));
  lines.push("source_type: web_document");
  lines.push("stage: 1");
  lines.push(field("lang", page.language ?? "en"));
  lines.push("extraction_method: defuddle");
  lines.push("news_coo_version: 1");

  // Optional Defuddle fields
  if (page.site) lines.push(field("site_name", page.site));
  if (page.author) lines.push(field("author", page.author));
  if (page.published) lines.push(field("published", page.published));
  if (page.description) lines.push(field("description", page.description));
  if (page.wordCount) lines.push(`word_count: ${page.wordCount}`);
  if (page.favicon) lines.push(field("favicon", page.favicon));

  // News Coo extensions
  lines.push(`selection_only: ${opts.selectionOnly ?? false}`);
  const highlights = opts.highlights ?? [];
  if (highlights.length > 0) {
    lines.push("highlights:");
    for (const h of highlights) {
      lines.push(`  - text: ${yamlStr(h.text)}`);
      lines.push(`    offset: ${h.offset}`);
    }
  } else {
    lines.push("highlights: []");
  }
  lines.push(`images_partial: ${opts.imagesPartial ?? false}`);
  lines.push(`images_count: ${opts.imagesCount ?? page.imageRefs.length}`);

  // PubMed
  if (page.pubmed?.doi) lines.push(field("doi", page.pubmed.doi));
  if (page.pubmed?.pmid) lines.push(field("pmid", page.pubmed.pmid));
  if (page.pubmed?.journal) lines.push(field("journal", page.pubmed.journal));

  lines.push("---");
  return lines.join("\n") + "\n";
}
