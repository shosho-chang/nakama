// FSA vault writer — writes Inbox/kb/{slug}.md (PRD §5.1).

import type { ExtractedPage } from "../shared/types.js";
import { buildFrontmatter } from "./frontmatter.js";
import type { FrontmatterOptions } from "./frontmatter.js";
import { fetchAndRewriteImages } from "./imageFetcher.js";
import { slugify } from "./slug.js";

export interface WriteResult {
  slug: string;
  path: string;
}

async function resolveDir(
  root: FileSystemDirectoryHandle,
  parts: string[],
): Promise<FileSystemDirectoryHandle> {
  let dir = root;
  for (const part of parts) {
    dir = await dir.getDirectoryHandle(part, { create: true });
  }
  return dir;
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
  const inboxDir = await resolveDir(root, ["Inbox", "kb"]);
  return fileExists(inboxDir, `${slug}.md`);
}

export async function writeToVaultExact(
  root: FileSystemDirectoryHandle,
  slug: string,
  content: string,
): Promise<WriteResult> {
  const inboxDir = await resolveDir(root, ["Inbox", "kb"]);
  const fileHandle = await inboxDir.getFileHandle(`${slug}.md`, { create: true });
  const writable = await fileHandle.createWritable();
  await writable.write(content);
  await writable.close();
  return { slug, path: `Inbox/kb/${slug}.md` };
}

export async function writeToVault(
  root: FileSystemDirectoryHandle,
  slug: string,
  content: string,
): Promise<WriteResult> {
  const inboxDir = await resolveDir(root, ["Inbox", "kb"]);

  // Collision detection: auto-suffix until free slot.
  let finalSlug = slug;
  let suffix = 2;
  while (await fileExists(inboxDir, `${finalSlug}.md`)) {
    finalSlug = `${slug}-${suffix}`;
    suffix++;
  }

  const fileHandle = await inboxDir.getFileHandle(`${finalSlug}.md`, {
    create: true,
  });
  const writable = await fileHandle.createWritable();
  await writable.write(content);
  await writable.close();

  return { slug: finalSlug, path: `Inbox/kb/${finalSlug}.md` };
}

export interface WritePageOptions {
  fetchImages?: boolean;
  frontmatterOpts?: Omit<FrontmatterOptions, "imagesPartial" | "imagesCount">;
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

  const frontmatter = buildFrontmatter(page, {
    ...opts.frontmatterOpts,
    imagesPartial,
    imagesCount,
  });

  return writeToVault(root, slug, frontmatter + "\n" + markdown);
}
