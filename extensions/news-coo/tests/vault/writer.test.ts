import { describe, expect, it } from "vitest";
import {
  writeToVault,
  writeToVaultExact,
  checkSlugExists,
  writePageToVault,
} from "../../src/vault/writer.js";
import type { ExtractedPage } from "../../src/shared/types.js";

// In-memory FSA directory tree.
class MemFileHandle {
  readonly kind = "file" as const;
  private _content = "";
  constructor(readonly name: string) {}
  async createWritable() {
    return {
      write: async (data: string) => { this._content = data; },
      close: async () => {},
    };
  }
  content() { return this._content; }
}

class MemDirHandle {
  readonly kind = "directory" as const;
  private files = new Map<string, MemFileHandle>();
  private dirs = new Map<string, MemDirHandle>();
  constructor(readonly name: string) {}

  async getDirectoryHandle(
    name: string,
    opts: { create?: boolean } = {},
  ): Promise<MemDirHandle> {
    if (!this.dirs.has(name)) {
      if (!opts.create)
        throw new DOMException("Entry not found", "NotFoundError");
      this.dirs.set(name, new MemDirHandle(name));
    }
    return this.dirs.get(name)!;
  }

  async getFileHandle(
    name: string,
    opts: { create?: boolean } = {},
  ): Promise<MemFileHandle> {
    if (!this.files.has(name)) {
      if (!opts.create)
        throw new DOMException("Entry not found", "NotFoundError");
      this.files.set(name, new MemFileHandle(name));
    }
    return this.files.get(name)!;
  }

  getFile(name: string): MemFileHandle | undefined {
    return this.files.get(name);
  }

}

describe("writeToVault", () => {
  it("creates Inbox/kb/{slug}.md with the provided content", async () => {
    const root = new MemDirHandle("vault");
    const content = "---\ntitle: Test\n---\n\n# Test\n";
    const result = await writeToVault(
      root as unknown as FileSystemDirectoryHandle,
      "test-article",
      content,
    );

    expect(result.slug).toBe("test-article");
    expect(result.path).toBe("test-article.md");

    const file = root.getFile("test-article.md");
    expect(file).toBeDefined();
    expect(file?.content()).toBe(content);
  });

  it("auto-suffixes slug when file already exists", async () => {
    const root = new MemDirHandle("vault");
    const content = "body";

    // Write first file manually to simulate existing file.
    await writeToVault(
      root as unknown as FileSystemDirectoryHandle,
      "my-article",
      content,
    );

    const result = await writeToVault(
      root as unknown as FileSystemDirectoryHandle,
      "my-article",
      content,
    );

    expect(result.slug).toBe("my-article-2");
    expect(result.path).toBe("my-article-2.md");
  });

  it("increments suffix past -2 when needed", async () => {
    const root = new MemDirHandle("vault");
    const content = "body";
    await writeToVault(root as unknown as FileSystemDirectoryHandle, "art", content);
    await writeToVault(root as unknown as FileSystemDirectoryHandle, "art", content);
    const result = await writeToVault(
      root as unknown as FileSystemDirectoryHandle,
      "art",
      content,
    );
    expect(result.slug).toBe("art-3");
  });
});

describe("checkSlugExists", () => {
  it("returns false when the slug file does not exist", async () => {
    const root = new MemDirHandle("vault");
    const exists = await checkSlugExists(
      root as unknown as FileSystemDirectoryHandle,
      "nonexistent-slug",
    );
    expect(exists).toBe(false);
  });

  it("returns true when the slug file already exists", async () => {
    const root = new MemDirHandle("vault");
    // Pre-create the file via writeToVaultExact.
    await writeToVaultExact(
      root as unknown as FileSystemDirectoryHandle,
      "existing-slug",
      "content",
    );
    const exists = await checkSlugExists(
      root as unknown as FileSystemDirectoryHandle,
      "existing-slug",
    );
    expect(exists).toBe(true);
  });
});

describe("writeToVaultExact", () => {
  it("creates the file at Inbox/kb/{slug}.md with exact content", async () => {
    const root = new MemDirHandle("vault");
    const result = await writeToVaultExact(
      root as unknown as FileSystemDirectoryHandle,
      "exact-slug",
      "exact content",
    );
    expect(result.slug).toBe("exact-slug");
    expect(result.path).toBe("exact-slug.md");
    expect(root.getFile("exact-slug.md")?.content()).toBe("exact content");
  });

  it("overwrites content when called twice with the same slug", async () => {
    const root = new MemDirHandle("vault");
    await writeToVaultExact(
      root as unknown as FileSystemDirectoryHandle,
      "dup",
      "first",
    );
    await writeToVaultExact(
      root as unknown as FileSystemDirectoryHandle,
      "dup",
      "second",
    );
    expect(root.getFile("dup.md")?.content()).toBe("second");
  });
});

const FAKE_PAGE: ExtractedPage = {
  url: "https://example.com/article",
  title: "Test Article",
  markdown: "# Hello",
  description: "",
  author: "Author",
  published: "2026-05-11",
  imageRefs: [],
};

describe("writePageToVault", () => {
  it("writes a file derived from page title slug", async () => {
    const root = new MemDirHandle("vault");
    const result = await writePageToVault(
      root as unknown as FileSystemDirectoryHandle,
      FAKE_PAGE,
    );
    expect(result.slug).toBe("test-article");
    expect(result.path).toBe("test-article.md");
  });

  it("skips image fetching when fetchImages is false", async () => {
    const root = new MemDirHandle("vault");
    const result = await writePageToVault(
      root as unknown as FileSystemDirectoryHandle,
      FAKE_PAGE,
      { fetchImages: false },
    );
    expect(result.slug).toBe("test-article");
  });

  it("uses titleOverride/authorOverride for slug and frontmatter", async () => {
    const root = new MemDirHandle("vault");
    const result = await writePageToVault(
      root as unknown as FileSystemDirectoryHandle,
      FAKE_PAGE,
      {
        titleOverride: "Edited Title From Form",
        authorOverride: "Edited Author",
      },
    );
    expect(result.slug).toBe("edited-title-from-form");
    const file = root.getFile("edited-title-from-form.md");
    expect(file?.content()).toContain("Edited Title From Form");
    expect(file?.content()).toContain("Edited Author");
  });

  it("overwrites existing file when exact=true", async () => {
    const root = new MemDirHandle("vault");
    await writePageToVault(
      root as unknown as FileSystemDirectoryHandle,
      FAKE_PAGE,
      { exact: true },
    );
    await writePageToVault(
      root as unknown as FileSystemDirectoryHandle,
      { ...FAKE_PAGE, markdown: "# Second Pass" },
      { exact: true },
    );
    // Same slug — no auto-suffix.
    expect(root.getFile("test-article.md")?.content()).toContain("Second Pass");
    expect(root.getFile("test-article-2.md")).toBeUndefined();
  });

  it("auto-suffixes by default (exact=false) when slug exists", async () => {
    const root = new MemDirHandle("vault");
    await writePageToVault(
      root as unknown as FileSystemDirectoryHandle,
      FAKE_PAGE,
    );
    const result = await writePageToVault(
      root as unknown as FileSystemDirectoryHandle,
      FAKE_PAGE,
    );
    expect(result.slug).toBe("test-article-2");
  });

  it("uses empty highlights when none provided", async () => {
    const root = new MemDirHandle("vault");
    const result = await writePageToVault(
      root as unknown as FileSystemDirectoryHandle,
      FAKE_PAGE,
      {},
    );
    expect(result.path).toContain("test-article");
  });

  it("includes highlights section when highlights provided", async () => {
    const root = new MemDirHandle("vault");
    await writePageToVault(
      root as unknown as FileSystemDirectoryHandle,
      FAKE_PAGE,
      { highlights: [{ text: "notable passage" }] },
    );
    const file = root.getFile("test-article.md");
    expect(file?.content()).toContain("notable passage");
  });
});
