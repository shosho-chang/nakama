// FSA vault writer — writes Inbox/kb/{slug}.md (PRD §5.1).

import type { ExtractedPage } from "../shared/types.js";
import { buildFrontmatter } from "./frontmatter.js";
import type { FrontmatterOptions, Highlight } from "./frontmatter.js";
import { fetchAndRewriteImages } from "./imageFetcher.js";
import { slugify } from "./slug.js";

export function buildHighlightsSection(highlights: Highlight[]): string {
  if (highlights.length === 0) return "";
  const lines = ["", "## Highlights", ""];
  for (const h of highlights) {
    lines.push(`> ${h.text}`, "");
  }
  return lines.join("\n");
}

export interface WriteResult {
  slug: string;
  path: string;
}

async function fileExists(
  dir: FileSystemDirectoryHandle,
  name: string,
): Promise<boolean> {
  try {
    await dir.getFileHandle(name);
    return true;
  } catch {
    return false;
  }
}

export async function checkSlugExists(
  root: FileSystemDirectoryHandle,
  slug: string,
): Promise<boolean> {
  return fileExists(root, `${slug}.md`);
}

export async function writeToVaultExact(
  root: FileSystemDirectoryHandle,
  slug: string,
  content: string,
): Promise<WriteResult> {
  const fileHandle = await root.getFileHandle(`${slug}.md`, { create: true });
  const writable = await fileHandle.createWritable();
  await writable.write(content);
  await writable.close();
  return { slug, path: `${slug}.md` };
}

export async function writeToVault(
  root: FileSystemDirectoryHandle,
  slug: string,
  content: string,
): Promise<WriteResult> {
  // Collision detection: auto-suffix until free slot.
  let finalSlug = slug;
  let suffix = 2;
  while (await fileExists(root, `${finalSlug}.md`)) {
    finalSlug = `${slug}-${suffix}`;
    suffix++;
  }

  const fileHandle = await root.getFileHandle(`${finalSlug}.md`, {
    create: true,
  });
  const writable = await fileHandle.createWritable();
  await writable.write(content);
  await writable.close();

  return { slug: finalSlug, path: `${finalSlug}.md` };
}

export interface WritePageOptions {
  fetchImages?: boolean;
  frontmatterOpts?: Omit<FrontmatterOptions, "imagesPartial" | "imagesCount">;
  highlights?: Highlight[];
}

export async function writePageToVault(
  root: FileSystemDirectoryHandle,
  page: ExtractedPage,
  opts: WritePageOptions = {},
): Promise<WriteResult> {
  const slug = slugify(page.title);
  let markdown = page.markdown;
  let imagesPartial = false;
  let imagesCount = page.imageRefs.length;

  if (opts.fetchImages && page.imageRefs.length > 0) {
    const img = await fetchAndRewriteImages(root, slug, markdown, page.url);
    markdown = img.rewrittenMarkdown;
    imagesPartial = img.failedCount > 0;
    imagesCount = img.savedCount;
  }

  const highlights = opts.highlights ?? [];
  const frontmatter = buildFrontmatter(page, {
    ...opts.frontmatterOpts,
    imagesPartial,
    imagesCount,
    highlights,
  });

  return writeToVault(root, slug, frontmatter + "\n" + markdown + buildHighlightsSection(highlights));
}
