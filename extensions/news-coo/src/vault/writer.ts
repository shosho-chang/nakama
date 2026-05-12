// FSA vault writer — writes {slug}.md at vault root.

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
  // Default: true. Downloading images to `attachments/<slug>/` and rewriting
  // markdown image refs to local vault paths is the only sensible behaviour —
  // remote-only refs go stale and don't render offline. Pass `false` to opt
  // out for tests or unusual flows.
  fetchImages?: boolean;
  // When true, write to `{slug}.md` regardless of collisions (overwrites).
  // Default false: auto-suffix `-2`, `-3`… until a free slot is found.
  exact?: boolean;
  // Override title/author after extraction (popup form lets the user edit
  // them before save). The slug derives from the effective title.
  titleOverride?: string;
  authorOverride?: string;
  frontmatterOpts?: Omit<FrontmatterOptions, "imagesPartial" | "imagesCount">;
  highlights?: Highlight[];
}

export async function writePageToVault(
  root: FileSystemDirectoryHandle,
  page: ExtractedPage,
  opts: WritePageOptions = {},
): Promise<WriteResult> {
  const effectivePage: ExtractedPage = {
    ...page,
    title: opts.titleOverride ?? page.title,
    author: opts.authorOverride ?? page.author,
  };
  const slug = slugify(effectivePage.title);
  let markdown = effectivePage.markdown;
  let imagesPartial = false;
  let imagesCount = effectivePage.imageRefs.length;

  // fetchImages defaults to true — only `=== false` opts out.
  if (opts.fetchImages !== false && effectivePage.imageRefs.length > 0) {
    const img = await fetchAndRewriteImages(root, slug, markdown, effectivePage.url);
    markdown = img.rewrittenMarkdown;
    imagesPartial = img.failedCount > 0;
    imagesCount = img.savedCount;
  }

  const highlights = opts.highlights ?? [];
  const frontmatter = buildFrontmatter(effectivePage, {
    ...opts.frontmatterOpts,
    imagesPartial,
    imagesCount,
    highlights,
  });

  const content = frontmatter + "\n" + markdown + buildHighlightsSection(highlights);
  return opts.exact
    ? writeToVaultExact(root, slug, content)
    : writeToVault(root, slug, content);
}
