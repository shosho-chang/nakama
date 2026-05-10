import { describe, expect, it } from "vitest";
import { writeToVault } from "../../src/vault/writer.js";

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

  getInboxKb(): MemDirHandle | undefined {
    return this.dirs.get("Inbox")?.dirs.get("kb");
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
    expect(result.path).toBe("Inbox/kb/test-article.md");

    const file = root.getInboxKb()?.getFile("test-article.md");
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
    expect(result.path).toBe("Inbox/kb/my-article-2.md");
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
