import { describe, it, expect, vi, beforeEach } from "vitest";
import { initPopup } from "../../src/popup/popup.js";
import type { PopupDeps } from "../../src/popup/popup.js";
import type { ExtractedPage } from "../../src/shared/types.js";
import type { WriteResult } from "../../src/vault/writer.js";

// Minimal fake page for extraction responses.
const FAKE_PAGE: ExtractedPage = {
  url: "https://example.com/article",
  title: "Test Article",
  markdown: "# Test Article\n\nContent here.",
  description: "A test article",
  author: "Jane Doe",
  published: "2026-05-10",
  imageRefs: [],
  site: "Example",
  wordCount: 42,
};

const FAKE_HANDLE = {} as FileSystemDirectoryHandle;
const FAKE_RESULT: WriteResult = { slug: "test-article", path: "Inbox/kb/test-article.md" };

function buildDOM(): void {
  document.body.innerHTML = `
    <main id="root">
      <h1>News Coo</h1>
      <section id="state-loading" hidden><p class="muted">Extracting…</p></section>
      <section id="state-preview" hidden>
        <form id="form-preview">
          <input id="field-title" type="text" />
          <input id="field-author" type="text" />
          <input id="field-site" type="text" readonly />
          <span id="field-word-count"></span>
          <span id="field-image-count"></span>
          <span id="field-highlight-count" hidden></span>
          <span id="badge-selection-only" hidden></span>
          <code id="field-slug"></code>
          <p id="dedup-warning" hidden></p>
          <button type="submit" id="btn-save">Save</button>
        </form>
      </section>
      <section id="state-dedup" hidden>
        <p><code id="dedup-existing-path"></code></p>
        <button id="btn-overwrite">Overwrite</button>
        <button id="btn-suffix">Save as -2</button>
        <button id="btn-cancel-dedup">Cancel</button>
      </section>
      <section id="state-saving" hidden><p>Saving…</p></section>
      <section id="state-saved" hidden><p>Saved — <code id="saved-path"></code></p></section>
      <section id="state-error" hidden>
        <p id="error-msg"></p>
        <button id="btn-retry">Retry</button>
      </section>
    </main>
  `;
}

// Minimal chrome mock — only what popup.ts uses.
function setupChrome(): void {
  (globalThis as Record<string, unknown>).chrome = {
    tabs: {
      query: vi.fn().mockResolvedValue([{ id: 1 }]),
      sendMessage: vi.fn(),
    },
    runtime: {
      openOptionsPage: vi.fn(),
    },
  };
}

function makeDeps(overrides: Partial<PopupDeps> = {}): PopupDeps {
  return {
    loadHandle: vi.fn().mockResolvedValue(FAKE_HANDLE),
    verifyHandle: vi.fn().mockResolvedValue(true),
    sendExtract: vi.fn().mockResolvedValue({ ok: true, page: FAKE_PAGE, selectionOnly: false }),
    checkSlugExists: vi.fn().mockResolvedValue(false),
    writeExact: vi.fn().mockResolvedValue(FAKE_RESULT),
    writeAutoSuffix: vi.fn().mockResolvedValue({ slug: "test-article-2", path: "Inbox/kb/test-article-2.md" }),
    slugify: (title: string) => title.toLowerCase().replace(/\s+/g, "-"),
    buildContent: (_page, title, _author, _selectionOnly, _highlights) => `---\ntitle: ${title}\n---\n\ncontent`,
    getHighlights: vi.fn().mockResolvedValue([]),
    clearHighlights: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  };
}

describe("initPopup — state: loading → preview (happy path)", () => {
  beforeEach(() => {
    buildDOM();
    setupChrome();
  });

  it("shows preview state after successful extraction", async () => {
    await initPopup(makeDeps());

    expect(document.getElementById("state-preview")?.hidden).toBe(false);
    expect(document.getElementById("state-loading")?.hidden).toBe(true);
    expect(document.getElementById("state-error")?.hidden).toBe(true);
  });

  it("populates title field from extracted page", async () => {
    await initPopup(makeDeps());

    const titleEl = document.getElementById("field-title") as HTMLInputElement;
    expect(titleEl.value).toBe("Test Article");
  });

  it("populates author field from extracted page", async () => {
    await initPopup(makeDeps());

    const authorEl = document.getElementById("field-author") as HTMLInputElement;
    expect(authorEl.value).toBe("Jane Doe");
  });

  it("shows word count when present", async () => {
    await initPopup(makeDeps());

    const wcEl = document.getElementById("field-word-count");
    expect(wcEl?.textContent).toContain("42");
  });

  it("shows slug preview derived from title", async () => {
    await initPopup(makeDeps());

    const slugEl = document.getElementById("field-slug");
    expect(slugEl?.textContent).toContain("test-article");
  });

  it("hides dedup warning when slug does not exist", async () => {
    await initPopup(makeDeps({ checkSlugExists: vi.fn().mockResolvedValue(false) }));

    expect(document.getElementById("dedup-warning")?.hidden).toBe(true);
  });

  it("shows dedup warning when slug already exists", async () => {
    await initPopup(makeDeps({ checkSlugExists: vi.fn().mockResolvedValue(true) }));

    expect(document.getElementById("dedup-warning")?.hidden).toBe(false);
  });
});

describe("initPopup — state: error (no vault)", () => {
  beforeEach(() => {
    buildDOM();
    setupChrome();
  });

  it("shows error state when no vault handle is found", async () => {
    await initPopup(makeDeps({ loadHandle: vi.fn().mockResolvedValue(null) }));

    expect(document.getElementById("state-error")?.hidden).toBe(false);
    expect(document.getElementById("error-msg")?.textContent).toContain("No vault");
  });

  it("shows error state when vault permission revoked", async () => {
    await initPopup(makeDeps({ verifyHandle: vi.fn().mockResolvedValue(false) }));

    expect(document.getElementById("state-error")?.hidden).toBe(false);
    expect(document.getElementById("error-msg")?.textContent).toContain("revoked");
  });
});

describe("initPopup — state: error (extraction failed)", () => {
  beforeEach(() => {
    buildDOM();
    setupChrome();
  });

  it("shows error state when sendExtract rejects", async () => {
    await initPopup(
      makeDeps({ sendExtract: vi.fn().mockRejectedValue(new Error("no content script")) }),
    );

    expect(document.getElementById("state-error")?.hidden).toBe(false);
  });

  it("shows error state when extraction response is not ok", async () => {
    await initPopup(
      makeDeps({
        sendExtract: vi.fn().mockResolvedValue({ ok: false, error: "parse error" }),
      }),
    );

    expect(document.getElementById("state-error")?.hidden).toBe(false);
    expect(document.getElementById("error-msg")?.textContent).toContain("parse error");
  });
});

describe("initPopup — state: preview → saving → saved (no collision)", () => {
  beforeEach(() => {
    buildDOM();
    setupChrome();
  });

  it("calls writeExact and shows saved state on form submit", async () => {
    const writeExact = vi.fn().mockResolvedValue(FAKE_RESULT);
    const deps = makeDeps({ writeExact });
    await initPopup(deps);

    document.getElementById("form-preview")!.dispatchEvent(
      new Event("submit", { bubbles: true, cancelable: true }),
    );
    // Flush microtasks.
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();

    expect(writeExact).toHaveBeenCalledOnce();
    expect(document.getElementById("state-saved")?.hidden).toBe(false);
    expect(document.getElementById("saved-path")?.textContent).toBe(
      "Inbox/kb/test-article.md",
    );
  });
});

describe("initPopup — state: preview → dedup-prompt → overwrite / suffix", () => {
  beforeEach(() => {
    buildDOM();
    setupChrome();
  });

  it("shows dedup-prompt when slug exists on save attempt", async () => {
    const deps = makeDeps({ checkSlugExists: vi.fn().mockResolvedValue(true) });
    await initPopup(deps);

    document.getElementById("form-preview")!.dispatchEvent(
      new Event("submit", { bubbles: true, cancelable: true }),
    );
    await Promise.resolve();
    await Promise.resolve();

    expect(document.getElementById("state-dedup")?.hidden).toBe(false);
  });

  it("overwrite button calls writeExact and shows saved", async () => {
    const writeExact = vi.fn().mockResolvedValue(FAKE_RESULT);
    const deps = makeDeps({
      checkSlugExists: vi.fn().mockResolvedValue(true),
      writeExact,
    });
    await initPopup(deps);

    document.getElementById("form-preview")!.dispatchEvent(
      new Event("submit", { bubbles: true, cancelable: true }),
    );
    await Promise.resolve();
    await Promise.resolve();

    document.getElementById("btn-overwrite")!.click();
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();

    expect(writeExact).toHaveBeenCalledOnce();
    expect(document.getElementById("state-saved")?.hidden).toBe(false);
  });

  it("suffix button calls writeAutoSuffix and shows saved", async () => {
    const writeAutoSuffix = vi.fn().mockResolvedValue({
      slug: "test-article-2",
      path: "Inbox/kb/test-article-2.md",
    });
    const deps = makeDeps({
      checkSlugExists: vi.fn().mockResolvedValue(true),
      writeAutoSuffix,
    });
    await initPopup(deps);

    document.getElementById("form-preview")!.dispatchEvent(
      new Event("submit", { bubbles: true, cancelable: true }),
    );
    await Promise.resolve();
    await Promise.resolve();

    document.getElementById("btn-suffix")!.click();
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();

    expect(writeAutoSuffix).toHaveBeenCalledOnce();
    expect(document.getElementById("state-saved")?.hidden).toBe(false);
    expect(document.getElementById("saved-path")?.textContent).toBe(
      "Inbox/kb/test-article-2.md",
    );
  });

  it("cancel button returns to preview state", async () => {
    const deps = makeDeps({ checkSlugExists: vi.fn().mockResolvedValue(true) });
    await initPopup(deps);

    document.getElementById("form-preview")!.dispatchEvent(
      new Event("submit", { bubbles: true, cancelable: true }),
    );
    await Promise.resolve();
    await Promise.resolve();

    document.getElementById("btn-cancel-dedup")!.click();

    expect(document.getElementById("state-preview")?.hidden).toBe(false);
    expect(document.getElementById("state-dedup")?.hidden).toBe(true);
  });
});
