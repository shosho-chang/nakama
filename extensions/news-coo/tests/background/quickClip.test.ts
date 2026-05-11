import { describe, it, expect, vi } from "vitest";
import { quickClip } from "../../src/background/quickClip.js";
import type { QuickClipDeps } from "../../src/background/quickClip.js";
import type { ExtractedPage } from "../../src/shared/types.js";
import type { WriteResult } from "../../src/vault/writer.js";

const FAKE_PAGE: ExtractedPage = {
  url: "https://example.com/a",
  title: "Hello",
  markdown: "# Hello",
  description: "",
  author: "",
  published: "",
  imageRefs: [],
};

const FAKE_HANDLE = {} as FileSystemDirectoryHandle;
const FAKE_RESULT: WriteResult = { slug: "hello", path: "Inbox/kb/hello.md" };

function makeDeps(overrides: Partial<QuickClipDeps> = {}): QuickClipDeps {
  return {
    loadHandle: vi.fn().mockResolvedValue(FAKE_HANDLE),
    verifyHandle: vi.fn().mockResolvedValue(true),
    sendExtract: vi.fn().mockResolvedValue({ ok: true, page: FAKE_PAGE, selectionOnly: false }),
    getHighlights: vi.fn().mockResolvedValue([]),
    clearHighlights: vi.fn().mockResolvedValue(undefined),
    notifySuccess: vi.fn(),
    notifyError: vi.fn(),
    writePageToVault: vi.fn().mockResolvedValue(FAKE_RESULT),
    ...overrides,
  };
}

describe("quickClip", () => {
  it("calls notifySuccess exactly once on success", async () => {
    const deps = makeDeps();
    await quickClip(1, deps);

    expect(deps.notifySuccess).toHaveBeenCalledOnce();
    expect(deps.notifySuccess).toHaveBeenCalledWith("hello");
    expect(deps.notifyError).not.toHaveBeenCalled();
  });

  it("calls notifyError once when no vault handle", async () => {
    const deps = makeDeps({ loadHandle: vi.fn().mockResolvedValue(null) });
    await quickClip(1, deps);

    expect(deps.notifyError).toHaveBeenCalledOnce();
    expect((deps.notifyError as ReturnType<typeof vi.fn>).mock.calls[0][0]).toContain(
      "No vault",
    );
    expect(deps.notifySuccess).not.toHaveBeenCalled();
  });

  it("calls notifyError once when vault permission revoked", async () => {
    const deps = makeDeps({ verifyHandle: vi.fn().mockResolvedValue(false) });
    await quickClip(1, deps);

    expect(deps.notifyError).toHaveBeenCalledOnce();
    expect(deps.notifySuccess).not.toHaveBeenCalled();
  });

  it("calls notifyError once when sendExtract throws", async () => {
    const deps = makeDeps({
      sendExtract: vi.fn().mockRejectedValue(new Error("no script")),
    });
    await quickClip(1, deps);

    expect(deps.notifyError).toHaveBeenCalledOnce();
    expect(deps.notifySuccess).not.toHaveBeenCalled();
  });

  it("calls notifyError once when extraction response is not ok", async () => {
    const deps = makeDeps({
      sendExtract: vi.fn().mockResolvedValue({ ok: false, error: "parse error" }),
    });
    await quickClip(1, deps);

    expect(deps.notifyError).toHaveBeenCalledOnce();
    expect(deps.notifySuccess).not.toHaveBeenCalled();
  });

  it("calls notifyError once when writePageToVault throws", async () => {
    const deps = makeDeps({
      writePageToVault: vi.fn().mockRejectedValue(new Error("disk full")),
    });
    await quickClip(1, deps);

    expect(deps.notifyError).toHaveBeenCalledOnce();
    expect(deps.notifySuccess).not.toHaveBeenCalled();
  });

  it("does not call writePageToVault when sendExtract rejects", async () => {
    const deps = makeDeps({
      sendExtract: vi.fn().mockRejectedValue(new Error("boom")),
    });
    await quickClip(1, deps);

    expect(deps.writePageToVault).not.toHaveBeenCalled();
  });

  it("threads selectionOnly + extractionMethod 'selection' to frontmatter when selection clipped", async () => {
    const writeSpy = vi.fn().mockResolvedValue(FAKE_RESULT);
    const deps = makeDeps({
      sendExtract: vi.fn().mockResolvedValue({ ok: true, page: FAKE_PAGE, selectionOnly: true }),
      writePageToVault: writeSpy,
    });
    await quickClip(1, deps);

    const opts = writeSpy.mock.calls[0][2] as { frontmatterOpts?: { selectionOnly?: boolean; extractionMethod?: string } };
    expect(opts.frontmatterOpts?.selectionOnly).toBe(true);
    expect(opts.frontmatterOpts?.extractionMethod).toBe("selection");
  });

  it("threads accumulated highlights into write and clears them after success", async () => {
    const writeSpy = vi.fn().mockResolvedValue(FAKE_RESULT);
    const highlights = [{ text: "passage one" }, { text: "passage two" }];
    const clearSpy = vi.fn().mockResolvedValue(undefined);
    const deps = makeDeps({
      getHighlights: vi.fn().mockResolvedValue(highlights),
      clearHighlights: clearSpy,
      writePageToVault: writeSpy,
    });
    await quickClip(42, deps);

    const opts = writeSpy.mock.calls[0][2] as { highlights?: typeof highlights };
    expect(opts.highlights).toEqual(highlights);
    expect(clearSpy).toHaveBeenCalledWith(42);
  });

  it("does not clear highlights when write fails", async () => {
    const clearSpy = vi.fn();
    const deps = makeDeps({
      getHighlights: vi.fn().mockResolvedValue([{ text: "x" }]),
      clearHighlights: clearSpy,
      writePageToVault: vi.fn().mockRejectedValue(new Error("disk full")),
    });
    await quickClip(1, deps);

    expect(clearSpy).not.toHaveBeenCalled();
    expect(deps.notifyError).toHaveBeenCalledOnce();
  });
});
