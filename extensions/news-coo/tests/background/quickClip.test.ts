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
});
