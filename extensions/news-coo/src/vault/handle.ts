// IndexedDB persistence for the vault FileSystemDirectoryHandle (PRD D3).

const DB_NAME = "news-coo";
const STORE_NAME = "vault";
const HANDLE_KEY = "directory-handle";

// Chrome-specific FSA permission API (not yet in standard TS DOM lib).
interface FSAPermissionHandle extends FileSystemDirectoryHandle {
  queryPermission(options?: { mode: "read" | "readwrite" }): Promise<PermissionState>;
  requestPermission(options?: { mode: "read" | "readwrite" }): Promise<PermissionState>;
}

async function openDb(): Promise<IDBDatabase> {
  return new Promise<IDBDatabase>((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, 1);
    req.onupgradeneeded = () => {
      req.result.createObjectStore(STORE_NAME);
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error ?? new Error("IDB open failed"));
  });
}

export async function saveHandle(
  handle: FileSystemDirectoryHandle,
): Promise<void> {
  const db = await openDb();
  return new Promise<void>((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, "readwrite");
    tx.objectStore(STORE_NAME).put(handle, HANDLE_KEY);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error ?? new Error("IDB write failed"));
  });
}

export async function loadHandle(): Promise<FileSystemDirectoryHandle | null> {
  const db = await openDb();
  return new Promise<FileSystemDirectoryHandle | null>((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, "readonly");
    const req = tx.objectStore(STORE_NAME).get(HANDLE_KEY);
    req.onsuccess = () =>
      resolve((req.result as FileSystemDirectoryHandle | undefined) ?? null);
    req.onerror = () => reject(req.error ?? new Error("IDB read failed"));
  });
}

export async function verifyHandle(
  handle: FileSystemDirectoryHandle,
): Promise<boolean> {
  try {
    const h = handle as FSAPermissionHandle;
    const state = await h.queryPermission({ mode: "readwrite" });
    if (state === "granted") return true;
    const requested = await h.requestPermission({ mode: "readwrite" });
    return requested === "granted";
  } catch {
    return false;
  }
}
