import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { loadHandle, saveHandle, verifyHandle } from "../../src/vault/handle.js";

// Minimal in-memory IndexedDB mock.
function createFakeIDB() {
  const stores: Record<string, Map<IDBValidKey, unknown>> = { vault: new Map() };
  let opened = false;

  function makeOpenReq() {
    const req = {
      result: null as unknown as IDBDatabase,
      error: null as null,
      onupgradeneeded: null as ((ev: Event) => void) | null,
      onsuccess: null as ((ev: Event) => void) | null,
      onerror: null as null,
    };

    const db: IDBDatabase = {
      createObjectStore: (name: string) => {
        stores[name] = new Map();
        return {} as IDBObjectStore;
      },
      transaction: (storeName: string, _mode: string): IDBTransaction => {
        const storeMap = stores[storeName] ?? new Map();
        let oncompleteCb: (() => void) | null = null;
        const tx = {
          get oncomplete() { return oncompleteCb; },
          set oncomplete(v) { oncompleteCb = v; },
          onerror: null,
          objectStore: (_name: string): IDBObjectStore => ({
            put: (value: unknown, key: IDBValidKey): IDBRequest => {
              storeMap.set(key, value);
              queueMicrotask(() => oncompleteCb?.());
              return { result: undefined, error: null, onsuccess: null, onerror: null } as unknown as IDBRequest;
            },
            get: (key: IDBValidKey): IDBRequest => {
              const r = {
                result: storeMap.get(key) ?? null as unknown,
                error: null,
                onsuccess: null as ((ev: Event) => void) | null,
                onerror: null,
              };
              queueMicrotask(() => r.onsuccess?.({} as Event));
              return r as unknown as IDBRequest;
            },
          } as unknown as IDBObjectStore),
        } as unknown as IDBTransaction;
        return tx;
      },
    } as unknown as IDBDatabase;

    req.result = db;
    queueMicrotask(() => {
      if (!opened) {
        opened = true;
        req.onupgradeneeded?.({} as Event);
      }
      req.onsuccess?.({} as Event);
    });

    return req as unknown as IDBOpenDBRequest;
  }

  return { open: (_name: string, _v: number) => makeOpenReq() } as unknown as IDBFactory;
}

describe("handle storage", () => {
  beforeEach(() => {
    vi.stubGlobal("indexedDB", createFakeIDB());
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("loadHandle returns null when nothing stored", async () => {
    const h = await loadHandle();
    expect(h).toBeNull();
  });

  it("saveHandle then loadHandle round-trips the handle", async () => {
    const fakeHandle = { name: "MyVault" } as unknown as FileSystemDirectoryHandle;
    await saveHandle(fakeHandle);
    const loaded = await loadHandle();
    expect(loaded).toBe(fakeHandle);
  });
});

describe("verifyHandle", () => {
  it("returns true when queryPermission reports granted", async () => {
    const handle = {
      queryPermission: vi.fn().mockResolvedValue("granted"),
      requestPermission: vi.fn(),
    } as unknown as FileSystemDirectoryHandle;

    expect(await verifyHandle(handle)).toBe(true);
  });

  it("requests permission when queryPermission reports prompt and returns true on grant", async () => {
    const handle = {
      queryPermission: vi.fn().mockResolvedValue("prompt"),
      requestPermission: vi.fn().mockResolvedValue("granted"),
    } as unknown as FileSystemDirectoryHandle;

    expect(await verifyHandle(handle)).toBe(true);
  });

  it("returns false when permission denied after request", async () => {
    const handle = {
      queryPermission: vi.fn().mockResolvedValue("prompt"),
      requestPermission: vi.fn().mockResolvedValue("denied"),
    } as unknown as FileSystemDirectoryHandle;

    expect(await verifyHandle(handle)).toBe(false);
  });

  it("returns false when handle throws", async () => {
    const handle = {
      queryPermission: vi.fn().mockRejectedValue(new Error("revoked")),
    } as unknown as FileSystemDirectoryHandle;

    expect(await verifyHandle(handle)).toBe(false);
  });
});
