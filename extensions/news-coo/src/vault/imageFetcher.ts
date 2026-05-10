// Image fetcher — TypeScript port of shared/image_fetcher.py.
// Fetches images referenced in Markdown, writes them to vault, rewrites refs.

const MAX_IMAGE_BYTES = 20 * 1024 * 1024; // 20 MB
const DEFAULT_TIMEOUT_MS = 15_000;

const CT_TO_EXT: Readonly<Record<string, string>> = {
  "image/jpeg": "jpg",
  "image/jpg": "jpg",
  "image/pjpeg": "jpg",
  "image/png": "png",
  "image/webp": "webp",
  "image/gif": "gif",
  "image/svg+xml": "svg",
  "image/tiff": "tiff",
  "image/bmp": "bmp",
  "image/avif": "avif",
};

const VALID_EXTS = new Set([
  "jpg", "jpeg", "png", "webp", "gif", "svg", "tiff", "bmp", "avif",
]);

const MD_IMAGE_RE = /!\[([^\]]*)\]\((\S+?)\)/g;

export interface FetchImagesResult {
  rewrittenMarkdown: string;
  savedCount: number;
  failedCount: number;
}

function extensionFor(contentType: string, url: string): string | null {
  const ct = contentType.split(";")[0].trim().toLowerCase();
  if (ct in CT_TO_EXT) return CT_TO_EXT[ct];
  if (ct.startsWith("image/")) {
    return ct.split("/")[1]?.split("+")[0] ?? null;
  }
  // Fallback: URL path extension
  try {
    const ext = new URL(url).pathname.split(".").pop()?.toLowerCase() ?? "";
    if (VALID_EXTS.has(ext)) return ext === "jpeg" ? "jpg" : ext;
  } catch {
    // ignore malformed URLs
  }
  return null;
}

function resolveUrl(rawUrl: string, baseUrl: string): string | null {
  if (!rawUrl) return null;
  try {
    const parsed = new URL(rawUrl);
    if (parsed.protocol === "http:" || parsed.protocol === "https:")
      return rawUrl;
  } catch {
    // relative — fall through to base resolution
  }
  try {
    const joined = new URL(rawUrl, baseUrl);
    if (joined.protocol === "http:" || joined.protocol === "https:")
      return joined.href;
  } catch {
    // ignore
  }
  return null;
}

async function downloadImage(
  url: string,
  timeoutMs: number,
): Promise<{ buf: ArrayBuffer; contentType: string } | null> {
  const controller = new AbortController();
  const timer = setTimeout(() => { controller.abort(); }, timeoutMs);
  try {
    const resp = await fetch(url, { signal: controller.signal });
    if (!resp.ok) return null;

    const declared = resp.headers.get("content-length");
    if (declared !== null) {
      const n = parseInt(declared, 10);
      if (Number.isFinite(n) && n > MAX_IMAGE_BYTES) return null;
    }

    // Stream and abort once the running total exceeds MAX_IMAGE_BYTES, so
    // missing/lying Content-Length cannot force a multi-GB buffer into RAM.
    const body = resp.body;
    if (body === null) return null;
    const reader = body.getReader();
    const chunks: Uint8Array[] = [];
    let total = 0;
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      total += value.byteLength;
      if (total > MAX_IMAGE_BYTES) {
        controller.abort();
        return null;
      }
      chunks.push(value);
    }

    const buf = new Uint8Array(total);
    let offset = 0;
    for (const c of chunks) {
      buf.set(c, offset);
      offset += c.byteLength;
    }
    return {
      buf: buf.buffer,
      contentType: resp.headers.get("content-type") ?? "",
    };
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

async function resolveAttachmentDir(
  root: FileSystemDirectoryHandle,
  slug: string,
): Promise<FileSystemDirectoryHandle> {
  const kb = await root.getDirectoryHandle("KB", { create: true });
  const att = await kb.getDirectoryHandle("Attachments", { create: true });
  const web = await att.getDirectoryHandle("web", { create: true });
  return web.getDirectoryHandle(slug, { create: true });
}

async function writeImageFile(
  dir: FileSystemDirectoryHandle,
  filename: string,
  buf: ArrayBuffer,
): Promise<void> {
  const fh = await dir.getFileHandle(filename, { create: true });
  const wr = await fh.createWritable();
  await wr.write(buf);
  await wr.close();
}

export async function fetchAndRewriteImages(
  root: FileSystemDirectoryHandle,
  slug: string,
  markdown: string,
  baseUrl: string,
  timeoutMs = DEFAULT_TIMEOUT_MS,
): Promise<FetchImagesResult> {
  // Collect unique URLs in appearance order.
  const matches = [...markdown.matchAll(MD_IMAGE_RE)];
  if (matches.length === 0) {
    return { rewrittenMarkdown: markdown, savedCount: 0, failedCount: 0 };
  }

  const uniqueUrls: string[] = [];
  const urlSet = new Set<string>();
  for (const m of matches) {
    const resolved = resolveUrl(m[2].trim(), baseUrl);
    if (resolved && !urlSet.has(resolved)) {
      uniqueUrls.push(resolved);
      urlSet.add(resolved);
    }
  }

  const urlToVaultPath = new Map<string, string>();
  let savedCount = 0;
  let failedCount = 0;

  const dir = await resolveAttachmentDir(root, slug);
  const vaultPrefix = `KB/Attachments/web/${slug}`;

  for (let i = 0; i < uniqueUrls.length; i++) {
    const url = uniqueUrls[i];
    const imgIdx = i + 1;
    const result = await downloadImage(url, timeoutMs);
    if (!result) {
      failedCount++;
      continue;
    }
    const ext = extensionFor(result.contentType, url);
    if (!ext) {
      failedCount++;
      continue;
    }
    const filename = `img-${imgIdx}.${ext}`;
    try {
      await writeImageFile(dir, filename, result.buf);
      urlToVaultPath.set(url, `${vaultPrefix}/${filename}`);
      savedCount++;
    } catch {
      failedCount++;
    }
  }

  // Rewrite markdown.
  const rewrittenMarkdown = markdown.replace(
    MD_IMAGE_RE,
    (_match, alt: string, rawUrl: string) => {
      const resolved = resolveUrl(rawUrl.trim(), baseUrl);
      if (resolved) {
        const vaultPath = urlToVaultPath.get(resolved);
        if (vaultPath) return `![${alt}](${vaultPath})`;
      }
      return _match;
    },
  );

  return { rewrittenMarkdown, savedCount, failedCount };
}
