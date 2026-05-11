import { describe, it, expect, vi, beforeEach } from "vitest";
import { notifySuccess, notifyError } from "../../src/shared/notify.js";

function setupChrome(): ReturnType<typeof vi.fn> {
  const create = vi.fn();
  (globalThis as Record<string, unknown>).chrome = {
    notifications: { create },
  };
  return create;
}

beforeEach(() => {
  // Reset navigator.language to English so t() returns English strings.
  Object.defineProperty(navigator, "language", { value: "en-US", configurable: true });
});

describe("notifySuccess", () => {
  it("calls chrome.notifications.create once", () => {
    const create = setupChrome();
    notifySuccess("my-slug");
    expect(create).toHaveBeenCalledOnce();
  });

  it("creates a basic notification type", () => {
    const create = setupChrome();
    notifySuccess("my-slug");
    const opts = create.mock.calls[0][1] as { type: string };
    expect(opts.type).toBe("basic");
  });

  it("includes slug path in message", () => {
    const create = setupChrome();
    notifySuccess("my-slug");
    const opts = create.mock.calls[0][1] as { message: string };
    expect(opts.message).toBe("Inbox/kb/my-slug.md");
  });

  it("uses localised title (English)", () => {
    const create = setupChrome();
    notifySuccess("s");
    const opts = create.mock.calls[0][1] as { title: string };
    expect(opts.title).toBe("News Coo — Saved");
  });
});

describe("notifyError", () => {
  it("calls chrome.notifications.create once", () => {
    const create = setupChrome();
    notifyError("something broke");
    expect(create).toHaveBeenCalledOnce();
  });

  it("passes the error message through", () => {
    const create = setupChrome();
    notifyError("disk full");
    const opts = create.mock.calls[0][1] as { message: string };
    expect(opts.message).toBe("disk full");
  });

  it("uses localised error title (English)", () => {
    const create = setupChrome();
    notifyError("x");
    const opts = create.mock.calls[0][1] as { title: string };
    expect(opts.title).toBe("News Coo — Error");
  });
});
