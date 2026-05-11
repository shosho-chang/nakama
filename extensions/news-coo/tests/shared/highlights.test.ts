import { describe, it, expect, vi, beforeEach } from "vitest";
import { getHighlights, pushHighlight, clearHighlights } from "../../src/shared/highlights.js";

// In-memory store that mirrors chrome.storage.session behaviour.
let store: Record<string, unknown> = {};

function setupChrome(): void {
  (globalThis as Record<string, unknown>).chrome = {
    storage: {
      session: {
        get: vi.fn(async (k: string) => {
          return { [k]: store[k] };
        }),
        set: vi.fn(async (obj: Record<string, unknown>) => {
          Object.assign(store, obj);
        }),
        remove: vi.fn(async (k: string) => {
          delete store[k];
        }),
      },
    },
  };
}

beforeEach(() => {
  store = {};
  setupChrome();
});

describe("getHighlights", () => {
  it("returns empty array when no highlights stored", async () => {
    const result = await getHighlights(1);
    expect(result).toEqual([]);
  });

  it("returns stored highlights for the given tabId", async () => {
    store["highlights-42"] = [{ text: "passage one" }];
    const result = await getHighlights(42);
    expect(result).toEqual([{ text: "passage one" }]);
  });

  it("isolates highlights by tabId", async () => {
    store["highlights-1"] = [{ text: "tab 1" }];
    store["highlights-2"] = [{ text: "tab 2" }];
    expect(await getHighlights(1)).toEqual([{ text: "tab 1" }]);
    expect(await getHighlights(2)).toEqual([{ text: "tab 2" }]);
  });
});

describe("pushHighlight", () => {
  it("adds highlight to an empty list", async () => {
    await pushHighlight(1, { text: "first" });
    const result = await getHighlights(1);
    expect(result).toEqual([{ text: "first" }]);
  });

  it("appends highlight to existing list", async () => {
    await pushHighlight(1, { text: "first" });
    await pushHighlight(1, { text: "second" });
    const result = await getHighlights(1);
    expect(result).toEqual([{ text: "first" }, { text: "second" }]);
  });

  it("does not affect other tabs", async () => {
    await pushHighlight(1, { text: "tab 1 highlight" });
    expect(await getHighlights(2)).toEqual([]);
  });
});

describe("clearHighlights", () => {
  it("removes stored highlights for the given tabId", async () => {
    store["highlights-5"] = [{ text: "to be cleared" }];
    await clearHighlights(5);
    expect(await getHighlights(5)).toEqual([]);
  });

  it("does not throw when no highlights exist", async () => {
    await expect(clearHighlights(99)).resolves.toBeUndefined();
  });

  it("does not affect other tabs", async () => {
    store["highlights-1"] = [{ text: "keep me" }];
    store["highlights-2"] = [{ text: "clear me" }];
    await clearHighlights(2);
    expect(await getHighlights(1)).toEqual([{ text: "keep me" }]);
  });
});
