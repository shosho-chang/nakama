import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchAndRewriteImages } from "../../src/vault/imageFetcher.js";

// Re-use the in-memory FSA mock from writer tests.
class MemFileHandle {
  readonly kind = "file" as const;
  private _buf: ArrayBuffer = new ArrayBuffer(0);
  constructor(readonly name: string) {}
  async createWritable() {
    return {
      write: async (data: ArrayBuffer) => { this._buf = data; },
      close: async () => {},
    };
  }
  bytes() { return this._buf.byteLength; }
}

class MemDirHandle {
  readonly kind = "directory" as const;
  readonly files = new Map<string, MemFileHandle>();
  private dirs = new Map<string, MemDirHandle>();
  constructor(readonly name: string) {}

  async getDirectoryHandle(name: string, opts: { create?: boolean } = {}): Promise<MemDirHandle> {
    if (!this.dirs.has(name)) {
      if (!opts.create) throw new DOMException("NotFound", "NotFoundError");
      this.dirs.set(name, new MemDirHandle(name));
    }
    return this.dirs.get(name)!;
  }

  async getFileHandle(name: string, opts: { create?: boolean } = {}): Promise<MemFileHandle> {
    if (!this.files.has(name)) {
      if (!opts.create) throw new DOMException("NotFound", "NotFoundError");
      this.files.set(name, new MemFileHandle(name));
    }
    return this.files.get(name)!;
  }

  attachmentDir(slug: string): MemDirHandle | undefined {
    return this.dirs.get("attachments")?.dirs.get(slug);
  }
}

function streamFor(buf: ArrayBuffer, chunkSize = 64 * 1024): ReadableStream<Uint8Array> {
  const u8 = new Uint8Array(buf);
  let offset = 0;
  return new ReadableStream<Uint8Array>({
    pull(controller) {
      if (offset >= u8.byteLength) {
        controller.close();
        return;
      }
      const end = Math.min(offset + chunkSize, u8.byteLength);
      controller.enqueue(u8.slice(offset, end));
      offset = end;
    },
  });
}

function mockResponse(opts: {
  ok?: boolean;
  status?: number;
  contentType?: string;
  body?: ArrayBuffer;
  contentLength?: string;
  bodyStream?: ReadableStream<Uint8Array> | null;
}): Response {
  const body = opts.body ?? new ArrayBuffer(4);
  const stream =
    opts.bodyStream === null
      ? null
      : (opts.bodyStream ?? streamFor(body));
  return {
    ok: opts.ok ?? true,
    status: opts.status ?? 200,
    body: stream,
    headers: {
      get: (k: string) => {
        if (k === "content-type") return opts.contentType ?? "image/jpeg";
        if (k === "content-length") return opts.contentLength ?? null;
        return null;
      },
    },
  } as unknown as Response;
}

describe("fetchAndRewriteImages", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("returns unmodified markdown when no images", async () => {
    const root = new MemDirHandle("vault");
    const md = "# No images\n\nJust text.";
    const result = await fetchAndRewriteImages(
      root as unknown as FileSystemDirectoryHandle,
      "no-images",
      md,
      "https://example.com",
    );
    expect(result.rewrittenMarkdown).toBe(md);
    expect(result.savedCount).toBe(0);
    expect(result.failedCount).toBe(0);
  });

  it("downloads image, writes to vault, rewrites URL", async () => {
    const root = new MemDirHandle("vault");
    vi.mocked(fetch).mockResolvedValue(
      mockResponse({ contentType: "image/png", body: new ArrayBuffer(100) }),
    );

    const md = "![alt](https://example.com/img.png)";
    const result = await fetchAndRewriteImages(
      root as unknown as FileSystemDirectoryHandle,
      "my-slug",
      md,
      "https://example.com",
    );

    expect(result.savedCount).toBe(1);
    expect(result.failedCount).toBe(0);
    expect(result.rewrittenMarkdown).toBe(
      "![alt](attachments/my-slug/img-1.png)",
    );
    const dir = root.attachmentDir("my-slug");
    expect(dir?.files.has("img-1.png")).toBe(true);
  });

  it("keeps remote URL when fetch fails (CORS/network error)", async () => {
    const root = new MemDirHandle("vault");
    vi.mocked(fetch).mockRejectedValue(new TypeError("Network error"));

    const md = "![photo](https://blocked.example.com/img.jpg)";
    const result = await fetchAndRewriteImages(
      root as unknown as FileSystemDirectoryHandle,
      "fail-slug",
      md,
      "https://example.com",
    );

    expect(result.savedCount).toBe(0);
    expect(result.failedCount).toBe(1);
    expect(result.rewrittenMarkdown).toBe(md);
  });

  it("rejects images over 20 MB via Content-Length header", async () => {
    const root = new MemDirHandle("vault");
    vi.mocked(fetch).mockResolvedValue(
      mockResponse({
        contentType: "image/jpeg",
        contentLength: String(21 * 1024 * 1024),
        body: new ArrayBuffer(21 * 1024 * 1024),
      }),
    );

    const md = "![big](https://example.com/big.jpg)";
    const result = await fetchAndRewriteImages(
      root as unknown as FileSystemDirectoryHandle,
      "big-slug",
      md,
      "https://example.com",
    );

    expect(result.savedCount).toBe(0);
    expect(result.failedCount).toBe(1);
    expect(result.rewrittenMarkdown).toBe(md);
  });

  it("aborts mid-stream when body exceeds 20 MB without a Content-Length header", async () => {
    const root = new MemDirHandle("vault");
    vi.mocked(fetch).mockResolvedValue(
      mockResponse({
        contentType: "image/jpeg",
        body: new ArrayBuffer(21 * 1024 * 1024),
      }),
    );

    const result = await fetchAndRewriteImages(
      root as unknown as FileSystemDirectoryHandle,
      "big-slug2",
      "![big](https://example.com/img.jpg)",
      "https://example.com",
    );

    expect(result.savedCount).toBe(0);
    expect(result.failedCount).toBe(1);
  });

  it("aborts mid-stream when server lies and Content-Length under-reports actual body size", async () => {
    const root = new MemDirHandle("vault");
    vi.mocked(fetch).mockResolvedValue(
      mockResponse({
        contentType: "image/jpeg",
        contentLength: "1024",
        body: new ArrayBuffer(21 * 1024 * 1024),
      }),
    );

    const result = await fetchAndRewriteImages(
      root as unknown as FileSystemDirectoryHandle,
      "lying-slug",
      "![big](https://example.com/img.jpg)",
      "https://example.com",
    );

    expect(result.savedCount).toBe(0);
    expect(result.failedCount).toBe(1);
  });

  it("returns null when response body stream is missing", async () => {
    const root = new MemDirHandle("vault");
    vi.mocked(fetch).mockResolvedValue(
      mockResponse({ contentType: "image/png", bodyStream: null }),
    );

    const result = await fetchAndRewriteImages(
      root as unknown as FileSystemDirectoryHandle,
      "no-body-slug",
      "![x](https://example.com/x.png)",
      "https://example.com",
    );

    expect(result.savedCount).toBe(0);
    expect(result.failedCount).toBe(1);
  });

  it("keeps remote URL when HTTP status is not 200", async () => {
    const root = new MemDirHandle("vault");
    vi.mocked(fetch).mockResolvedValue(mockResponse({ ok: false, status: 404 }));

    const result = await fetchAndRewriteImages(
      root as unknown as FileSystemDirectoryHandle,
      "404-slug",
      "![img](https://example.com/missing.jpg)",
      "https://example.com",
    );

    expect(result.failedCount).toBe(1);
    expect(result.savedCount).toBe(0);
  });

  it("detects extension from URL path when content-type is unknown", async () => {
    const root = new MemDirHandle("vault");
    vi.mocked(fetch).mockResolvedValue(
      mockResponse({ contentType: "application/octet-stream", body: new ArrayBuffer(50) }),
    );

    const result = await fetchAndRewriteImages(
      root as unknown as FileSystemDirectoryHandle,
      "ct-slug",
      "![img](https://cdn.example.com/photo.webp)",
      "https://example.com",
    );

    expect(result.savedCount).toBe(1);
    expect(result.rewrittenMarkdown).toContain("img-1.webp");
  });

  it("handles multiple images, deduplicates same URL", async () => {
    const root = new MemDirHandle("vault");
    vi.mocked(fetch).mockImplementation(async () =>
      mockResponse({ contentType: "image/gif", body: new ArrayBuffer(10) }),
    );

    const md =
      "![a](https://example.com/a.gif) ![b](https://example.com/b.gif) ![a2](https://example.com/a.gif)";
    const result = await fetchAndRewriteImages(
      root as unknown as FileSystemDirectoryHandle,
      "multi-slug",
      md,
      "https://example.com",
    );

    // Only 2 unique URLs should be fetched (a.gif and b.gif)
    expect(result.savedCount).toBe(2);
    // Both occurrences of a.gif should be rewritten to img-1.gif
    expect(result.rewrittenMarkdown).toContain("img-1.gif");
    expect(result.rewrittenMarkdown).toContain("img-2.gif");
    expect(result.rewrittenMarkdown.match(/img-1\.gif/g)?.length).toBe(2);
  });
});
